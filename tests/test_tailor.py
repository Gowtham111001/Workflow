from jobpilot.config import TailorSettings
from jobpilot.pipeline import tailor

from .conftest import FakeLLM
from .test_grounding import tailored


def test_retries_with_feedback_then_passes(master, profile, job, analysis):
    bad = tailored()
    bad.experience[0].bullets[0].text = "Reduced latency 90% on Kubernetes"
    llm = FakeLLM(bad, tailored())
    result = tailor.tailor(job, master, profile, analysis, None, llm, TailorSettings())
    assert result.attempts == 2 and result.report.ok and not result.repaired
    assert "Kubernetes" in llm.calls[1]["prompt"]  # violations were fed back
    assert "e1.b1" in llm.calls[0]["system"]  # master resume (with ids) is in the cached system prompt


def test_repairs_when_retry_also_fails(master, profile, job, analysis, tmp_path):
    bad = tailored()
    bad.experience[0].bullets[0].text = "Reduced latency 90%"
    llm = FakeLLM(bad, bad)
    result = tailor.tailor(job, master, profile, analysis, None, llm, TailorSettings())
    assert result.repaired and result.report.ok
    review = tailor.write_outputs(tmp_path, job, result, None)
    assert "Fact check" in review.read_text()
    assert (tmp_path / "tailored.json").exists()
