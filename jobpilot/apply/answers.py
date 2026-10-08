"""Decide what to put in each application form field.

Resolution order, most to least trusted:
  1. files        resume / cover letter uploads
  2. answer bank  your own answers in profile.yaml `answers:` (substring match on the label)
  3. profile      rule table mapping common labels to profile fields
  4. LLM          free-text and non-sensitive multiple-choice questions only,
                  drafted from your resume; always flagged for review
Sensitive questions (work authorization, sponsorship, legal attestations,
demographics, salary...) are NEVER guessed: if steps 1-3 have no answer,
the field is reported as missing and the question is queued for you.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml
from pydantic import BaseModel

from ..config import ApplicantProfile
from ..llm import StructuredLLM
from ..models import Job, MasterResume

SENSITIVE = re.compile(
    r"sponsor|visa|authori[sz]|eligib|legal|right to work|criminal|convict|felony|background check|"
    r"gender|sex\b|race|ethnic|veteran|disab|hispanic|latin|pronoun|sexual orientation|transgender|"
    r"agree|consent|certify|acknowledge|attest|terms|privacy|policy|signature|"
    r"salary|compensation|pay expectation|desired pay|relocat|clearance|drug|over 18|18 years|age\b|"
    r"previously (been )?(employed|worked)|non-compete|citizenship",
    re.I,
)
DECLINE = re.compile(r"decline|don.?t wish|do not wish|prefer not|not to (answer|say|disclose)|choose not", re.I)


@dataclass
class FormField:
    key: str
    kind: str  # text | email | tel | url | number | date | textarea | select | radio | checkbox | checkbox_group | file | combobox
    label: str
    required: bool = False
    options: list[str] = field(default_factory=list)
    option_keys: list[str] = field(default_factory=list)
    hint: str = ""  # the control's id/name, e.g. "resume" when the visible label is just "Attach"
    help: str = ""  # helper text under the field, e.g. "great answers are 200-400 words"


@dataclass
class Answer:
    value: str
    source: str  # file | bank | profile | llm
    needs_review: bool = False  # LLM-drafted: a human should glance at it before submit


def norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())


def yes_no(v: Optional[bool]) -> Optional[str]:
    return None if v is None else ("Yes" if v else "No")


def match_option(answer: str, options: list[str]) -> Optional[str]:
    """Map a free answer onto one of a field's options, or None if nothing fits."""
    if not options:
        return answer
    a = norm(answer)
    normed = [(o, norm(o)) for o in options if norm(o) and norm(o) not in ("select", "select an option", "please select")]
    for o, n in normed:
        if n == a:
            return o
    if DECLINE.search(answer):
        for o, _ in normed:
            if DECLINE.search(o):
                return o
    for o, n in normed:  # "Yes" -> "Yes, I am authorized..."
        if n.startswith(a + " ") or a.startswith(n + " "):
            return o
    if len(a) >= 4:
        hits = [o for o, n in normed if a in n or n in a]
        if len(hits) == 1:
            return hits[0]
    return None


def _location(p: ApplicantProfile) -> Optional[str]:
    a = p.personal.address
    parts = [x for x in (a.city, a.state, a.country) if x]
    return ", ".join(parts) or None


# (label pattern, getter). First match wins, so specific patterns come first.
RULES: list[tuple[re.Pattern, Callable[[ApplicantProfile], Optional[str]]]] = [
    (re.compile(p, re.I), g) for p, g in [
        # Specific questions first: their labels often contain generic words like "location".
        (r"sponsor", lambda p: yes_no(p.work_authorization.requires_sponsorship)),
        (r"relocat", lambda p: yes_no(p.preferences.willing_to_relocate)),
        (r"(salary|compensation|pay) (expectation|requirement)|desired (salary|pay|compensation)|expected (salary|compensation)",
         lambda p: p.preferences.desired_salary),
        (r"notice period", lambda p: p.preferences.notice_period),
        (r"start date|available to start|earliest .*start|when can you start", lambda p: p.preferences.earliest_start_date),
        # Total experience only: "years of experience with Python" is a different question.
        (r"years of (professional |work |industry |total )?experience(?! (with|in|using))",
         lambda p: str(p.preferences.years_of_experience) if p.preferences.years_of_experience is not None else None),
        (r"preferred (first )?name|nickname", lambda p: p.personal.preferred_name or p.personal.first_name),
        (r"first name|given name|forename", lambda p: p.personal.first_name),
        (r"last name|family name|surname", lambda p: p.personal.last_name),
        (r"^(full )?(legal )?name\b|your name", lambda p: f"{p.personal.first_name} {p.personal.last_name}"),
        (r"e-?mail", lambda p: p.personal.email),
        (r"phone|mobile|telephone", lambda p: p.personal.phone),
        (r"linkedin", lambda p: p.links.linkedin),
        (r"github", lambda p: p.links.github),
        (r"portfolio", lambda p: p.links.portfolio or p.links.website),
        (r"website|personal (site|url)|blog", lambda p: p.links.website or p.links.portfolio),
        (r"pronoun", lambda p: p.personal.pronouns),
        (r"street|address line|^address", lambda p: p.personal.address.street),
        (r"\bcity\b|\btown\b", lambda p: p.personal.address.city),
        (r"\bstate\b|province|\bregion\b", lambda p: p.personal.address.state),
        (r"zip|postal", lambda p: p.personal.address.postal_code),
        (r"country", lambda p: p.personal.address.country),
        (r"location|where (are you|do you) (based|live)", _location),
        (r"hispanic|latin", lambda p: p.demographics.hispanic_latino),
        (r"gender|\bsex\b", lambda p: p.demographics.gender),
        (r"\brace\b|ethnic", lambda p: p.demographics.race_ethnicity),
        (r"veteran", lambda p: p.demographics.veteran_status),
        (r"disab", lambda p: p.demographics.disability_status),
    ]
]

