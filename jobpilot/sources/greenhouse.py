from __future__ import annotations

import httpx

from ..models import Job
from .base import html_to_text, job_id

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
# The hosted form. absolute_url is often the company's own careers site with the
# form in an iframe (e.g. stripe.com/jobs/search?gh_jid=...); this URL is the form itself.
FORM = "https://job-boards.greenhouse.io/embed/job_app?for={board}&token={id}"


def parse_job(board: str, j: dict) -> Job:
    location = (j.get("location") or {}).get("name", "")
    return Job(
        id=job_id("greenhouse", str(j["id"])),
        source="greenhouse",
        company=j.get("company_name") or board,
        title=j["title"],
        location=location,
        remote="remote" in location.lower() or None,
        url=j["absolute_url"],
        apply_url=FORM.format(board=board, id=j["id"]),
        description=html_to_text(j.get("content") or ""),
        posted_at=j.get("first_published") or j.get("updated_at"),
    )


def fetch(board: str, client: httpx.Client) -> list[Job]:
    resp = client.get(API.format(board=board), params={"content": "true"})
    resp.raise_for_status()
    return [parse_job(board, j) for j in resp.json().get("jobs", [])]


def fetch_one(board: str, external_id: str, client: httpx.Client) -> Job:
    resp = client.get(f"{API.format(board=board)}/{external_id}")
    resp.raise_for_status()
    return parse_job(board, resp.json())
