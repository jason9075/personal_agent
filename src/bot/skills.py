"""Shared node execution helpers and routing utilities.

Public surface used by engine.py and bot.py:
  - SkillActionResult       — generic subprocess result dataclass
  - execute_skill_generic   — generic --args-json subprocess call
  - render_general_reply    — fallback LLM reply used by general-reply node
  - format_direct_skill_reply — format stdout as Discord reply
  - execute_schedule_action — shared impl for finance-schedule/run.py

Internal routing helpers (used by engine._try_direct_route):
  - _route_finance_report_direct
  - _route_finance_schedule_direct
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import SCHEDULE_DB_PATH, SKILLS_DIR
from .logging_utils import get_logger
from .prompts import load_prompt, load_prompt_path
from .schedule_db import create_job, delete_job, ensure_db, list_jobs, update_job
from .scheduler import parse_cron


@dataclass(frozen=True)
class SkillActionResult:
    tool_name: str
    args: dict
    stdout: str
    stderr: str
    returncode: int


# ---------------------------------------------------------------------------
# Generic skill execution (--args-json protocol)
# ---------------------------------------------------------------------------


def execute_skill_generic(
    skill_id: str,
    script_path: str,
    args: dict,
    repo_root: Path,
) -> SkillActionResult:
    """Run a skill via the --args-json protocol."""
    logger = get_logger()
    run_py = repo_root / script_path
    if not run_py.exists():
        raise RuntimeError(f"skill script not found: {run_py}")

    cmd = ["python", str(run_py), "--args-json", json.dumps(args, ensure_ascii=False)]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=False,
    )
    logger.info(
        "Executed skill skill=%s returncode=%s cmd=%s",
        skill_id,
        result.returncode,
        cmd,
    )
    return SkillActionResult(
        tool_name=skill_id,
        args=args,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
        returncode=result.returncode,
    )


# ---------------------------------------------------------------------------
# Reply formatting
# ---------------------------------------------------------------------------


def format_direct_skill_reply(action_result: SkillActionResult) -> str:
    """Return a direct Discord reply without Pass 2 synthesis."""
    output = action_result.stdout.strip() or action_result.stderr.strip() or "(no output)"
    if action_result.returncode != 0:
        return f"技能執行失敗：\n```text\n{output[:3500]}\n```"
    return output[:3500]


def render_general_reply(
    user_msg: str,
    *,
    recent_context: str = "",
    system_prompt_path: str | None = None,
) -> str:
    """Generate a normal Discord reply when no skill is selected."""
    logger = get_logger()
    prompt_template = load_prompt("general_reply.md")
    prompt = prompt_template.format(
        user_msg=user_msg,
        recent_context=recent_context.strip() or "(none)",
    )
    system_prompt = load_prompt_path(system_prompt_path).strip() if system_prompt_path else ""
    if system_prompt:
        prompt = f"{system_prompt}\n\n{prompt}"
    cmd = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "-C",
        str(SKILLS_DIR.parents[0]),
    ]
    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        cwd=SKILLS_DIR.parents[0],
        check=False,
    )
    logger.info(
        "General reply completed returncode=%s stdout_len=%s stderr_len=%s",
        result.returncode,
        len(result.stdout),
        len(result.stderr),
    )
    if result.returncode != 0:
        return "目前無法完成一般回覆，請稍後再試。"
    return result.stdout.strip() or "目前沒有可回覆的內容。"


# ---------------------------------------------------------------------------
# Finance schedule shared implementation (used by skills/finance-schedule/run.py)
# ---------------------------------------------------------------------------


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
            last = f" | last={job.last_run_at} {job.last_status}".rstrip() if job.last_run_at or job.last_status else ""
            lines.append(
                f"- #{job.id} {job.name} | cron={job.cron_expr} | source={source_label} | workers={job.workers} | {enabled}{last}"
            )
        return "\n".join(lines)

    if action == "add":
        name = str(args.get("name", "")).strip()
        cron_expr = str(args.get("cron", "")).strip()
        source_id = str(args.get("source", "")).strip()
        workers = int(args.get("workers", 4))
        target_channel = str(args.get("channel", "") or args.get("channel_id", "")).strip() or channel_id
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
        )
        return f"已新增排程 #{job.id} `{job.name}`：cron=`{job.cron_expr}` source=`{job.source_id or '(all)'}` workers={job.workers}"

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
        )
        return f"已更新排程 #{job.id} `{job.name}`：cron=`{job.cron_expr}` source=`{job.source_id or '(all)'}` workers={job.workers}"

    raise RuntimeError(f"unsupported schedule action: {action}")


# ---------------------------------------------------------------------------
# Direct route functions (used by engine._try_direct_route)
# ---------------------------------------------------------------------------


def _route_finance_report_direct(user_msg: str) -> dict | None:
    text = user_msg.strip()
    lowered = text.lower()
    if not any(keyword in lowered for keyword in ("finance", "report", "財經", "報告", "來源", "source")):
        return None

    args: dict[str, object] = {}
    if "finance sources" in lowered or "list sources" in lowered or ("列出" in text and "來源" in text):
        args["list_sources"] = True
        return {"tool": "finance-report", "args": args}

    source_match = re.search(r"(?:\bsource\b|來源)\s*[:=]?\s*([a-zA-Z0-9_-]+)", text, re.IGNORECASE)
    if source_match:
        args["source"] = source_match.group(1)

    workers_match = re.search(r"(?:workers?)\s*[:=]?\s*(\d+)", text, re.IGNORECASE)
    if workers_match:
        args["workers"] = int(workers_match.group(1))

    date_match = re.search(r"\b(\d{8}|\d{4}-\d{2}-\d{2})\b", text)
    if date_match:
        args["target_date"] = date_match.group(1)

    if any(keyword in lowered for keyword in ("finance report", "財經報告", "finance", "report", "財經")):
        return {"tool": "finance-report", "args": args}
    return None


def _route_finance_schedule_direct(user_msg: str) -> dict | None:
    text = user_msg.strip()
    lowered = text.lower()
    if not any(keyword in lowered for keyword in ("schedule", "cron", "排程")):
        return None
    if not any(keyword in lowered for keyword in ("finance", "財經", "report")):
        return None

    args: dict[str, object] = {}
    action_match = re.search(r"\b(list|add|update|delete|enable|disable)\b", lowered)
    if action_match:
        args["action"] = action_match.group(1)
    elif "列表" in text:
        args["action"] = "list"
    elif "新增" in text:
        args["action"] = "add"
    elif "修改" in text:
        args["action"] = "update"
    elif "刪除" in text:
        args["action"] = "delete"
    elif "停用" in text:
        args["action"] = "disable"
    elif "啟用" in text:
        args["action"] = "enable"
    else:
        args["action"] = "list"

    id_match = re.search(r"\b(?:id|job)\s*[:=]?\s*(\d+)\b", lowered)
    if not id_match:
        leading_id = re.search(r"\b(?:update|delete|enable|disable)\s+(\d+)\b", lowered)
        if leading_id:
            id_match = leading_id
    if id_match:
        args["id"] = int(id_match.group(1))

    name_match = re.search(r"\bname\s*[:=]\s*([a-zA-Z0-9_-]+)", text)
    if name_match:
        args["name"] = name_match.group(1)

    cron_match = re.search(r'\bcron\s*[:=]\s*"([^"]+)"', text)
    if not cron_match:
        cron_match = re.search(r"\bcron\s*[:=]\s*([^\s]+(?:\s+[^\s]+){4})", text)
    if cron_match:
        args["cron"] = cron_match.group(1).strip()

    source_match = re.search(r"(?:\bsource\b|來源)\s*[:=]?\s*([a-zA-Z0-9_-]*)", text, re.IGNORECASE)
    if source_match:
        args["source"] = source_match.group(1)

    workers_match = re.search(r"(?:workers?)\s*[:=]?\s*(\d+)", text, re.IGNORECASE)
    if workers_match:
        args["workers"] = int(workers_match.group(1))

    channel_match = re.search(r"(?:channel)\s*[:=]?\s*(\d+)", text, re.IGNORECASE)
    if channel_match:
        args["channel"] = channel_match.group(1)

    return {"tool": "finance-schedule", "args": args}
