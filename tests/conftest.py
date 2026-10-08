from __future__ import annotations

import pytest

from jobpilot.config import ApplicantProfile
from jobpilot.models import Job, JobAnalysis, MasterResume


class FakeLLM:
    """Returns queued responses in order; records every call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def parse(self, *, system, prompt, schema, effort="medium"):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema, "effort": effort})
        response = self.responses.pop(0)
        return response if isinstance(response, schema) else schema.model_validate(response)


@pytest.fixture
def master() -> MasterResume:
    return MasterResume.model_validate({
        "basics": {"name": "Jane Doe", "email": "jane@example.com", "location": "New York, NY",
                   "links": [{"label": "GitHub", "url": "https://github.com/janedoe"}]},
        "summary": "Backend engineer with 4 years of experience.",
        "experience": [
            {"company": "Acme", "title": "Software Engineer", "start": "2022-01", "end": "Present", "bullets": [
                {"text": "Cut API p99 latency by 40% by adding Redis caching to the order service", "skills": ["Redis", "Python"]},
                {"text": "Built a Kafka pipeline processing 2,000,000 events per day", "skills": ["Kafka"]},
                {"text": "Mentored 3 junior engineers"},
            ]},
            {"company": "Initech", "title": "Junior Developer", "start": "2020-06", "end": "2021-12", "bullets": [
                {"text": "Wrote REST APIs in Flask for the billing system", "skills": ["Flask", "Python", "REST"]},
            ]},
        ],
        "projects": [{"name": "dotfiles", "bullets": [{"text": "Automated laptop setup with Ansible", "skills": ["Ansible"]}]}],
        "education": [{"institution": "State University", "degree": "B.S.", "field": "Computer Science", "end": "2020"}],
        "skills": [{"category": "Languages", "items": ["Python", "SQL"]},
                   {"category": "Infra", "items": ["Redis", "Kafka", "PostgreSQL"]}],
    }).assign_ids()


@pytest.fixture
def profile() -> ApplicantProfile:
    return ApplicantProfile.model_validate({
        "personal": {"first_name": "Jane", "last_name": "Doe", "email": "jane@example.com", "phone": "+1 555 0100",
                     "address": {"city": "New York", "state": "NY", "country": "United States", "postal_code": "10001"}},
        "links": {"linkedin": "https://linkedin.com/in/janedoe", "github": "https://github.com/janedoe"},
        "work_authorization": {"authorized_countries": ["United States"], "requires_sponsorship": False},
        "preferences": {"years_of_experience": 4, "willing_to_relocate": True},
        "answers": {"how did you hear": "Company careers page"},
    })


@pytest.fixture
def job() -> Job:
    return Job(id="abc123", source="greenhouse", company="Globex", title="Backend Engineer",
               location="New York, NY, United States", url="https://example.com/jobs/1",
               description="We need Python, Redis and Kubernetes experience.")


@pytest.fixture
def analysis() -> JobAnalysis:
    return JobAnalysis(seniority="mid", must_have_skills=["Python", "Kubernetes"], nice_to_have_skills=["Redis"],
                       responsibilities=["Build services"], ats_keywords=["microservices"], work_mode="hybrid",
                       visa_sponsorship="unknown", red_flags=[])
