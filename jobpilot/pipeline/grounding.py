"""Mechanical truthfulness checks for tailored output.

The tailoring prompt asks Claude not to invent anything; this module doesn't
take that on trust. Every tailored bullet cites master bullets, and we check:

1. citations exist and belong to the section they appear in,
2. every number in a bullet appears in the bullets it cites (no "40%" -> "60%"),
3. no job-posting keyword appears in a bullet unless the cited bullets or
   your skills list already contain it (no keyword-stuffing skills you lack),
4. every listed skill is one you have evidence for,
5. every number in the cover letter appears somewhere in your resume.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import JobAnalysis, MasterResume, TailoredResume

NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUMBER.findall(text)}


def contains_term(text: str, term: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(term.lower())}(?![\w])", text.lower()) is not None


@dataclass
class Violation:
    where: str  # e.g. "e1 bullet 2", "skills", "cover_letter"
    problem: str
    bullet_index: tuple[str, int] | None = None  # (section id, bullet idx) for auto-repair


@dataclass
class GroundingReport:
    violations: list[Violation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_feedback(self) -> str:
        return "\n".join(f"- {v.where}: {v.problem}" for v in self.violations)


def check(tailored: TailoredResume, master: MasterResume, analysis: JobAnalysis | None,
          extra_numbers: set[str] = frozenset()) -> GroundingReport:
    report = GroundingReport()
    bullets = master.bullet_index()
    sections = {s.id: s for s in (*master.experience, *master.projects)}
    master_skills = master.all_skills()
    keywords = set()
    if analysis:
        keywords = {k.strip() for k in (*analysis.must_have_skills, *analysis.nice_to_have_skills,
                                        *analysis.ats_keywords) if k.strip()}

    for group_name, group in (("experience", tailored.experience), ("projects", tailored.projects)):
        for section in group:
            master_section = sections.get(section.source_id)
            if master_section is None:
                report.violations.append(Violation(f"{group_name}", f"unknown section id {section.source_id!r}"))
                continue
            own_ids = {b.id for b in master_section.bullets}
            for i, tb in enumerate(section.bullets):
                where = f"{section.source_id} bullet {i + 1}"
                loc = (section.source_id, i)
                bad = [sid for sid in tb.source_ids if sid not in own_ids]
                if not tb.source_ids or bad:
                    report.violations.append(Violation(where, f"cites {bad or 'nothing'}; must cite bullets of "
                                                              f"{section.source_id}: {sorted(own_ids)}", loc))
                    continue
                source_text = " ".join(bullets[sid].text for sid in tb.source_ids)
                source_tags = {s.lower() for sid in tb.source_ids for s in bullets[sid].skills}
                invented = numbers(tb.text) - numbers(source_text)
                if invented:
                    report.violations.append(Violation(where, f"numbers {sorted(invented)} not in cited bullets", loc))
                for kw in keywords:
                    if (contains_term(tb.text, kw) and not contains_term(source_text, kw)
                            and kw.lower() not in source_tags and kw.lower() not in master_skills):
                        report.violations.append(
                            Violation(where, f"mentions {kw!r}, which nothing in your resume evidences", loc))

    for g in tailored.skills:
        for s in g.items:
            if s.strip().lower() not in master_skills:
                report.violations.append(Violation("skills", f"{s!r} is not in your master resume"))

    master_numbers = set(extra_numbers)
    for b in bullets.values():
        master_numbers |= numbers(b.text)
    for text in (master.summary or "", *(d for e in master.education for d in e.details)):
        master_numbers |= numbers(text)
    invented = numbers(tailored.cover_letter) - master_numbers
    if invented:
        report.violations.append(Violation("cover_letter", f"numbers {sorted(invented)} not found in your resume"))

    covered = {s.source_id for s in tailored.experience}
    for exp in master.experience:
        if exp.id not in covered:
            report.warnings.append(f"{exp.company} ({exp.id}) was dropped; it will be re-added with original bullets")
    return report


def repair(tailored: TailoredResume, master: MasterResume, report: GroundingReport) -> TailoredResume:
    """Last resort after a failed retry: replace offending bullets with the
    original text they cite, drop unevidenced skills, re-add dropped roles."""
    from ..models import TailoredBullet, TailoredSection

    bullets = master.bullet_index()
    sections = {s.id: s for s in (*master.experience, *master.projects)}
    bad_bullets = {v.bullet_index for v in report.violations if v.bullet_index}
    master_skills = master.all_skills()

    def fix_group(group: list[TailoredSection]) -> list[TailoredSection]:
        out = []
        for section in group:
            if section.source_id not in sections:
                continue
            own = {b.id for b in sections[section.source_id].bullets}
            new_bullets = []
            for i, tb in enumerate(section.bullets):
                if (section.source_id, i) not in bad_bullets:
                    new_bullets.append(tb)
                    continue
                for sid in tb.source_ids:  # fall back to the verbatim originals
                    if sid in own and all(sid not in nb.source_ids for nb in new_bullets):
                        new_bullets.append(TailoredBullet(source_ids=[sid], text=bullets[sid].text))
            out.append(section.model_copy(update={"bullets": new_bullets}))
        return out

    experience = fix_group(tailored.experience)
    covered = {s.source_id for s in experience}
    for exp in master.experience:
        if exp.id not in covered:
            experience.append(TailoredSection(
                source_id=exp.id,
                bullets=[TailoredBullet(source_ids=[b.id], text=b.text) for b in exp.bullets[:2]],
            ))
    skills = [g.model_copy(update={"items": [s for s in g.items if s.strip().lower() in master_skills]})
              for g in tailored.skills]
    return tailored.model_copy(update={
        "experience": experience,
        "projects": fix_group(tailored.projects),
        "skills": [g for g in skills if g.items],
    })
