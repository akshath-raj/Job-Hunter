"""SQLite persistence for jobs and applications.

Kept deliberately simple: two tables, JSON blobs for the model payloads, plus a
few promoted columns for cheap filtering. Dedup is by job id so re-running a
search never creates duplicates or re-applies to something already handled.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from . import config
from .models import Application, Job, JobStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    company TEXT,
    title TEXT,
    status TEXT,
    match_score REAL,
    discovered_at TEXT,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    job_id TEXT PRIMARY KEY,
    status TEXT,
    submitted_at TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---- jobs -----------------------------------------------------------------

def upsert_job(job: Job) -> bool:
    """Insert a job. Returns True if newly discovered, False if it already existed.

    An existing job's status is preserved (we never clobber an applied/skipped
    job just because it showed up in search again).
    """
    with _conn() as c:
        existing = c.execute("SELECT id FROM jobs WHERE id = ?", (job.id,)).fetchone()
        if existing:
            return False
        c.execute(
            "INSERT INTO jobs (id, company, title, status, match_score, discovered_at, payload)"
            " VALUES (?,?,?,?,?,?,?)",
            (job.id, job.company, job.title, job.status.value, job.match_score,
             job.discovered_at, job.model_dump_json()),
        )
        return True


def update_job(job: Job) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE jobs SET company=?, title=?, status=?, match_score=?, payload=? WHERE id=?",
            (job.company, job.title, job.status.value, job.match_score,
             job.model_dump_json(), job.id),
        )


def get_job(job_id: str) -> Job | None:
    with _conn() as c:
        row = c.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        return Job.model_validate_json(row["payload"]) if row else None


def list_jobs(status: JobStatus | None = None, limit: int = 200) -> list[Job]:
    with _conn() as c:
        if status:
            rows = c.execute(
                "SELECT payload FROM jobs WHERE status=? "
                "ORDER BY match_score DESC, discovered_at DESC LIMIT ?",
                (status.value, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT payload FROM jobs ORDER BY discovered_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Job.model_validate_json(r["payload"]) for r in rows]


def counts_by_status() -> dict[str, int]:
    with _conn() as c:
        rows = c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}


def all_job_ids() -> set[str]:
    """All known job ids — used to skip re-visiting jobs we've already seen."""
    with _conn() as c:
        return {r["id"] for r in c.execute("SELECT id FROM jobs").fetchall()}


def job_exists(job_id: str) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone() is not None


# ---- applications ---------------------------------------------------------

def save_application(app: Application) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO applications (job_id, status, submitted_at, payload) VALUES (?,?,?,?)"
            " ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,"
            " submitted_at=excluded.submitted_at, payload=excluded.payload",
            (app.job_id, app.status.value, app.submitted_at, app.model_dump_json()),
        )


def get_application(job_id: str) -> Application | None:
    with _conn() as c:
        row = c.execute("SELECT payload FROM applications WHERE job_id=?", (job_id,)).fetchone()
        return Application.model_validate_json(row["payload"]) if row else None


def list_applications(status: JobStatus | None = None) -> list[Application]:
    with _conn() as c:
        if status:
            rows = c.execute(
                "SELECT payload FROM applications WHERE status=?", (status.value,)
            ).fetchall()
        else:
            rows = c.execute("SELECT payload FROM applications").fetchall()
        return [Application.model_validate_json(r["payload"]) for r in rows]
