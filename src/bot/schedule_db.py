"""SQLite-backed schedule storage for the Discord bot."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScheduledJob:
    id: int
    name: str
    cron_expr: str
    source_id: str
    workers: int
    channel_id: str
    enabled: bool
    last_run_at: str
    last_status: str
    last_message: str


def ensure_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS finance_schedule_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                cron_expr TEXT NOT NULL,
                source_id TEXT NOT NULL DEFAULT '',
                workers INTEGER NOT NULL DEFAULT 4,
                channel_id TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run_at TEXT NOT NULL DEFAULT '',
                last_status TEXT NOT NULL DEFAULT '',
                last_message TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.commit()


def list_jobs(db_path: Path) -> list[ScheduledJob]:
    ensure_db(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, name, cron_expr, source_id, workers, channel_id, enabled,
                   last_run_at, last_status, last_message
            FROM finance_schedule_jobs
            ORDER BY id ASC
            """
        ).fetchall()
    return [_row_to_job(row) for row in rows]


def create_job(
    db_path: Path,
    *,
    name: str,
    cron_expr: str,
    source_id: str,
    workers: int,
    channel_id: str,
) -> ScheduledJob:
    ensure_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO finance_schedule_jobs (name, cron_expr, source_id, workers, channel_id, enabled)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (name, cron_expr, source_id, workers, channel_id),
        )
        conn.commit()
        job_id = int(cursor.lastrowid)
    return get_job(db_path, job_id)


def get_job(db_path: Path, job_id: int) -> ScheduledJob:
    ensure_db(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, name, cron_expr, source_id, workers, channel_id, enabled,
                   last_run_at, last_status, last_message
            FROM finance_schedule_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"schedule job {job_id} not found")
    return _row_to_job(row)


def update_job(
    db_path: Path,
    job_id: int,
    *,
    name: str | None = None,
    cron_expr: str | None = None,
    source_id: str | None = None,
    workers: int | None = None,
    channel_id: str | None = None,
    enabled: bool | None = None,
) -> ScheduledJob:
    ensure_db(db_path)
    current = get_job(db_path, job_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE finance_schedule_jobs
            SET name = ?, cron_expr = ?, source_id = ?, workers = ?, channel_id = ?, enabled = ?
            WHERE id = ?
            """,
            (
                current.name if name is None else name,
                current.cron_expr if cron_expr is None else cron_expr,
                current.source_id if source_id is None else source_id,
                current.workers if workers is None else workers,
                current.channel_id if channel_id is None else channel_id,
                1 if (current.enabled if enabled is None else enabled) else 0,
                job_id,
            ),
        )
        conn.commit()
    return get_job(db_path, job_id)


def delete_job(db_path: Path, job_id: int) -> None:
    ensure_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM finance_schedule_jobs WHERE id = ?", (job_id,))
        conn.commit()
    if cursor.rowcount == 0:
        raise RuntimeError(f"schedule job {job_id} not found")


def set_job_run_result(
    db_path: Path,
    job_id: int,
    *,
    ran_at: str,
    status: str,
    message: str,
) -> None:
    ensure_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE finance_schedule_jobs
            SET last_run_at = ?, last_status = ?, last_message = ?
            WHERE id = ?
            """,
            (ran_at, status, message[:2000], job_id),
        )
        conn.commit()


def _row_to_job(row: tuple) -> ScheduledJob:
    return ScheduledJob(
        id=int(row[0]),
        name=str(row[1]),
        cron_expr=str(row[2]),
        source_id=str(row[3]),
        workers=int(row[4]),
        channel_id=str(row[5]),
        enabled=bool(row[6]),
        last_run_at=str(row[7] or ""),
        last_status=str(row[8] or ""),
        last_message=str(row[9] or ""),
    )