COUNTRY_ALIASES = {
    "united states": ["united states", "u.s.", "usa", "us"],
    "united kingdom": ["united kingdom", "uk", "u.k."],
    "india": ["india"],
    "canada": ["canada"],
}


class DraftAnswer(BaseModel):
    answer: str
    confident: bool


DRAFT_SYSTEM = """You fill in job application questions on behalf of the candidate, in their voice (first person).

Use only facts from the candidate's resume and profile below. Never invent experience, employers,
numbers or credentials. If the question asks for something the materials don't cover, return
confident=false and an empty answer.
- Free-text questions: answer directly and specifically, 40-150 words unless the question implies
  a one-liner. No clichés, no restating the question.
- When options are given, `answer` must be exactly one of the options.

<resume>
{resume}
</resume>
<profile>
{profile}
</profile>"""


class AnswerResolver:
    def __init__(self, profile: ApplicantProfile, resume: MasterResume, job: Job, files: dict[str, Path],
                 cover_letter: str, llm: Optional[StructuredLLM] = None, effort: str = "medium"):
        self.profile, self.job, self.files, self.cover_letter, self.llm, self.effort = (
            profile, job, files, cover_letter, llm, effort)
        safe_profile = profile.model_dump(exclude={"demographics", "answers"}, exclude_none=True)
        self.system = DRAFT_SYSTEM.format(
            resume=yaml.safe_dump(resume.model_dump(exclude_none=True), sort_keys=False),
            profile=yaml.safe_dump(safe_profile, sort_keys=False),
        )

    def resolve(self, f: FormField) -> Optional[Answer]:
        label = f.label.strip()
        n = norm(label)

        if f.kind == "file":
            n = f"{n} {norm(f.hint)}"
            if re.search(r"cover", n) and "cover_pdf" in self.files:
                return Answer(str(self.files["cover_pdf"]), "file")
            if re.search(r"resume|\bcv\b|curriculum", n) or f.required:
                return Answer(str(self.files["resume_pdf"]), "file")
            return None

        if re.search(r"cover letter", n) and f.kind == "textarea":
            return Answer(self.cover_letter, "file")

        for fragment, value in self.profile.answers.items():
            if norm(fragment) and norm(fragment) in n:
                return self._fit(f, value, "bank")

        if f.kind == "checkbox":
            return None  # single checkboxes are consents/opt-ins: only ticked via your answer bank

        if re.search(r"authori[sz]ed to work|legally (eligible|able|permitted) to work|right to work|work authori[sz]ation", n):
            return self._work_authorization(f, n)

        for pattern, getter in RULES:
            if pattern.search(label):
                value = getter(self.profile)
                if value is not None:
                    return self._fit(f, value, "profile")
                break  # recognised the question but you haven't set this field: don't let the LLM guess

        if SENSITIVE.search(label) or self.llm is None:
            return None
        return self._draft(f)

    def _fit(self, f: FormField, value: str, source: str) -> Optional[Answer]:
        if f.options:
            chosen = match_option(value, f.options)
            return Answer(chosen, source) if chosen else None
        return Answer(value, source)

    def _work_authorization(self, f: FormField, label: str) -> Optional[Answer]:
        haystack = f"{label} {norm(self.job.location)}"
        for country in self.profile.work_authorization.authorized_countries:
            aliases = COUNTRY_ALIASES.get(country.lower(), [country.lower()])
            if any(re.search(rf"\b{re.escape(norm(a))}\b", haystack) for a in aliases):
                return self._fit(f, "Yes", "profile")
        return None  # unknown country: ask rather than guess on a legal question

    def _draft(self, f: FormField) -> Optional[Answer]:
        prompt = (f"Job: {self.job.title} at {self.job.company}\n<job_description>\n{self.job.description}\n"
                  f"</job_description>\n\nQuestion: {f.label}\n")
        if f.help:
            prompt += f"Guidance shown under the question: {f.help}\n"
        if f.options:
            prompt += "Options:\n" + "\n".join(f"- {o}" for o in f.options)
        draft = self.llm.parse(system=self.system, prompt=prompt, schema=DraftAnswer, effort=self.effort)
        if not draft.confident or not draft.answer.strip():
            return None
        if f.options:
            chosen = match_option(draft.answer, f.options)
            return Answer(chosen, "llm", needs_review=True) if chosen else None
        return Answer(draft.answer.strip(), "llm", needs_review=True)


def queue_questions(path: Path, job: Job, fields: list[FormField]) -> None:
    """Append unanswered questions to pending_questions.yaml so you answer each once.

    Copy the answer into profile.yaml `answers:` (a distinctive fragment of the
    question as the key) and it is reused on every future application."""
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    for f in fields:
        entry = data.setdefault(f.label, {"answer": "", "options": f.options, "seen_in": []})
        where = f"{job.company} - {job.title}"
        if where not in entry["seen_in"]:
            entry["seen_in"].append(where)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100))
