"""Stage orchestration. Each function is one step of the pipeline, idempotent
over the tracker's state, so you can run steps individually or all at once
(`jobpilot run`) and re-run safely after a crash.

  discover -> filter -> score -> tailor -> review -> apply -> track
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import sources
from .config import Workspace
from .db import Tracker, row_analysis, row_fit, row_job
from .llm import LLMError, StructuredLLM
from .models import JobStatus, TailoredResume
from .pipeline import filters, render, score, tailor

Log = Callable[[str], None]


def discover(ws: Workspace, tracker: Tracker, log: Log = print) -> tuple[int, int]:
    settings = ws.settings()
    jobs = sources.discover(settings.sources, lambda src, slug, e: log(f"  ! {src}/{slug}: {e}"))
    new = kept = 0
    for job in jobs:
        reason = filters.hard_filter(job, settings.search)
        status = JobStatus.FILTERED_OUT if reason else JobStatus.DISCOVERED
        if tracker.add_job(job, status, reason):
            new += 1
            kept += reason is None
    log(f"Fetched {len(jobs)} postings: {new} new, {kept} passed filters.")
    return new, kept


def add_url(ws: Workspace, tracker: Tracker, url: str, company: Optional[str], title: Optional[str],
            log: Log = print) -> Optional[str]:
    job = sources.from_url(url, company, title)
    if not tracker.add_job(job, JobStatus.DISCOVERED, "added manually"):
        log(f"Already tracked: {job.title} @ {job.company}")
        return None
    log(f"Added {job.id}: {job.title} @ {job.company}")
    return job.id


def score_jobs(ws: Workspace, tracker: Tracker, llm: StructuredLLM, limit: Optional[int] = None,
               log: Log = print) -> int:
    settings, profile, resume = ws.settings(), ws.profile(), ws.resume()
    system = score.build_system(resume, profile)
    rows = tracker.jobs([JobStatus.DISCOVERED])[:limit]
    for row in rows:
        job = row_job(row)
        try:
            result = score.score_job(job, system, profile, llm, settings.llm.effort_analyze)
        except LLMError as e:
            log(f"  ! {job.title} @ {job.company}: {e}")
            continue
        tracker.save_scoring(job.id, result.analysis, result.fit)
        passed = result.fit.score >= settings.search.min_score
        tracker.set_status(job.id, JobStatus.SCORED if passed else JobStatus.SKIPPED,
                           None if passed else f"score {result.fit.score} < {settings.search.min_score}")
        log(f"  {result.fit.score:>3} {'✓' if passed else '·'} {job.title} @ {job.company}")
    return len(rows)


def tailor_jobs(ws: Workspace, tracker: Tracker, llm: StructuredLLM, job_ids: Iterable[str] = (),
                top: Optional[int] = None, log: Log = print) -> list[str]:
    settings, profile, master = ws.settings(), ws.profile(), ws.resume()
    rows = ([tracker.get(i) for i in job_ids] if job_ids
            else tracker.jobs([JobStatus.SCORED], order_by_score=True)[:top])
    done = []
    for row in filter(None, rows):
        job, analysis, fit = row_job(row), row_analysis(row), row_fit(row)
        out = ws.job_dir(job.id)
        try:
            result = tailor.tailor(job, master, profile, analysis, fit, llm, settings.tailor, settings.llm.effort_tailor)
        except LLMError as e:
            log(f"  ! {job.title} @ {job.company}: {e}")
            continue
        review = tailor.write_outputs(out, job, result, fit)
        paths = render.render(master, result.tailored, job.company, out, settings.tailor.paper,
                              f"{profile.personal.first_name}_{profile.personal.last_name}")
        (out / "files.json").write_text(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))

        auto_ok = not settings.apply.require_review and result.report.ok
        tracker.set_status(job.id, JobStatus.APPROVED if auto_ok else JobStatus.TAILORED,
                           None if result.report.ok else "fact check flagged items; see review.md")
        flag = "" if result.report.ok else "  [fact check: needs attention]"
        log(f"  Tailored {job.title} @ {job.company} -> {review}{flag}")
        done.append(job.id)
    return done


def apply_jobs(ws: Workspace, tracker: Tracker, llm: Optional[StructuredLLM], job_ids: Iterable[str] = (),
               confirm=None, log: Log = print) -> None:
    from .apply.answers import AnswerResolver, queue_questions
    from .apply.browser import fill_application

    settings, profile, master = ws.settings(), ws.profile(), ws.resume()
    if settings.apply.mode == "review":
        log("apply.mode is 'review': nothing is filled automatically. Switch to 'assisted' or 'auto' in settings.yaml.")
        return
    rows = ([tracker.get(i) for i in job_ids] if job_ids
            else tracker.jobs([JobStatus.APPROVED, JobStatus.NEEDS_INPUT, JobStatus.READY_TO_SUBMIT],
                              order_by_score=True))
    for row in filter(None, rows):
        job = row_job(row)
        if tracker.applied_today() >= settings.apply.daily_limit:
            log(f"Daily limit of {settings.apply.daily_limit} applications reached.")
            return
        if tracker.applied_to_company(job.company) >= settings.apply.per_company_limit:
            log(f"  Skipping {job.title} @ {job.company}: per-company limit reached.")
            continue
        out = ws.job_dir(job.id)
        files_json = out / "files.json"
        if not files_json.exists():
            log(f"  Skipping {job.id}: not tailored yet (run `jobpilot tailor {job.id}`).")
            continue
        files = {k: Path(v) for k, v in json.loads(files_json.read_text()).items()}
        tailored = TailoredResume.model_validate_json((out / "tailored.json").read_text())
        resolver = AnswerResolver(profile, master, job, files, tailored.cover_letter, llm, settings.llm.effort_answer)

        log(f"\n→ {job.title} @ {job.company}\n  {job.apply_url or job.url}")
        try:
            result = fill_application(job.apply_url or job.url, resolver, out, mode=settings.apply.mode,
                                      headless=settings.apply.headless, confirm=confirm or (lambda r: False), log=log)
        except Exception as e:
            tracker.set_status(job.id, JobStatus.FAILED, f"{type(e).__name__}: {e}")
            log(f"  ! failed: {e}")
            continue

        if result.missing_required:
            queue_questions(ws.pending_questions_path, job, result.missing_required)
        if result.submitted:
            tracker.set_status(job.id, JobStatus.APPLIED, result.confirmation or "submitted")
            log("  ✓ applied")
        elif result.missing_required:
            tracker.set_status(job.id, JobStatus.NEEDS_INPUT,
                               f"{len(result.missing_required)} unanswered required question(s)")
            log(f"  ? {len(result.missing_required)} required question(s) need your answer: "
                f"see {ws.pending_questions_path}")
        else:
            tracker.set_status(job.id, JobStatus.READY_TO_SUBMIT, "filled, not submitted")
            log("  … filled but not submitted")
