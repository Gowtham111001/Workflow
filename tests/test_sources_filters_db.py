from datetime import datetime, timedelta, timezone

from jobpilot.config import SearchSettings
from jobpilot.db import Tracker
from jobpilot.models import Job, JobStatus
from jobpilot.pipeline.filters import hard_filter
from jobpilot.sources import URL_PATTERNS, ashby, greenhouse, lever
from jobpilot.sources.base import html_to_text


def test_greenhouse_parse_unescapes_html():
    job = greenhouse.parse_job("globex", {
        "id": 42, "title": "Backend Engineer", "absolute_url": "https://job-boards.greenhouse.io/globex/jobs/42",
        "company_name": "Globex", "location": {"name": "Remote - US"}, "first_published": "2026-10-01T00:00:00Z",
        "content": "&lt;h2&gt;About&lt;/h2&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;li&gt;Go&lt;/li&gt;&lt;/ul&gt;",
    })
    assert job.company == "Globex" and job.remote is True
    assert "- Python" in job.description and "<" not in job.description
    assert job.apply_url == "https://job-boards.greenhouse.io/embed/job_app?for=globex&token=42"


def test_lever_parse_includes_lists():
    job = lever.parse_job("initech", {
        "id": "11111111-2222-3333-4444-555555555555", "text": "SRE", "hostedUrl": "https://jobs.lever.co/initech/x",
        "categories": {"location": "Austin, TX"}, "description": "<p>Intro</p>", "createdAt": 1790000000000,
        "lists": [{"text": "Requirements", "content": "<li>Linux</li><li>Terraform</li>"}], "workplaceType": "onsite",
    })
    assert "Requirements" in job.description and "- Terraform" in job.description
    assert job.apply_url.endswith("/apply") and job.remote is None


def test_ashby_parse_salary():
    job = ashby.parse_job("ramp", {
        "id": "abc", "title": "Engineer", "location": "NYC", "isRemote": False, "jobUrl": "https://jobs.ashbyhq.com/ramp/abc",
        "descriptionPlain": "Build things", "compensation": {"scrapeableCompensationSalarySummary": "$150K - $200K"},
    })
    assert job.salary == "$150K - $200K" and job.apply_url.endswith("/application")


def test_url_patterns():
    def match(url):
        for source, pattern in URL_PATTERNS:
            if m := pattern.search(url):
                return source, m.groups()

    assert match("https://job-boards.greenhouse.io/anthropic/jobs/4461450008") == ("greenhouse", ("anthropic", "4461450008"))
    assert match("https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=8172510") == ("greenhouse", ("stripe", "8172510"))
    assert match("https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd")[0] == "lever"
    assert match("https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245/application")[0] == "ashby"
    assert match("https://example.com/careers/123") is None


def test_html_to_text_strips_scripts():
    assert html_to_text("<p>Hi</p><script>alert(1)</script><p>there</p>") == "Hi\n\nthere"


def make_job(**kw):
    base = dict(id="j1", source="greenhouse", company="Globex", title="Senior Backend Engineer",
                location="New York, NY", url="https://x", posted_at=datetime.now(timezone.utc).isoformat())
    base.update(kw)
    return Job(**base)


def test_hard_filter():
    s = SearchSettings(title_keywords=["backend"], exclude_title_keywords=["manager"], locations=["New York"],
                       max_age_days=30)
    assert hard_filter(make_job(), s) is None
    assert "title" in hard_filter(make_job(title="Frontend Engineer"), s)
    assert "excluded" in hard_filter(make_job(title="Backend Engineering Manager"), s)
    assert "location" in hard_filter(make_job(location="Berlin"), s)
    assert hard_filter(make_job(location="Remote"), s) is None  # remote_ok
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    assert "days ago" in hard_filter(make_job(posted_at=old), s)


def test_tracker_dedupes_and_counts_limits(tmp_path):
    t = Tracker(tmp_path / "db.sqlite")
    assert t.add_job(make_job())
    assert not t.add_job(make_job(id="j2", source="lever"))  # same company+title reposted elsewhere
    assert t.add_job(make_job(id="j3", title="Data Engineer"))
    t.set_status("j1", JobStatus.APPLIED, "ok")
    assert t.applied_today() == 1 and t.applied_to_company("globex") == 1
    assert [e["status"] for e in t.events("j1")] == ["discovered", "applied"]
    assert t.counts() == {"applied": 1, "discovered": 1}
