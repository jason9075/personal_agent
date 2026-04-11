"""Prompt loading helpers for the Discord bot."""
from __future__ import annotations

from .config import PROMPT_DIR


def load_prompt(name: str) -> str:
    """Read a bot prompt file on demand."""
    path = PROMPT_DIR / name
    if not path.exists():
        raise RuntimeError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8")
