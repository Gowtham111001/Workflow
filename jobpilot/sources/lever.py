from __future__ import annotations

from datetime import datetime, timezone

import httpx

from ..models import Job
from .base import html_to_text, job_id

API = "https://api.lever.co/v0/postings/{company}"


def parse_job(company: str, j: dict) -> Job:
    cats = j.get("categories") or {}
    sections = [j.get("description") or ""]
    for lst in j.get("lists") or []:
        sections.append(f"<h3>{lst.get('text', '')}</h3><ul>{lst.get('content', '')}</ul>")
    sections.append(j.get("additional") or "")
    created = j.get("createdAt")
    return Job(
        id=job_id("lever", j["id"]),
        source="lever",
        company=company,
        title=j["text"],
        location=cats.get("location", ""),
        remote=(j.get("workplaceType") == "remote") or None,
        url=j["hostedUrl"],
        apply_url=j.get("applyUrl") or j["hostedUrl"] + "/apply",
        description=html_to_text("".join(sections)),
        posted_at=datetime.fromtimestamp(created / 1000, timezone.utc).isoformat() if created else None,
    )


def fetch(company: str, client: httpx.Client) -> list[Job]:
    resp = client.get(API.format(company=company), params={"mode": "json"})
    resp.raise_for_status()
    return [parse_job(company, j) for j in resp.json()]


def fetch_one(company: str, external_id: str, client: httpx.Client) -> Job:
    resp = client.get(f"{API.format(company=company)}/{external_id}", params={"mode": "json"})
    resp.raise_for_status()
    return parse_job(company, resp.json())
