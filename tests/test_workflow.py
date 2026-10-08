import json

import pytest
import yaml

from jobpilot import workflow
from jobpilot.cli import EXAMPLES
from jobpilot.config import Workspace, write_yaml
from jobpilot.db import Tracker
from jobpilot.models import FitAssessment, JobStatus
from jobpilot.pipeline.score import JobScoring

from .conftest import FakeLLM
from .test_grounding import tailored

pytest.importorskip("playwright.sync_api")


@pytest.fixture
def ws(tmp_path, master, profile):
    ws = Workspace(tmp_path)
    settings = yaml.safe_load((EXAMPLES / "settings.example.yaml").read_text())
    write_yaml(ws.settings_path, settings)
    write_yaml(ws.profile_path, profile.model_dump())
    ws.save_resume(master)
    return ws


def scoring(analysis, score):
    return JobScoring(analysis=analysis, fit=FitAssessment(score=score, verdict="strong", matched_requirements=["Python"],
                                                           gaps=["Kubernetes"], rationale="Solid backend match."))


def test_score_then_tailor(ws, job, analysis):
    tracker = Tracker(ws.db_path)
    other = job.model_copy(update={"id": "zzz999", "title": "iOS Engineer"})
    tracker.add_job(job)
    tracker.add_job(other)

    llm = FakeLLM(scoring(analysis, 82), scoring(analysis, 30))
    workflow.score_jobs(ws, tracker, llm, log=lambda m: None)
    assert tracker.get(job.id)["status"] == "scored" and tracker.get(job.id)["score"] == 82
    assert tracker.get(other.id)["status"] == "skipped"
    assert llm.calls[0]["effort"] == "low"

    done = workflow.tailor_jobs(ws, tracker, FakeLLM(tailored()), top=5, log=lambda m: None)
    assert done == [job.id]
    assert tracker.get(job.id)["status"] == JobStatus.TAILORED.value  # require_review: true
    out = ws.root / "applications" / job.id
    files = json.loads((out / "files.json").read_text())
    assert files["resume_pdf"].endswith("Jane_Doe_Resume.pdf")
    assert "Fit: 82/100" in (out / "review.md").read_text()


def test_sponsorship_guardrail(ws, job, analysis, profile):
    profile.work_authorization.requires_sponsorship = True
    write_yaml(ws.profile_path, profile.model_dump())
    tracker = Tracker(ws.db_path)
    tracker.add_job(job)
    no_visa = analysis.model_copy(update={"visa_sponsorship": "no"})
    workflow.score_jobs(ws, tracker, FakeLLM(scoring(no_visa, 95)), log=lambda m: None)
    row = tracker.get(job.id)
    assert row["score"] == 0 and row["status"] == "skipped"
