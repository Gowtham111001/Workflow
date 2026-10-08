"""Stage 2: cheap deterministic filters, run before any tokens are spent."""

from __future__ import annotations

from typing import Optional

from ..config import SearchSettings
from ..models import Job
from ..sources.base import age_days


def hard_filter(job: Job, search: SearchSettings) -> Optional[str]:
    """Return a rejection reason, or None if the job passes."""
    title = job.title.lower()
    if search.title_keywords and not any(k.lower() in title for k in search.title_keywords):
        return "title doesn't match title_keywords"
    for k in search.exclude_title_keywords:
        if k.lower() in title:
            return f"title contains excluded keyword {k!r}"

    if search.locations:
        loc = job.location.lower()
        is_remote = bool(job.remote) or "remote" in loc
        if not any(l.lower() in loc for l in search.locations) and not (search.remote_ok and is_remote):
            return f"location {job.location!r} not in preferred locations"

    if search.max_age_days is not None:
        age = age_days(job.posted_at)
        if age is not None and age > search.max_age_days:
            return f"posted {age:.0f} days ago"
    return None
