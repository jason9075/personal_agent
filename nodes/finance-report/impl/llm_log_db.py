"""SQLite logging of LLM prompts and responses for the finance report pipeline."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS finance_llm_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,
    source_id     TEXT    NOT NULL,
    target_date   TEXT    NOT NULL,
    model         TEXT    NOT NULL,
    prompt        TEXT    NOT NULL,
    response      TEXT,
    success       INTEGER NOT NULL DEFAULT 1,
    error_message TEXT
)
"""

_INSERT = """
INSERT INTO finance_llm_log
    (created_at, source_id, target_date, model, prompt, response, success, error_message)
VALUES
    (:created_at, :source_id, :target_date, :model, :prompt, :response, :success, :error_message)
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
    source_id: str,
    target_date: str,
    model: str,
    prompt: str,
    response: str | None,
    success: bool,
    error_message: str | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            _INSERT,
            {
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source_id": source_id,
                "target_date": target_date,
                "model": model,
                "prompt": prompt,
                "response": response,
                "success": 1 if success else 0,
                "error_message": error_message,
            },
        )
        conn.commit()
