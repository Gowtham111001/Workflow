"""Job discovery: configured company boards, plus any single posting URL you paste."""

from __future__ import annotations

import re
from typing import Callable, Optional

import httpx

from ..config import SourceSettings
from ..models import Job
from . import ashby, greenhouse, lever
from .base import html_to_text, http_client, job_id

BOARD_FETCHERS: dict[str, Callable[[str, httpx.Client], list[Job]]] = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
}

URL_PATTERNS = [
    ("greenhouse", re.compile(r"greenhouse\.io/([\w-]+)/jobs/(\d+)")),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_app\?for=([\w-]+)&token=(\d+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([\w-]+)/([0-9a-f-]{36})")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)/([0-9a-f-]{36})")),
]


def discover(sources: SourceSettings, on_error: Callable[[str, str, Exception], None]) -> list[Job]:
    """Pull every posting from every configured board. One bad board doesn't stop the rest."""
    jobs: list[Job] = []
    with http_client() as client:
        for source, fetch in BOARD_FETCHERS.items():
            for slug in getattr(sources, source):
                try:
                    jobs.extend(fetch(slug, client))
                except Exception as e:  # network, 404 slug, schema drift
                    on_error(source, slug, e)
    return jobs


def from_url(url: str, company: Optional[str] = None, title: Optional[str] = None) -> Job:
    """Turn any posting URL into a Job. Known ATS URLs go through their API;
    anything else is fetched as HTML (you can override company/title)."""
    with http_client() as client:
        for source, pattern in URL_PATTERNS:
            m = pattern.search(url)
            if m:
                fetch_one = {"greenhouse": greenhouse.fetch_one, "lever": lever.fetch_one, "ashby": ashby.fetch_one}
                job = fetch_one[source](m.group(1), m.group(2), client)
                return job.model_copy(update={k: v for k, v in (("company", company), ("title", title)) if v})

        resp = client.get(url)
        resp.raise_for_status()
        page = resp.text
        page_title = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
        return Job(
            id=job_id("manual", url),
            source="manual",
            company=company or "Unknown",
            title=title or (html_to_text(page_title.group(1)) if page_title else url),
            url=url,
            apply_url=url,
            description=html_to_text(page),
        )
