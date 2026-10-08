"""Stage 4: tailor the resume + write a cover letter for one job.

Flow: generate -> grounding check -> (one retry with the violations as
feedback) -> mechanical repair if still failing -> render PDFs + review notes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..config import ApplicantProfile, TailorSettings
from ..llm import StructuredLLM
from ..models import FitAssessment, Job, JobAnalysis, MasterResume, TailoredResume
from . import grounding

SYSTEM = """You tailor a candidate's resume and write a cover letter for a specific job.

You may ONLY use facts from the master resume below. You may:
- choose which bullets to include and in what order (most relevant first),
- rephrase a bullet to lead with the outcome and to use the posting's terminology
  *where it truthfully describes the same thing* (e.g. "made the API faster" ->
  "optimized REST API latency" if the bullet is about a REST API),
- merge two bullets from the same role into one.
You may NOT:
- add tools, skills, responsibilities, scope or seniority the source bullets don't show,
- change, round or invent any number,
- change companies, titles or dates (those are taken from the master resume anyway).

Every output bullet must list in `source_ids` the master bullet ids it was derived from;
bullets may only cite bullets of the same role/project (`source_id`).
Include every experience entry (fewer bullets for less relevant ones, at most {max_bullets} each).
Include at most {max_projects} projects, only if relevant.
`skills`: only skills from the master resume, most relevant groups/items first; omit irrelevant ones.
`headline`: a one-line professional title aligned with the role, truthful to the candidate's experience.
`summary`: 2-3 sentences, specific, no clichés ("passionate", "results-driven", "team player").
`cover_letter`: 200-320 words, plain paragraphs, no placeholder brackets. Open with why this role
at this company (use specifics from the posting), connect 2-3 concrete achievements to their needs,
close briefly. Sign with the candidate's name. Do not mention gaps or apologize.
`change_notes`: short notes for the candidate on what you emphasized and why.

Master resume (YAML, with ids):
<resume>
{resume}
</resume>"""


@dataclass
class TailorResult:
    tailored: TailoredResume
    report: grounding.GroundingReport
    attempts: int
    repaired: bool


def build_system(master: MasterResume, settings: TailorSettings) -> str:
    return SYSTEM.format(
        resume=yaml.safe_dump(master.model_dump(exclude_none=True), sort_keys=False),
        max_bullets=settings.max_bullets_per_role,
        max_projects=settings.max_projects,
    )


def job_prompt(job: Job, analysis: JobAnalysis | None, fit: FitAssessment | None) -> str:
    parts = [f"<job>\nCompany: {job.company}\nTitle: {job.title}\nLocation: {job.location}\n\n{job.description}\n</job>"]
    if analysis:
        parts.append(f"<job_analysis>\n{analysis.model_dump_json(indent=1)}\n</job_analysis>")
    if fit:
        parts.append(f"<fit_assessment>\n{fit.model_dump_json(indent=1)}\n</fit_assessment>")
    parts.append("Tailor the resume and write the cover letter for this job.")
    return "\n\n".join(parts)


def tailor(job: Job, master: MasterResume, profile: ApplicantProfile, analysis: JobAnalysis | None,
           fit: FitAssessment | None, llm: StructuredLLM, settings: TailorSettings, effort: str = "high") -> TailorResult:
    system = build_system(master, settings)
    prompt = job_prompt(job, analysis, fit)
    extra_numbers = {str(profile.preferences.years_of_experience)} if profile.preferences.years_of_experience else set()

    tailored = llm.parse(system=system, prompt=prompt, schema=TailoredResume, effort=effort)
    report = grounding.check(tailored, master, analysis, extra_numbers)
    attempts = 1
    if not report.ok:
        attempts = 2
        retry_prompt = (
            f"{prompt}\n\nA previous draft failed the fact check. Fix these problems; when a claim can't be "
            f"supported by the cited bullets, drop or soften it rather than re-citing:\n{report.as_feedback()}"
        )
        tailored = llm.parse(system=system, prompt=retry_prompt, schema=TailoredResume, effort=effort)
        report = grounding.check(tailored, master, analysis, extra_numbers)

    repaired = False
    if not report.ok or report.warnings:
        tailored = grounding.repair(tailored, master, report)
        repaired = True
        final = grounding.check(tailored, master, analysis, extra_numbers)
        final.warnings = report.warnings + final.warnings
        report = final
    return TailorResult(tailored, report, attempts, repaired)


def write_outputs(out_dir: Path, job: Job, result: TailorResult, fit: FitAssessment | None) -> Path:
    """Save tailored.json and a human-readable review.md; returns review.md path."""
    (out_dir / "tailored.json").write_text(result.tailored.model_dump_json(indent=2))
    (out_dir / "job.json").write_text(job.model_dump_json(indent=2))
    lines = [f"# {job.title} @ {job.company}", "", f"Posting: {job.url}", ""]
    if fit:
        lines += [f"**Fit: {fit.score}/100 ({fit.verdict})** {fit.rationale}", "",
                  "Matched: " + "; ".join(fit.matched_requirements), "", "Gaps: " + ("; ".join(fit.gaps) or "none"), ""]
    lines += ["## What changed", *[f"- {n}" for n in result.tailored.change_notes], ""]
    lines += ["## Fact check",
              "Passed." if result.report.ok else "**Needs your attention:**",
              *[f"- {v.where}: {v.problem}" for v in result.report.violations],
              *[f"- note: {w}" for w in result.report.warnings],
              f"- attempts: {result.attempts}, mechanically repaired: {result.repaired}", ""]
    lines += ["## Cover letter", "", result.tailored.cover_letter, ""]
    review = out_dir / "review.md"
    review.write_text("\n".join(lines))
    (out_dir / "cover_letter.txt").write_text(result.tailored.cover_letter)
    (out_dir / "grounding.json").write_text(json.dumps({
        "ok": result.report.ok,
        "violations": [v.__dict__ for v in result.report.violations],
        "warnings": result.report.warnings,
    }, indent=2, default=list))
    return review
