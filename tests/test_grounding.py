from jobpilot.models import TailoredResume
from jobpilot.pipeline import grounding


def tailored(**overrides) -> TailoredResume:
    data = {
        "headline": "Backend Engineer",
        "summary": "Backend engineer.",
        "experience": [
            {"source_id": "e1", "bullets": [
                {"source_ids": ["e1.b1"], "text": "Reduced API p99 latency 40% with Redis caching"},
                {"source_ids": ["e1.b2"], "text": "Built Kafka pipeline handling 2,000,000 daily events"},
            ]},
            {"source_id": "e2", "bullets": [{"source_ids": ["e2.b1"], "text": "Built Flask REST APIs for billing"}]},
        ],
        "projects": [],
        "skills": [{"category": "Core", "items": ["Python", "Redis"]}],
        "cover_letter": "I cut latency by 40% at Acme.",
        "change_notes": [],
    }
    data.update(overrides)
    return TailoredResume.model_validate(data)


def test_clean_resume_passes(master, analysis):
    report = grounding.check(tailored(), master, analysis)
    assert report.ok, report.as_feedback()
    assert not report.warnings


def test_changed_number_is_flagged(master, analysis):
    t = tailored()
    t.experience[0].bullets[0].text = "Reduced API p99 latency 60% with Redis caching"
    report = grounding.check(t, master, analysis)
    assert any("60" in v.problem for v in report.violations)


def test_citing_another_roles_bullet_is_flagged(master, analysis):
    t = tailored()
    t.experience[1].bullets[0].source_ids = ["e1.b1"]
    assert not grounding.check(t, master, analysis).ok


def test_keyword_stuffing_is_flagged(master, analysis):
    t = tailored()
    t.experience[0].bullets[0].text = "Reduced API p99 latency 40% with Redis caching on Kubernetes"
    report = grounding.check(t, master, analysis)
    assert any("Kubernetes" in v.problem for v in report.violations)


def test_keyword_from_skills_list_is_allowed(master, analysis):
    t = tailored()
    # Python isn't in e1.b2's text, but it is in the master skills list.
    t.experience[0].bullets[1].text = "Built Python Kafka pipeline handling 2,000,000 daily events"
    assert grounding.check(t, master, analysis).ok


def test_unevidenced_skill_and_cover_letter_number(master, analysis):
    t = tailored(skills=[{"category": "Core", "items": ["Python", "Kubernetes"]}],
                 cover_letter="I have 10 years of experience.")
    problems = [v.where for v in grounding.check(t, master, analysis).violations]
    assert "skills" in problems and "cover_letter" in problems


def test_repair_restores_originals_and_dropped_roles(master, analysis):
    t = tailored(skills=[{"category": "Core", "items": ["Python", "Kubernetes"]}])
    t.experience[0].bullets[0].text = "Reduced latency 75% on Kubernetes"
    t.experience = t.experience[:1]  # drop Initech
    report = grounding.check(t, master, analysis)
    fixed = grounding.repair(t, master, report)
    assert fixed.experience[0].bullets[0].text == master.experience[0].bullets[0].text
    assert {s.source_id for s in fixed.experience} == {"e1", "e2"}
    assert fixed.skills[0].items == ["Python"]
    assert grounding.check(fixed, master, analysis).ok
