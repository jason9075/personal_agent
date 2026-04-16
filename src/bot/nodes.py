"""Shared node execution helpers."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .schedule_db import create_job, delete_job, ensure_db, list_jobs, parse_input_json, update_job
from .scheduler import parse_cron
from .config import SCHEDULE_DB_PATH


@dataclass(frozen=True)
class NodeActionResult:
    node_id: str
    args: dict
    stdout: str
    stderr: str
    returncode: int


@dataclass(frozen=True)
class NodeLlmEnvelope:
    run_output: str
    response_mode: str
    task_prompt: str = ""
    default_args: dict = field(default_factory=dict)
    output_path: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


def parse_llm_envelope(action_result: NodeActionResult) -> NodeLlmEnvelope | None:
    if action_result.returncode != 0:
        return None
    raw = action_result.stdout.strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"node '{action_result.node_id}' stdout is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"node '{action_result.node_id}' stdout must be a JSON object")
    kind = str(parsed.get("kind", "")).strip()
    if kind == "reply":
        return None
    if kind != "infer":
        raise RuntimeError(f"node '{action_result.node_id}' unknown kind: {kind!r}")
    default_args = parsed.get("default_args", {})
    metadata = parsed.get("metadata", {})
    return NodeLlmEnvelope(
        run_output=str(parsed.get("run_output", "")).strip(),
        response_mode=str(parsed.get("response_mode", "passthrough")).strip() or "passthrough",
        task_prompt=str(parsed.get("task_prompt", "")).strip(),
        default_args=default_args if isinstance(default_args, dict) else {},
        output_path=str(parsed.get("output_path", "")).strip(),
        metadata={str(k): str(v) for k, v in metadata.items()} if isinstance(metadata, dict) else {},
    )


def format_direct_node_reply(action_result: NodeActionResult) -> str:
    """Return a direct Discord reply without additional synthesis."""
    if action_result.returncode != 0:
        output = action_result.stdout.strip() or action_result.stderr.strip() or "(no output)"
        return f"節點執行失敗：\n```text\n{output[:3500]}\n```"
    raw = action_result.stdout.strip()
    if not raw:
        return "(no output)"
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and parsed.get("kind") == "reply":
            return str(parsed.get("reply", "")).strip()
    except json.JSONDecodeError:
        pass
    return raw[:3500]


def execute_schedule_action(args: dict, *, channel_id: str = "") -> str:
    """Shared implementation for schedule operations."""
    ensure_db(SCHEDULE_DB_PATH)
    action = str(args.get("action", "list")).strip().lower()
    if action == "list":
        jobs = list_jobs(SCHEDULE_DB_PATH)
        if not jobs:
            return "目前沒有排程。"
        lines = ["目前排程："]
        for job in jobs:
            target = _format_schedule_target(job.start_node_id, job.input_json)
            enabled = "enabled" if job.enabled else "disabled"
            run_once_label = " | run_once" if job.run_once else ""
            last = f" | last={job.last_run_at} {job.last_status}".rstrip() if job.last_run_at or job.last_status else ""
            lines.append(
                f"- #{job.id} {job.name} | cron={job.cron_expr} | {target} | {enabled}{run_once_label}{last}"
            )
        return "\n".join(lines)

    if action == "add":
        name = str(args.get("name", "")).strip()
        cron_expr = str(args.get("cron", "")).strip()
        start_node_id = str(args.get("start_node_id") or args.get("node") or args.get("target_node_id") or "").strip()
        input_json = _schedule_input_from_args(args)
        target_channel = str(args.get("channel", "") or args.get("channel_id", "")).strip() or channel_id
        run_once = bool(args.get("run_once", False))
        if not name or not cron_expr:
            raise RuntimeError("add requires name=<job_name> and cron=\"m h dom mon dow\"")
        if not start_node_id:
            raise RuntimeError("add requires start_node_id=<node_id>")
        parse_cron(cron_expr)
        job = create_job(
            SCHEDULE_DB_PATH,
            name=name,
            cron_expr=cron_expr,
            start_node_id=start_node_id,
            input_json=input_json,
            channel_id=target_channel,
            run_once=run_once,
        )
        kind = "一次性排程" if job.run_once else "排程"
        return f"已新增{kind} #{job.id} `{job.name}`：cron=`{job.cron_expr}` {_format_schedule_target(job.start_node_id, job.input_json)}"

    if action in {"update", "enable", "disable", "delete"}:
        job_id = int(args.get("id", 0))
        if job_id <= 0:
            raise RuntimeError(f"{action} requires id=<job_id>")

        if action == "delete":
            delete_job(SCHEDULE_DB_PATH, job_id)
            return f"已刪除排程 #{job_id}"
        if action == "enable":
            job = update_job(SCHEDULE_DB_PATH, job_id, enabled=True)
            return f"已啟用排程 #{job.id} `{job.name}`"
        if action == "disable":
            job = update_job(SCHEDULE_DB_PATH, job_id, enabled=False)
            return f"已停用排程 #{job.id} `{job.name}`"

        update_name = str(args.get("name", "")).strip() or None
        update_cron_expr = str(args.get("cron", "")).strip() or None
        update_start_node_id = str(
            args.get("start_node_id") or args.get("node") or args.get("target_node_id") or ""
        ).strip() or None
        update_input_json = _schedule_input_from_args(args) if _has_schedule_input(args) else None
        update_target_channel = str(args.get("channel", "") or args.get("channel_id", "")).strip() or None
        update_run_once = bool(args["run_once"]) if "run_once" in args else None
        if update_cron_expr:
            parse_cron(update_cron_expr)
        job = update_job(
            SCHEDULE_DB_PATH,
            job_id,
            name=update_name,
            cron_expr=update_cron_expr,
            start_node_id=update_start_node_id,
            input_json=update_input_json,
            channel_id=update_target_channel,
            run_once=update_run_once,
        )
        return f"已更新排程 #{job.id} `{job.name}`：cron=`{job.cron_expr}` {_format_schedule_target(job.start_node_id, job.input_json)}"

    raise RuntimeError(f"unsupported schedule action: {action}")


def _has_schedule_input(args: dict) -> bool:
    return any(key in args for key in ("input_json", "message", "task_message", "args", "source", "target_date", "workers"))


def _schedule_input_from_args(args: dict) -> dict[str, Any]:
    if "input_json" in args:
        raw_input = args["input_json"]
        if isinstance(raw_input, dict):
            parsed = dict(raw_input)
        else:
            parsed = parse_input_json(str(raw_input))
    else:
        parsed = {
            "message": str(args.get("message", "") or args.get("task_message", "")),
            "args": {},
            "metadata": {},
        }

    node_args = parsed.get("args", {})
    if not isinstance(node_args, dict):
        node_args = {}
    for key in ("source", "target_date", "workers"):
        if key in args:
            node_args[key] = args[key]
    parsed["args"] = node_args
    parsed["message"] = str(parsed.get("message", ""))
    metadata = parsed.get("metadata", {})
    parsed["metadata"] = metadata if isinstance(metadata, dict) else {}
    return parsed


def _format_schedule_target(start_node_id: str, input_json: str) -> str:
    try:
        parsed = parse_input_json(input_json)
    except RuntimeError:
        parsed = {}
    message = str(parsed.get("message", "")).strip()
    args = parsed.get("args", {})
    args_label = json.dumps(args, ensure_ascii=False, sort_keys=True) if isinstance(args, dict) and args else "{}"
    if len(args_label) > 80:
        args_label = args_label[:77] + "..."
    message_label = message[:60] + ("..." if len(message) > 60 else "")
    return f"node={start_node_id} | message={message_label or '(empty)'} | args={args_label}"
