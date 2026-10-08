"""Data models shared by every stage of the pipeline.

Three families:
- MasterResume: the single source of truth about you, parsed once from your
  resume and then reviewed/edited by hand. Every bullet has a stable id.
- Job / JobAnalysis / FitAssessment: what we know about a posting.
- TailoredResume: a per-job *view* of the master resume. It may select,
  reorder and rephrase, but every bullet must cite the master bullets it came
  from (source_ids) so grounding can be checked mechanically.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Master resume
# --------------------------------------------------------------------------


class Link(BaseModel):
    label: str
    url: str


class Bullet(BaseModel):
    id: str = ""
    text: str
    skills: list[str] = Field(default_factory=list, description="Skills/tools evidenced by this bullet")


class Experience(BaseModel):
    id: str = ""
    company: str
    title: str
    location: Optional[str] = None
    start: Optional[str] = Field(default=None, description="e.g. 2021-06 or Jun 2021")
    end: Optional[str] = Field(default=None, description="e.g. 2023-01, or Present")
    bullets: list[Bullet] = Field(default_factory=list)


class Project(BaseModel):
    id: str = ""
    name: str
    link: Optional[str] = None
    bullets: list[Bullet] = Field(default_factory=list)


class Education(BaseModel):
    institution: str
    degree: Optional[str] = None
    field: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    details: list[str] = Field(default_factory=list)


class SkillGroup(BaseModel):
    category: str
    items: list[str]


class Basics(BaseModel):
    name: str
    headline: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    links: list[Link] = Field(default_factory=list)


class MasterResume(BaseModel):
    basics: Basics
    summary: Optional[str] = None
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)

    def assign_ids(self) -> "MasterResume":
        """Give every experience/project/bullet a stable id (e1, e1.b2, p1.b1...).

        Existing ids are kept so hand edits to the YAML don't reshuffle them.
        """
        for i, exp in enumerate(self.experience, 1):
            exp.id = exp.id or f"e{i}"
            for j, b in enumerate(exp.bullets, 1):
                b.id = b.id or f"{exp.id}.b{j}"
        for i, proj in enumerate(self.projects, 1):
            proj.id = proj.id or f"p{i}"
            for j, b in enumerate(proj.bullets, 1):
                b.id = b.id or f"{proj.id}.b{j}"
        return self

    def bullet_index(self) -> dict[str, Bullet]:
        out: dict[str, Bullet] = {}
        for section in (*self.experience, *self.projects):
            for b in section.bullets:
                out[b.id] = b
        return out

    def all_skills(self) -> set[str]:
        """Every skill you have evidence for: the skills section plus bullet tags."""
        skills = {s.strip().lower() for g in self.skills for s in g.items}
        for b in self.bullet_index().values():
            skills |= {s.strip().lower() for s in b.skills}
        return skills


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


class JobStatus(str, Enum):
    DISCOVERED = "discovered"
    FILTERED_OUT = "filtered_out"
    SCORED = "scored"
    SKIPPED = "skipped"  # scored below threshold, or you said no
    TAILORED = "tailored"
    APPROVED = "approved"
    NEEDS_INPUT = "needs_input"  # form asked something we can't answer safely
    READY_TO_SUBMIT = "ready_to_submit"  # form filled, waiting for your click
    APPLIED = "applied"
    FAILED = "failed"
    # Post-application lifecycle, updated by you (or a Gmail watcher later)
    INTERVIEWING = "interviewing"
    REJECTED = "rejected"
    OFFER = "offer"


class Job(BaseModel):
    id: str  # stable hash of source + external id
    source: str  # greenhouse | lever | ashby | manual
    company: str
    title: str
    location: str = ""
    remote: Optional[bool] = None
    url: str
    apply_url: Optional[str] = None
    description: str = ""
    posted_at: Optional[str] = None
    salary: Optional[str] = None


Seniority = Literal["intern", "entry", "mid", "senior", "staff", "principal", "manager", "director", "unknown"]


class JobAnalysis(BaseModel):
    seniority: Seniority
    years_experience_min: Optional[int] = None
    must_have_skills: list[str]
    nice_to_have_skills: list[str]
    responsibilities: list[str]
    ats_keywords: list[str] = Field(description="Exact phrases an ATS keyword filter would likely look for")
    work_mode: Literal["remote", "hybrid", "onsite", "unknown"]
    visa_sponsorship: Literal["yes", "no", "unknown"]
    salary_range: Optional[str] = None
    red_flags: list[str] = Field(description="Things worth a second look, e.g. unpaid trial work, vague role")


class FitAssessment(BaseModel):
    score: int = Field(description="0-100: how likely this candidate gets an interview")
    verdict: Literal["strong", "possible", "weak"]
    matched_requirements: list[str]
    gaps: list[str] = Field(description="Requirements the candidate has no evidence for")
    rationale: str


# --------------------------------------------------------------------------
# Tailored output
# --------------------------------------------------------------------------


class TailoredBullet(BaseModel):
    source_ids: list[str] = Field(description="Ids of the master bullets this bullet is derived from")
    text: str


class TailoredSection(BaseModel):
    source_id: str = Field(description="Id of the master experience (e1...) or project (p1...)")
    bullets: list[TailoredBullet]


class TailoredResume(BaseModel):
    headline: str
    summary: str
    experience: list[TailoredSection]
    projects: list[TailoredSection]
    skills: list[SkillGroup]
    cover_letter: str
    change_notes: list[str] = Field(description="What was emphasized/reworded and why, for the human reviewer")
