"""Prompt loading helpers for the Discord bot."""
from __future__ import annotations

from pathlib import Path

from .config import NODES_DIR


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
