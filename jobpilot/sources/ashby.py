from __future__ import annotations

import httpx

from ..models import Job
from .base import html_to_text, job_id

API = "https://api.ashbyhq.com/posting-api/job-board/{board}"


def _salary(j: dict) -> str | None:
    comp = j.get("compensation") or {}
    return comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")


def parse_job(board: str, j: dict) -> Job:
    return Job(
        id=job_id("ashby", j["id"]),
        source="ashby",
        company=board,
        title=j["title"],
        location=j.get("location") or "",
        remote=bool(j.get("isRemote")) or None,
        url=j["jobUrl"],
        apply_url=j.get("applyUrl") or j["jobUrl"] + "/application",
        description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml") or ""),
        posted_at=j.get("publishedAt"),
        salary=_salary(j),
    )


def fetch(board: str, client: httpx.Client) -> list[Job]:
    resp = client.get(API.format(board=board), params={"includeCompensation": "true"})
    resp.raise_for_status()
    return [parse_job(board, j) for j in resp.json().get("jobs", []) if j.get("isListed", True)]


def fetch_one(board: str, external_id: str, client: httpx.Client) -> Job:
    # Ashby has no public single-posting endpoint; find it on the board.
    for job in fetch(board, client):
        if job.id == job_id("ashby", external_id):
            return job
    raise LookupError(f"Ashby posting {external_id} not found on board {board!r}")
