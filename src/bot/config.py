"""Runtime configuration for the private Discord bot."""
from __future__ import annotations

import os
from pathlib import Path


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} not set")
    return value


def _optional_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


ALLOWED_USER_ID = _require_env("ALLOWED_USER_ID")
REPO_ROOT = Path(__file__).resolve().parents[2]
NODES_DIR = Path(__file__).resolve().parents[2] / "nodes"
ENGINE_SYSTEM_PROMPT_PATH = Path(__file__).resolve().with_name("engine_system_prompt.md")
SCHEDULE_DB_PATH = Path(__file__).resolve().parents[2] / "db" / "bot_scheduler.sqlite3"
WORKFLOW_DB_PATH = Path(__file__).resolve().parents[2] / "db" / "workflow.sqlite3"
WORKFLOW_TRACE_DB_PATH = Path(__file__).resolve().parents[2] / "db" / "workflow_trace.sqlite3"
BOT_LOG_DIR = Path(_optional_env("BOT_LOG_DIR", ".local/bot/logs"))
WEB_PORT = int(_optional_env("WEB_PORT", "8765"))
