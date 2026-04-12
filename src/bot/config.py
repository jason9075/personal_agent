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
GEMINI_TOOL_MODEL = _optional_env("GEMINI_TOOL_MODEL")
PROMPT_DIR = Path(__file__).resolve().parent / "prompt"
SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"
SCHEDULE_DB_PATH = Path(__file__).resolve().parents[2] / "db" / "bot_scheduler.sqlite3"
WORKFLOW_DB_PATH = Path(__file__).resolve().parents[2] / "db" / "workflow.sqlite3"
BOT_LOG_DIR = Path(_optional_env("BOT_LOG_DIR", ".local/bot/logs"))
WEB_PORT = int(_optional_env("WEB_PORT", "8765"))
