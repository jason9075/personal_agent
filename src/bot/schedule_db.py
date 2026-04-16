"""SQLite-backed schedule storage for workflow jobs."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ScheduledJob:
    id: int
    name: str
    cron_expr: str
    start_node_id: str
    input_json: str
    channel_id: str
    enabled: bool
    run_once: bool
    notify_before_run: bool
    last_run_at: str
    last_status: str
    last_message: str


_EXPECTED_COLUMNS = {
    "id",
    "name",
    "cron_expr",
    "start_node_id",
    "input_json",
    "channel_id",
    "enabled",
    "run_once",
    "notify_before_run",
    "last_run_at",
    "last_status",
    "last_message",
}


def ensure_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        existing_columns = _table_columns(conn, "scheduled_jobs")
        if existing_columns and existing_columns != _EXPECTED_COLUMNS:
            conn.execute("DROP TABLE scheduled_jobs")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                cron_expr TEXT NOT NULL,
                start_node_id TEXT NOT NULL,
                input_json TEXT NOT NULL DEFAULT '{}',
                channel_id TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                run_once INTEGER NOT NULL DEFAULT 0,
                notify_before_run INTEGER NOT NULL DEFAULT 1,
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
            SELECT id, name, cron_expr, start_node_id, input_json, channel_id, enabled, run_once,
                   notify_before_run, last_run_at, last_status, last_message
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
    start_node_id: str,
    input_json: str | dict[str, Any] = "{}",
    channel_id: str = "",
    run_once: bool = False,
    notify_before_run: bool = True,
) -> ScheduledJob:
    ensure_db(db_path)
    normalized_input_json = normalize_input_json(input_json)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO scheduled_jobs
                (name, cron_expr, start_node_id, input_json, channel_id, enabled, run_once, notify_before_run)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                name,
                cron_expr,
                start_node_id,
                normalized_input_json,
                channel_id,
                1 if run_once else 0,
                1 if notify_before_run else 0,
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
            SELECT id, name, cron_expr, start_node_id, input_json, channel_id, enabled, run_once,
                   notify_before_run, last_run_at, last_status, last_message
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
    start_node_id: str | None = None,
    input_json: str | dict[str, Any] | None = None,
    channel_id: str | None = None,
    enabled: bool | None = None,
    run_once: bool | None = None,
    notify_before_run: bool | None = None,
) -> ScheduledJob:
    ensure_db(db_path)
    current = get_job(db_path, job_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE scheduled_jobs
            SET name = ?, cron_expr = ?, start_node_id = ?, input_json = ?, channel_id = ?,
                enabled = ?, run_once = ?, notify_before_run = ?
            WHERE id = ?
            """,
            (
                current.name if name is None else name,
                current.cron_expr if cron_expr is None else cron_expr,
                current.start_node_id if start_node_id is None else start_node_id,
                current.input_json if input_json is None else normalize_input_json(input_json),
                current.channel_id if channel_id is None else channel_id,
                1 if (current.enabled if enabled is None else enabled) else 0,
                1 if (current.run_once if run_once is None else run_once) else 0,
                1 if (current.notify_before_run if notify_before_run is None else notify_before_run) else 0,
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


def parse_input_json(raw_value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_value or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"input_json must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("input_json must be a JSON object")
    args = parsed.get("args", {})
    if args is not None and not isinstance(args, dict):
        raise RuntimeError("input_json.args must be a JSON object")
    metadata = parsed.get("metadata", {})
    if metadata is not None and not isinstance(metadata, dict):
        raise RuntimeError("input_json.metadata must be a JSON object")
    return parsed


def normalize_input_json(raw_value: str | dict[str, Any]) -> str:
    if isinstance(raw_value, dict):
        parsed = raw_value
    else:
        parsed = parse_input_json(raw_value)
    parsed = dict(parsed)
    parsed["message"] = str(parsed.get("message", ""))
    args = parsed.get("args", {})
    parsed["args"] = args if isinstance(args, dict) else {}
    metadata = parsed.get("metadata", {})
    parsed["metadata"] = metadata if isinstance(metadata, dict) else {}
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _row_to_job(row: tuple) -> ScheduledJob:
    return ScheduledJob(
        id=int(row[0]),
        name=str(row[1]),
        cron_expr=str(row[2]),
        start_node_id=str(row[3]),
        input_json=normalize_input_json(str(row[4] or "{}")),
        channel_id=str(row[5] or ""),
        enabled=bool(row[6]),
        run_once=bool(row[7]),
        notify_before_run=bool(row[8]),
        last_run_at=str(row[9] or ""),
        last_status=str(row[10] or ""),
        last_message=str(row[11] or ""),
    )
