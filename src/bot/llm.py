"""Centralized Codex execution for workflow-owned LLM calls."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .llm_log_db import log_llm_call
from .prompts import build_runtime_context, compose_prompt, load_engine_system_prompt, load_prompt_path

_LLM_LOG_DB_PATH = Path(__file__).resolve().parents[2] / "db" / "llm_calls.sqlite3"
_MAX_IMAGE_PATHS = 5


@dataclass(frozen=True)
class LlmRequest:
    node_id: str
    model_name: str
    node_prompt_path: str | None
    previous_input: str = ""
    run_output: str = ""
    next_nodes: list[dict] = field(default_factory=list)
    recent_context: str = ""
    user_message: str = ""
    task_prompt: str = ""
    image_paths: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


def build_llm_prompt(request: LlmRequest) -> str:
    engine_prompt = load_engine_system_prompt()
    node_prompt = load_prompt_path(request.node_prompt_path)
    runtime_context = build_runtime_context(
        previous_input=request.previous_input,
        run_output=request.run_output,
        next_nodes=request.next_nodes,
        recent_context=request.recent_context,
        user_message=request.user_message,
        task_prompt=request.task_prompt,
    )
    return compose_prompt(engine_prompt, node_prompt, runtime_context)


def run_codex_request(request: LlmRequest, repo_root: Path) -> str:
    prompt = build_llm_prompt(request)
    cmd = [
        "codex",
        "-m",
        request.model_name or "gpt-5.4",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "-C",
        str(repo_root),
    ]
    for image_path in _existing_image_paths(request.image_paths):
        cmd.extend(["--image", str(image_path)])
    completed = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        capture_output=True,
        cwd=repo_root,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip() or "codex exec failed"
        _log_request(
            request=request,
            prompt=prompt,
            response=None,
            success=False,
            error_message=stderr,
        )
        raise RuntimeError(stderr)

    response = completed.stdout.strip()
    _log_request(
        request=request,
        prompt=prompt,
        response=response,
        success=True,
        error_message=None,
    )
    return response


def unwrap_decision_reply(raw: str) -> str:
    """If LLM wrapped output in {"decision":"reply","reply":"..."}, extract the reply field."""
    text = raw.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("decision") == "reply" and "reply" in parsed:
            return str(parsed["reply"]).strip()
    except (json.JSONDecodeError, ValueError):
        pass
    return text


def parse_json_response(raw: str, fallback_reply: str) -> dict:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
    if not text:
        return {"decision": "reply", "reply": fallback_reply}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"decision": "reply", "reply": fallback_reply}
    if not isinstance(parsed, dict):
        return {"decision": "reply", "reply": fallback_reply}
    return parsed


def _log_request(
    *,
    request: LlmRequest,
    prompt: str,
    response: str | None,
    success: bool,
    error_message: str | None,
) -> None:
    metadata = dict(request.metadata)
    metadata["node_prompt_path"] = request.node_prompt_path or ""
    metadata["has_task_prompt"] = "1" if request.task_prompt else "0"
    metadata["image_count"] = str(len(_existing_image_paths(request.image_paths)))
    log_llm_call(
        db_path=_LLM_LOG_DB_PATH,
        node_id=request.node_id,
        model=request.model_name or "gpt-5.4",
        prompt=prompt,
        response=response,
        success=success,
        error_message=error_message,
        metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
    )


def _existing_image_paths(image_paths: list[str]) -> list[Path]:
    paths: list[Path] = []
    for image_path in image_paths:
        if len(paths) >= _MAX_IMAGE_PATHS:
            break
        path = Path(image_path)
        if path.exists() and path.is_file():
            paths.append(path)
    return paths
