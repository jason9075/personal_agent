"""Prompt loading helpers for the Discord bot."""
from __future__ import annotations

from pathlib import Path

from .config import ENGINE_SYSTEM_PROMPT_PATH, NODES_DIR


def load_prompt_path(path_str: str | None) -> str:
    """Read a prompt from an absolute or repo-relative path on demand."""
    if not path_str:
        return ""

    raw_path = Path(path_str)
    if raw_path.is_absolute():
        path = raw_path
    else:
        path = raw_path
        if not path.exists():
            path = NODES_DIR.parents[0] / raw_path

    if not path.exists():
        raise RuntimeError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def load_engine_system_prompt() -> str:
    """Read the shared engine-level system prompt."""
    if not ENGINE_SYSTEM_PROMPT_PATH.exists():
        raise RuntimeError(f"engine system prompt not found: {ENGINE_SYSTEM_PROMPT_PATH}")
    return ENGINE_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def compose_prompt(*sections: str) -> str:
    """Join non-empty prompt sections with blank lines."""
    return "\n\n".join(section.strip() for section in sections if section and section.strip())
