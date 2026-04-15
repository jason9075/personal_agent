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
    job_type: str
    task_message: str
    source_id: str
    workers: int
    channel_id: str
    enabled: bool
    run_once: bool
    last_run_at: str
    last_status: str
    last_message: str


def ensure_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                cron_expr TEXT NOT NULL,
                job_type TEXT NOT NULL DEFAULT 'finance-report',
                task_message TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                workers INTEGER NOT NULL DEFAULT 4,
                channel_id TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                run_once INTEGER NOT NULL DEFAULT 0,
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
            SELECT id, name, cron_expr, job_type, task_message, source_id, workers, channel_id, enabled, run_once,
                   last_run_at, last_status, last_message
            FROM scheduled_jobs
            ORDER BY id ASC
            """
        ).fetchall()
    return [_row_to_job(row) for row in rows]


def create_job(
    db_path: Path,
    *,
    name: str,
    cron_expr: str,
    job_type: str = "finance-report",
    task_message: str = "",
    source_id: str = "",
    workers: int = 4,
    channel_id: str = "",
    run_once: bool = False,
) -> ScheduledJob:
    ensure_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO scheduled_jobs
                (name, cron_expr, job_type, task_message, source_id, workers, channel_id, enabled, run_once)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                name,
                cron_expr,
                job_type,
                task_message,
                source_id,
                workers,
                channel_id,
                1 if run_once else 0,
            ),
        )
        conn.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("failed to create schedule job")
        job_id = int(cursor.lastrowid)
    return get_job(db_path, job_id)


def get_job(db_path: Path, job_id: int) -> ScheduledJob:
    ensure_db(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, name, cron_expr, job_type, task_message, source_id, workers, channel_id, enabled, run_once,
                   last_run_at, last_status, last_message
            FROM scheduled_jobs
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
    job_type: str | None = None,
    task_message: str | None = None,
    source_id: str | None = None,
    workers: int | None = None,
    channel_id: str | None = None,
    enabled: bool | None = None,
    run_once: bool | None = None,
) -> ScheduledJob:
    ensure_db(db_path)
    current = get_job(db_path, job_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE scheduled_jobs
            SET name = ?, cron_expr = ?, job_type = ?, task_message = ?, source_id = ?, workers = ?, channel_id = ?, enabled = ?, run_once = ?
            WHERE id = ?
            """,
            (
                current.name if name is None else name,
                current.cron_expr if cron_expr is None else cron_expr,
                current.job_type if job_type is None else job_type,
                current.task_message if task_message is None else task_message,
                current.source_id if source_id is None else source_id,
                current.workers if workers is None else workers,
                current.channel_id if channel_id is None else channel_id,
                1 if (current.enabled if enabled is None else enabled) else 0,
                1 if (current.run_once if run_once is None else run_once) else 0,
                job_id,
            ),
        )
        conn.commit()
    return get_job(db_path, job_id)


def delete_job(db_path: Path, job_id: int) -> None:
    ensure_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM scheduled_jobs WHERE id = ?", (job_id,))
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
            UPDATE scheduled_jobs
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
        job_type=str(row[3] or "finance-report"),
        task_message=str(row[4] or ""),
        source_id=str(row[5]),
        workers=int(row[6]),
        channel_id=str(row[7]),
        enabled=bool(row[8]),
        run_once=bool(row[9]),
        last_run_at=str(row[10] or ""),
        last_status=str(row[11] or ""),
        last_message=str(row[12] or ""),
    )
