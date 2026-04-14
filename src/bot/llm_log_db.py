"""SQLite logging for workflow-owned LLM calls."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,
    node_id       TEXT    NOT NULL,
    model         TEXT    NOT NULL,
    prompt        TEXT    NOT NULL,
    response      TEXT,
    success       INTEGER NOT NULL DEFAULT 1,
    error_message TEXT,
    metadata_json TEXT    NOT NULL DEFAULT '{}'
)
"""

_INSERT = """
INSERT INTO llm_calls
    (created_at, node_id, model, prompt, response, success, error_message, metadata_json)
VALUES
    (:created_at, :node_id, :model, :prompt, :response, :success, :error_message, :metadata_json)
"""


@contextmanager
def _connect(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.execute(_CREATE_TABLE)
        conn.commit()
        yield conn
    finally:
        conn.close()


def log_llm_call(
    *,
    db_path: Path,
    node_id: str,
    model: str,
    prompt: str,
    response: str | None,
    success: bool,
    error_message: str | None = None,
    metadata_json: str = "{}",
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            _INSERT,
            {
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "node_id": node_id,
                "model": model,
                "prompt": prompt,
                "response": response,
                "success": 1 if success else 0,
                "error_message": error_message,
                "metadata_json": metadata_json,
            },
        )
        conn.commit()
