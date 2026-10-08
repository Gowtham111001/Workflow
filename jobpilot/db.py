"""SQLite application tracker: one row per job, plus an event log.

The jobs table is the pipeline's state machine (see models.JobStatus). Every
status change is also appended to `events`, so you can see exactly what
happened to an application and when.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .models import FitAssessment, Job, JobAnalysis, JobStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    company       TEXT NOT NULL,
    title         TEXT NOT NULL,
    location      TEXT,
    url           TEXT NOT NULL,
    dedupe_key    TEXT NOT NULL,
    status        TEXT NOT NULL,
    score         INTEGER,
    job_json      TEXT NOT NULL,
    analysis_json TEXT,
    fit_json      TEXT,
    note          TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    applied_at    TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS jobs_dedupe ON jobs(dedupe_key);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL REFERENCES jobs(id),
    status     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dedupe_key(job: Job) -> str:
    """Same role reposted on another board (or re-listed) should not be applied to twice."""
    norm = lambda s: " ".join("".join(c for c in s.lower() if c.isalnum() or c.isspace()).split())
    return f"{norm(job.company)}|{norm(job.title)}"


class Tracker:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # -- writes ------------------------------------------------------------

    def add_job(self, job: Job, status: JobStatus = JobStatus.DISCOVERED, note: str | None = None) -> bool:
        """Insert a job if neither its id nor its dedupe key is known. Returns True if new."""
        key = dedupe_key(job)
        exists = self.conn.execute("SELECT 1 FROM jobs WHERE id = ? OR dedupe_key = ?", (job.id, key)).fetchone()
        if exists:
            return False
        now = _now()
        with self.conn:
            self.conn.execute(
                "INSERT INTO jobs (id, source, company, title, location, url, dedupe_key, status, job_json,"
                " note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job.id, job.source, job.company, job.title, job.location, job.url, key, status.value,
                 job.model_dump_json(), note, now, now),
            )
            self._event(job.id, status, note)
        return True

    def set_status(self, job_id: str, status: JobStatus, note: str | None = None) -> None:
        now = _now()
        applied_at = now if status == JobStatus.APPLIED else None
        with self.conn:
            self.conn.execute(
                "UPDATE jobs SET status = ?, note = COALESCE(?, note), updated_at = ?,"
                " applied_at = COALESCE(?, applied_at) WHERE id = ?",
                (status.value, note, now, applied_at, job_id),
            )
            self._event(job_id, status, note)

    def save_scoring(self, job_id: str, analysis: JobAnalysis, fit: FitAssessment) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE jobs SET analysis_json = ?, fit_json = ?, score = ?, updated_at = ? WHERE id = ?",
                (analysis.model_dump_json(), fit.model_dump_json(), fit.score, _now(), job_id),
            )

    def _event(self, job_id: str, status: JobStatus, detail: str | None) -> None:
        self.conn.execute(
            "INSERT INTO events (job_id, status, detail, created_at) VALUES (?, ?, ?, ?)",
            (job_id, status.value, detail, _now()),
        )

    # -- reads -------------------------------------------------------------

    def jobs(self, statuses: Iterable[JobStatus] | None = None, order_by_score: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM jobs"
        params: list[str] = []
        if statuses:
            statuses = list(statuses)
            sql += f" WHERE status IN ({','.join('?' * len(statuses))})"
            params = [s.value for s in statuses]
        sql += " ORDER BY score DESC, created_at" if order_by_score else " ORDER BY created_at"
        return self.conn.execute(sql, params).fetchall()

    def get(self, job_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def events(self, job_id: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM events WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()

    def applied_today(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        return self.conn.execute("SELECT COUNT(*) FROM jobs WHERE applied_at >= ?", (today,)).fetchone()[0]

    def applied_to_company(self, company: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE lower(company) = lower(?) AND applied_at IS NOT NULL", (company,)
        ).fetchone()[0]

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}


def row_job(row: sqlite3.Row) -> Job:
    return Job.model_validate_json(row["job_json"])


def row_analysis(row: sqlite3.Row) -> Optional[JobAnalysis]:
    return JobAnalysis.model_validate_json(row["analysis_json"]) if row["analysis_json"] else None


def row_fit(row: sqlite3.Row) -> Optional[FitAssessment]:
    return FitAssessment.model_validate(json.loads(row["fit_json"])) if row["fit_json"] else None
