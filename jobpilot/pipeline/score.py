"""Stage 3: analyze the posting and score your fit, in one LLM call.

Deterministic guardrails then override the model where the answer is a
policy, not a judgment (e.g. no sponsorship offered but you need it).
"""

from __future__ import annotations

import yaml
from pydantic import BaseModel

from ..config import ApplicantProfile
from ..llm import StructuredLLM
from ..models import FitAssessment, Job, JobAnalysis, MasterResume


class JobScoring(BaseModel):
    analysis: JobAnalysis
    fit: FitAssessment


SYSTEM = """You are a candid technical recruiter screening one candidate against job postings.

For each posting, first extract a structured analysis of the job, then assess fit.
Scoring guide (probability the candidate gets a first-round interview):
- 80-100: meets essentially all must-haves with direct evidence, seniority matches.
- 60-79: meets most must-haves; gaps are learnable or adjacent experience exists.
- 40-59: meaningful gaps in must-haves or a seniority mismatch.
- 0-39: clearly not a fit.
Only count a requirement as matched if the resume contains evidence for it. Be honest about gaps:
an inflated score wastes the candidate's time and the employer's.

Candidate resume (YAML):
<resume>
{resume}
</resume>

Candidate constraints:
{constraints}"""


def build_system(resume: MasterResume, profile: ApplicantProfile) -> str:
    auth = profile.work_authorization
    constraints = [
        f"- Authorized to work in: {', '.join(auth.authorized_countries) or 'unspecified'}",
        f"- Requires visa sponsorship: {auth.requires_sponsorship if auth.requires_sponsorship is not None else 'unspecified'}",
    ]
    if profile.preferences.years_of_experience is not None:
        constraints.append(f"- Years of professional experience: {profile.preferences.years_of_experience}")
    return SYSTEM.format(
        resume=yaml.safe_dump(resume.model_dump(exclude_none=True), sort_keys=False),
        constraints="\n".join(constraints),
    )


def job_prompt(job: Job) -> str:
    return (
        f"<job>\nCompany: {job.company}\nTitle: {job.title}\nLocation: {job.location}\n"
        f"Salary: {job.salary or 'not listed'}\n\n{job.description}\n</job>"
    )


def apply_guardrails(scoring: JobScoring, profile: ApplicantProfile) -> JobScoring:
    fit = scoring.fit
    if profile.work_authorization.requires_sponsorship and scoring.analysis.visa_sponsorship == "no":
        fit = fit.model_copy(update={
            "score": 0,
            "verdict": "weak",
            "gaps": [*fit.gaps, "Posting says no visa sponsorship; you require it"],
        })
    fit = fit.model_copy(update={"score": max(0, min(100, fit.score))})
    return JobScoring(analysis=scoring.analysis, fit=fit)


def score_job(job: Job, system: str, profile: ApplicantProfile, llm: StructuredLLM, effort: str = "low") -> JobScoring:
    scoring = llm.parse(system=system, prompt=job_prompt(job), schema=JobScoring, effort=effort)
    return apply_guardrails(scoring, profile)
