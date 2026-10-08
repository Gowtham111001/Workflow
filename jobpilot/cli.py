"""Command-line entry point: `jobpilot <command>` (or `python -m jobpilot`)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from . import workflow
from .config import Workspace
from .db import Tracker, row_fit, row_job
from .llm import LLMError
from .models import JobStatus

EXAMPLES = Path(__file__).resolve().parent.parent / "config"


def _llm(ws: Workspace):
    from .llm import ClaudeLLM

    return ClaudeLLM(ws.settings().llm.model)


def cmd_init(ws: Workspace, args) -> None:
    ws.root.mkdir(parents=True, exist_ok=True)
    for name in ("settings", "profile"):
        dest = ws.root / f"{name}.yaml"
        if dest.exists():
            print(f"exists  {dest}")
        else:
            shutil.copy(EXAMPLES / f"{name}.example.yaml", dest)
            print(f"created {dest}")
    print("\nNext: edit both files, then `jobpilot ingest path/to/resume.pdf`.")


def cmd_ingest(ws: Workspace, args) -> None:
    from .pipeline.ingest import ingest_resume

    if ws.resume_path.exists() and not args.force:
        sys.exit(f"{ws.resume_path} exists (and may have your hand edits). Use --force to overwrite.")
    resume = ingest_resume(Path(args.file), _llm(ws))
    ws.save_resume(resume)
    n = sum(len(e.bullets) for e in resume.experience) + sum(len(p.bullets) for p in resume.projects)
    print(f"Wrote {ws.resume_path}: {len(resume.experience)} roles, {len(resume.projects)} projects, {n} bullets.")
    print("Review it now. Fix anything mis-parsed and add bullets that didn't fit your one-page resume;\n"
          "tailoring can only use what's in this file.")


def cmd_discover(ws, args, tracker):
    workflow.discover(ws, tracker)


def cmd_add(ws, args, tracker):
    workflow.add_url(ws, tracker, args.url, args.company, args.title)


def cmd_score(ws, args, tracker):
    n = workflow.score_jobs(ws, tracker, _llm(ws), args.limit)
    print(f"Scored {n} job(s).")


def cmd_tailor(ws, args, tracker):
    done = workflow.tailor_jobs(ws, tracker, _llm(ws), args.job_ids, args.top)
    if done:
        print(f"\nTailored {len(done)} job(s). Next: `jobpilot review`.")


def cmd_review(ws, args, tracker):
    rows = tracker.jobs([JobStatus.TAILORED], order_by_score=True)
    if not rows:
        print("Nothing to review.")
        return
    for row in rows:
        job, fit = row_job(row), row_fit(row)
        out = ws.job_dir(job.id)
        print(f"\n{'=' * 70}\n{job.title} @ {job.company}  [{job.id}]  score {fit.score if fit else '?'}")
        print(f"{job.url}\n")
        print((out / "review.md").read_text())
        print(f"Files: {out}")
        while True:
            choice = input("[a]pprove  [s]kip  [o]pen folder  [l]ater  [q]uit > ").strip().lower()
            if choice == "o":
                _open(out)
                continue
            break
        if choice == "a":
            tracker.set_status(job.id, JobStatus.APPROVED)
        elif choice == "s":
            tracker.set_status(job.id, JobStatus.SKIPPED, "skipped at review")
        elif choice == "q":
            return


def _confirm_submitted(result) -> bool:
    print(f"\n  Form filled ({len([f for f in result.filled if f.value])} fields). Screenshot: {result.screenshot}")
    for f in result.filled:
        if f.needs_review:
            print(f"  ✎ drafted answer, please check: {f.label}")
        if f.error:
            print(f"  ! could not fill: {f.label} ({f.error})")
    for f in result.missing_required:
        print(f"  ? still needed: {f.label}")
    if result.captcha:
        print("  CAPTCHA present: solve it in the browser.")
    answer = input("  Review the form in the browser and submit it there. Did you submit? [y/N] ")
    return answer.strip().lower().startswith("y")


def cmd_apply(ws, args, tracker):
    workflow.apply_jobs(ws, tracker, _llm(ws), args.job_ids, confirm=_confirm_submitted)


def cmd_run(ws, args, tracker):
    settings = ws.settings()
    llm = _llm(ws)
    print("1/4 Discover"); workflow.discover(ws, tracker)
    print("2/4 Score"); workflow.score_jobs(ws, tracker, llm, args.limit)
    print("3/4 Tailor"); workflow.tailor_jobs(ws, tracker, llm, top=args.top)
    if settings.apply.require_review:
        print("4/4 Review required: run `jobpilot review`, then `jobpilot apply`.")
        return
    print("4/4 Apply"); workflow.apply_jobs(ws, tracker, llm, confirm=_confirm_submitted)


def cmd_status(ws, args, tracker):
    counts = tracker.counts()
    order = [s.value for s in JobStatus]
    print("  ".join(f"{s}: {counts[s]}" for s in order if s in counts) or "No jobs yet.")
    statuses = [JobStatus(s) for s in args.status] if args.status else [
        s for s in JobStatus if s not in (JobStatus.FILTERED_OUT, JobStatus.SKIPPED)]
    rows = tracker.jobs(statuses, order_by_score=True)
    if rows:
        print()
    for r in rows:
        score = "" if r["score"] is None else r["score"]
        print(f"{r['id']}  {r['status']:<15} {score!s:>3}  {r['title'][:45]:<45}  {r['company'][:20]:<20} {r['note'] or ''}")


def cmd_show(ws, args, tracker):
    row = tracker.get(args.job_id)
    if not row:
        sys.exit("No such job.")
    job = row_job(row)
    print(f"{job.title} @ {job.company} ({job.location})\n{job.url}\nstatus: {row['status']}  score: {row['score']}")
    review = ws.root / "applications" / job.id / "review.md"
    if review.exists():
        print("\n" + review.read_text())
    print("\nHistory:")
    for e in tracker.events(job.id):
        print(f"  {e['created_at']}  {e['status']:<15} {e['detail'] or ''}")


def cmd_mark(ws, args, tracker):
    tracker.set_status(args.job_id, JobStatus(args.status), args.note)
    print(f"{args.job_id} -> {args.status}")


def _open(path: Path) -> None:
    opener = {"darwin": "open", "win32": "explorer"}.get(sys.platform, "xdg-open")
    subprocess.run([opener, str(path)], check=False)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="jobpilot", description=__doc__)
    parser.add_argument("--data", default="data", help="data directory (default: ./data)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create data/settings.yaml and data/profile.yaml from examples")
    p = sub.add_parser("ingest", help="parse your resume into data/master_resume.yaml")
    p.add_argument("file")
    p.add_argument("--force", action="store_true")
    sub.add_parser("discover", help="fetch postings from configured boards and filter them")
    p = sub.add_parser("add", help="track a single posting by URL")
    p.add_argument("url")
    p.add_argument("--company")
    p.add_argument("--title")
    p = sub.add_parser("score", help="analyze + fit-score discovered jobs")
    p.add_argument("--limit", type=int)
    p = sub.add_parser("tailor", help="tailor resume + cover letter for scored jobs")
    p.add_argument("job_ids", nargs="*")
    p.add_argument("--top", type=int, default=5, help="when no ids given: best N scored jobs (default 5)")
    sub.add_parser("review", help="approve or skip tailored applications")
    p = sub.add_parser("apply", help="fill (and per mode, submit) approved applications")
    p.add_argument("job_ids", nargs="*")
    p = sub.add_parser("run", help="discover -> score -> tailor -> (review) -> apply")
    p.add_argument("--limit", type=int, help="max jobs to score this run")
    p.add_argument("--top", type=int, default=5)
    p = sub.add_parser("status", help="pipeline overview")
    p.add_argument("--status", action="append", choices=[s.value for s in JobStatus])
    p = sub.add_parser("show", help="details + history for one job")
    p.add_argument("job_id")
    p = sub.add_parser("mark", help="record an outcome, e.g. interviewing / rejected / offer")
    p.add_argument("job_id")
    p.add_argument("status", choices=[s.value for s in JobStatus])
    p.add_argument("--note")

    args = parser.parse_args(argv)
    ws = Workspace(args.data)
    try:
        if args.command in ("init", "ingest"):
            return globals()[f"cmd_{args.command}"](ws, args)
        ws.root.mkdir(parents=True, exist_ok=True)
        tracker = Tracker(ws.db_path)
        try:
            globals()[f"cmd_{args.command}"](ws, args, tracker)
        finally:
            tracker.close()
    except (FileNotFoundError, LLMError, ValueError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
