"""Prompt loading and runtime context helpers for the Discord bot."""
from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NODES_DIR = _REPO_ROOT / "nodes"
_ENGINE_SYSTEM_PROMPT_PATH = Path(__file__).resolve().with_name("engine_system_prompt.md")


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
            path = _NODES_DIR.parents[0] / raw_path

    if not path.exists():
        raise RuntimeError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def load_engine_system_prompt() -> str:
    """Read the shared engine-level system prompt."""
    if not _ENGINE_SYSTEM_PROMPT_PATH.exists():
        raise RuntimeError(f"engine system prompt not found: {_ENGINE_SYSTEM_PROMPT_PATH}")
    return _ENGINE_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def compose_prompt(*sections: str) -> str:
    """Join non-empty prompt sections with blank lines."""
    return "\n\n".join(section.strip() for section in sections if section and section.strip())


def build_runtime_context(
    *,
    previous_input: str = "",
    run_output: str = "",
    next_nodes: list[dict] | None = None,
    recent_context: str = "",
    user_message: str = "",
    task_prompt: str = "",
) -> str:
    parts: list[str] = []
    if previous_input.strip():
        parts.extend(["PREVIOUS_INPUT:", previous_input.strip(), ""])
    if run_output.strip():
        parts.extend(["RUN_OUTPUT:", run_output.strip(), ""])
    if next_nodes is not None:
        parts.extend(["Reachable next nodes:", json.dumps(next_nodes, ensure_ascii=False, indent=2), ""])
    if recent_context.strip():
        parts.extend(["Recent conversation:", recent_context.strip(), ""])
    if user_message.strip():
        parts.extend(["User message:", user_message.strip(), ""])
    if task_prompt.strip():
        parts.extend(["TASK PROMPT:", task_prompt.strip()])
    return "\n".join(parts).strip()
