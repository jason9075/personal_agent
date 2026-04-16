"""SQLite trace log for workflow runs and per-node payloads."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


MAX_JSON_CHARS = 500_000


@dataclass(frozen=True)
class WorkflowRunLog:
    id: int
    started_at: str
    finished_at: str
    status: str
    start_node_id: str
    trigger: str
    channel_id: str
    message: str
    error: str
    node_count: int


@dataclass(frozen=True)
class WorkflowNodeLog:
    id: int
    run_id: int
    seq: int
    node_id: str
    status: str
    started_at: str
    finished_at: str
    input_json: str
    output_json: str
    error: str


def ensure_trace_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflow_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                start_node_id TEXT NOT NULL DEFAULT '',
                trigger TEXT NOT NULL DEFAULT '',
                channel_id TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                request_json TEXT NOT NULL DEFAULT '{}',
                final_output_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS workflow_node_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                node_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                input_json TEXT NOT NULL DEFAULT '{}',
                output_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (run_id) REFERENCES workflow_runs(id)
            );

            CREATE INDEX IF NOT EXISTS workflow_runs_started_at_idx
                ON workflow_runs(started_at DESC);
            CREATE INDEX IF NOT EXISTS workflow_node_logs_run_seq_idx
                ON workflow_node_logs(run_id, seq);
            """
        )
        conn.commit()


def create_run(
    db_path: Path,
    *,
    start_node_id: str,
    trigger: str,
    channel_id: str,
    message: str,
    request: dict[str, Any],
) -> int:
    ensure_trace_db(db_path)
    now = _now()
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO workflow_runs
                (started_at, status, start_node_id, trigger, channel_id, message, request_json)
            VALUES (?, 'running', ?, ?, ?, ?, ?)
            """,
            (
                now,
                start_node_id,
                trigger,
                channel_id,
                message[:2000],
                _to_json_text(request),
            ),
        )
        conn.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("failed to create workflow trace run")
        return int(cursor.lastrowid)


def finish_run(
    db_path: Path,
    run_id: int,
    *,
    status: str,
    final_output: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    ensure_trace_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE workflow_runs
            SET finished_at = ?, status = ?, final_output_json = ?, error = ?
            WHERE id = ?
            """,
            (_now(), status, _to_json_text(final_output or {}), error[:4000], run_id),
        )
        conn.commit()


def log_node(
    db_path: Path,
    *,
    run_id: int,
    seq: int,
    node_id: str,
    status: str,
    started_at: str,
    input_payload: dict[str, Any],
    output_payload: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    ensure_trace_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO workflow_node_logs
                (run_id, seq, node_id, status, started_at, finished_at, input_json, output_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                seq,
                node_id,
                status,
                started_at,
                _now(),
                _to_json_text(input_payload),
                _to_json_text(output_payload or {}),
                error[:4000],
            ),
        )
        conn.commit()


def list_runs(db_path: Path, *, limit: int = 100) -> list[WorkflowRunLog]:
    ensure_trace_db(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.started_at, r.finished_at, r.status, r.start_node_id,
                   r.trigger, r.channel_id, r.message, r.error, COUNT(n.id) AS node_count
            FROM workflow_runs r
            LEFT JOIN workflow_node_logs n ON n.run_id = r.id
            GROUP BY r.id
            ORDER BY r.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_run(row) for row in rows]


def get_run(db_path: Path, run_id: int) -> WorkflowRunLog | None:
    ensure_trace_db(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT r.id, r.started_at, r.finished_at, r.status, r.start_node_id,
                   r.trigger, r.channel_id, r.message, r.error, COUNT(n.id) AS node_count
            FROM workflow_runs r
            LEFT JOIN workflow_node_logs n ON n.run_id = r.id
            WHERE r.id = ?
            GROUP BY r.id
            """,
            (run_id,),
        ).fetchone()
    return _row_to_run(row) if row else None


def list_node_logs(db_path: Path, run_id: int) -> list[WorkflowNodeLog]:
    ensure_trace_db(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, run_id, seq, node_id, status, started_at, finished_at,
                   input_json, output_json, error
            FROM workflow_node_logs
            WHERE run_id = ?
            ORDER BY seq ASC
            """,
            (run_id,),
        ).fetchall()
    return [_row_to_node_log(row) for row in rows]


def _row_to_run(row: tuple) -> WorkflowRunLog:
    return WorkflowRunLog(
        id=int(row[0]),
        started_at=str(row[1] or ""),
        finished_at=str(row[2] or ""),
        status=str(row[3] or ""),
        start_node_id=str(row[4] or ""),
        trigger=str(row[5] or ""),
        channel_id=str(row[6] or ""),
        message=str(row[7] or ""),
        error=str(row[8] or ""),
        node_count=int(row[9] or 0),
    )


def _row_to_node_log(row: tuple) -> WorkflowNodeLog:
    return WorkflowNodeLog(
        id=int(row[0]),
        run_id=int(row[1]),
        seq=int(row[2]),
        node_id=str(row[3] or ""),
        status=str(row[4] or ""),
        started_at=str(row[5] or ""),
        finished_at=str(row[6] or ""),
        input_json=str(row[7] or "{}"),
        output_json=str(row[8] or "{}"),
        error=str(row[9] or ""),
    )


def _to_json_text(value: Any) -> str:
    text = json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True)
    if len(text) > MAX_JSON_CHARS:
        marker = "\n... <truncated>"
        return text[: MAX_JSON_CHARS - len(marker)] + marker
    return text


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(item) for item in value]
        return str(value)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
