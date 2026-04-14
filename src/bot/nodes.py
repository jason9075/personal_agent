"""Shared node execution helpers."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .schedule_db import create_job, delete_job, ensure_db, list_jobs, update_job
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
    """Shared implementation for finance schedule operations."""
    ensure_db(SCHEDULE_DB_PATH)
    action = str(args.get("action", "list")).strip().lower()
    if action == "list":
        jobs = list_jobs(SCHEDULE_DB_PATH)
        if not jobs:
            return "目前沒有排程。"
        lines = ["目前排程："]
        for job in jobs:
            source_label = job.source_id or "(all)"
            enabled = "enabled" if job.enabled else "disabled"
            run_once_label = " | run_once" if job.run_once else ""
            last = f" | last={job.last_run_at} {job.last_status}".rstrip() if job.last_run_at or job.last_status else ""
            lines.append(
                f"- #{job.id} {job.name} | cron={job.cron_expr} | source={source_label} | workers={job.workers} | {enabled}{run_once_label}{last}"
            )
        return "\n".join(lines)

    if action == "add":
        name = str(args.get("name", "")).strip()
        cron_expr = str(args.get("cron", "")).strip()
        source_id = str(args.get("source", "")).strip()
        workers = int(args.get("workers", 4))
        target_channel = str(args.get("channel", "") or args.get("channel_id", "")).strip() or channel_id
        run_once = bool(args.get("run_once", False))
        if not name or not cron_expr:
            raise RuntimeError("add requires name=<job_name> and cron=\"m h dom mon dow\"")
        parse_cron(cron_expr)
        job = create_job(
            SCHEDULE_DB_PATH,
            name=name,
            cron_expr=cron_expr,
            source_id=source_id,
            workers=workers,
            channel_id=target_channel,
            run_once=run_once,
        )
        kind = "一次性排程" if job.run_once else "排程"
        return f"已新增{kind} #{job.id} `{job.name}`：cron=`{job.cron_expr}` source=`{job.source_id or '(all)'}` workers={job.workers}"

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

        name = str(args.get("name", "")).strip() or None
        cron_expr = str(args.get("cron", "")).strip() or None
        source_id = str(args.get("source", "")).strip() if "source" in args else None
        if source_id == "":
            source_id = ""
        workers = int(args["workers"]) if "workers" in args else None
        target_channel = str(args.get("channel", "") or args.get("channel_id", "")).strip() or None
        run_once = bool(args["run_once"]) if "run_once" in args else None
        if cron_expr:
            parse_cron(cron_expr)
        job = update_job(
            SCHEDULE_DB_PATH,
            job_id,
            name=name,
            cron_expr=cron_expr,
            source_id=source_id,
            workers=workers,
            channel_id=target_channel,
            run_once=run_once,
        )
        return f"已更新排程 #{job.id} `{job.name}`：cron=`{job.cron_expr}` source=`{job.source_id or '(all)'}` workers={job.workers}"

    raise RuntimeError(f"unsupported schedule action: {action}")
