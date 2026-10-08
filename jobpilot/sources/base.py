"""Shared helpers for job sources.

Sources use the public JSON job-board APIs that ATS vendors provide for
exactly this purpose (company career pages are built on them). They are
stable, need no login, and don't violate anyone's terms of service, unlike
scraping LinkedIn/Indeed, which gets accounts banned.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx

USER_AGENT = "jobpilot/0.1 (personal job search)"
BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}


def http_client() -> httpx.Client:
    return httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def job_id(source: str, external_id: str) -> str:
    return hashlib.sha1(f"{source}:{external_id}".encode()).hexdigest()[:12]


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    """Job descriptions arrive as HTML (Greenhouse double-escapes it). Return readable text."""
    if "&lt;" in raw:
        raw = html.unescape(raw)
    parser = _TextExtractor()
    parser.feed(raw)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def age_days(timestamp: str | None) -> float | None:
    if not timestamp:
        return None
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400
