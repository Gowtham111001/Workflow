"""Settings (how the pipeline behaves) and ApplicantProfile (what to autofill).

Both live as YAML under the data directory (default ./data, git-ignored).
`jobpilot init` copies the examples from ./config into place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field

from .models import MasterResume

DEFAULT_DATA_DIR = Path("data")


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


class LLMSettings(BaseModel):
    model: str = "claude-opus-5-5"
    # Effort per stage: analysis/scoring is routine, tailoring is where quality matters.
    effort_analyze: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    effort_tailor: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    effort_answer: Literal["low", "medium", "high", "xhigh", "max"] = "medium"


class SearchSettings(BaseModel):
    # A posting must match at least one of these (case-insensitive substring of title).
    title_keywords: list[str] = Field(default_factory=list)
    # ...and none of these.
    exclude_title_keywords: list[str] = Field(default_factory=list)
    # Empty = anywhere. Matched as substrings of the posting's location.
    locations: list[str] = Field(default_factory=list)
    remote_ok: bool = True
    max_age_days: Optional[int] = 30
    min_score: int = 65  # fit score needed before we spend tokens tailoring


class SourceSettings(BaseModel):
    # Company board slugs, e.g. boards-api.greenhouse.io/v1/boards/<slug>/jobs
    greenhouse: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)
    ashby: list[str] = Field(default_factory=list)


class TailorSettings(BaseModel):
    max_bullets_per_role: int = 5
    max_projects: int = 2
    paper: Literal["Letter", "A4"] = "Letter"


class ApplySettings(BaseModel):
    # review:   stop after tailoring; you approve each job, then run `apply`
    # assisted: fill the form in a visible browser, you click Submit (recommended)
    # auto:     submit without you, only when every field was answered confidently
    mode: Literal["review", "assisted", "auto"] = "assisted"
    # If false, tailored jobs at/above min_score are approved without `jobpilot review`.
    require_review: bool = True
    daily_limit: int = 15
    per_company_limit: int = 3  # across all time; avoids spamming one recruiter
    headless: bool = False


class Settings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    sources: SourceSettings = Field(default_factory=SourceSettings)
    tailor: TailorSettings = Field(default_factory=TailorSettings)
    apply: ApplySettings = Field(default_factory=ApplySettings)


# --------------------------------------------------------------------------
# Applicant profile (autofill)
# --------------------------------------------------------------------------


class Address(BaseModel):
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None


class Personal(BaseModel):
    first_name: str
    last_name: str
    preferred_name: Optional[str] = None
    email: str
    phone: Optional[str] = None
    address: Address = Field(default_factory=Address)
    pronouns: Optional[str] = None


class Links(BaseModel):
    linkedin: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    website: Optional[str] = None


class WorkAuthorization(BaseModel):
    authorized_countries: list[str] = Field(default_factory=list)
    requires_sponsorship: Optional[bool] = None
    status: Optional[str] = None  # e.g. "Citizen", "H-1B", "F-1 OPT"


class Preferences(BaseModel):
    desired_salary: Optional[str] = None
    notice_period: Optional[str] = None
    earliest_start_date: Optional[str] = None
    willing_to_relocate: Optional[bool] = None
    years_of_experience: Optional[int] = None


class Demographics(BaseModel):
    """Voluntary self-identification. Defaults to declining; only filled if you set it."""

    gender: str = "Decline to self-identify"
    race_ethnicity: str = "Decline to self-identify"
    veteran_status: str = "I don't wish to answer"
    disability_status: str = "I don't wish to answer"
    hispanic_latino: str = "Decline to self-identify"


class ApplicantProfile(BaseModel):
    personal: Personal
    links: Links = Field(default_factory=Links)
    work_authorization: WorkAuthorization = Field(default_factory=WorkAuthorization)
    preferences: Preferences = Field(default_factory=Preferences)
    demographics: Demographics = Field(default_factory=Demographics)
    # Your answer bank: question text (or a distinctive fragment) -> answer.
    # Grows over time: every question the pipeline couldn't answer lands in
    # data/pending_questions.yaml for you to answer once and move here.
    answers: dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


class Workspace:
    """Paths + lazy loaders for everything under the data directory."""

    def __init__(self, data_dir: Path | str = DEFAULT_DATA_DIR):
        self.root = Path(data_dir)

    settings_path = property(lambda self: self.root / "settings.yaml")
    profile_path = property(lambda self: self.root / "profile.yaml")
    resume_path = property(lambda self: self.root / "master_resume.yaml")
    db_path = property(lambda self: self.root / "jobpilot.db")
    pending_questions_path = property(lambda self: self.root / "pending_questions.yaml")

    def job_dir(self, job_id: str) -> Path:
        d = self.root / "applications" / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def settings(self) -> Settings:
        return Settings.model_validate(_read_yaml(self.settings_path) or {})

    def profile(self) -> ApplicantProfile:
        return ApplicantProfile.model_validate(_read_yaml(self.profile_path, required=True))

    def resume(self) -> MasterResume:
        data = _read_yaml(self.resume_path, required=True, hint="run `jobpilot ingest <resume file>` first")
        return MasterResume.model_validate(data).assign_ids()

    def save_resume(self, resume: MasterResume) -> None:
        write_yaml(self.resume_path, resume.model_dump(exclude_none=True))


def _read_yaml(path: Path, required: bool = False, hint: str = "run `jobpilot init` first"):
    if not path.exists():
        if required:
            raise FileNotFoundError(f"{path} not found; {hint}")
        return None
    with path.open() as f:
        return yaml.safe_load(f)


def write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=100)
