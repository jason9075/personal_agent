"""Skill loading, routing, and execution."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .config import GEMINI_TOOL_MODEL, PROMPT_DIR, SCHEDULE_DB_PATH, SKILLS_DIR
from .schedule_db import create_job, delete_job, ensure_db, list_jobs, update_job
from .scheduler import parse_cron


def load_skill_descriptors(skills_dir: Path) -> list[dict[str, str]]:
    """Scan skills/*/SKILL.md and return [{name, description}] from frontmatter only."""
    descriptors: list[dict[str, str]] = []
    if not skills_dir.exists():
        return descriptors
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        if not fm_match:
            continue
        fm = fm_match.group(1)
        name_m = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
        desc_m = re.search(r"^description:\s*(.+)$", fm, re.MULTILINE)
        bypass_m = re.search(r"^\s*bypasses_llm:\s*(.+)$", fm, re.MULTILINE)
        if not name_m or not desc_m:
            continue
        if bypass_m and bypass_m.group(1).strip().lower() != "true":
            continue
        descriptors.append({
            "name": name_m.group(1).strip(),
            "description": desc_m.group(1).strip(),
        })
    return descriptors


def load_skill_body(skill_name: str, skills_dir: Path) -> str:
    """Return the body of a SKILL.md (everything after the frontmatter closing ---)."""
    skill_md = skills_dir / skill_name / "SKILL.md"
    if not skill_md.exists():
        return ""
    text = skill_md.read_text(encoding="utf-8")
    return re.sub(r"^---\n.*?\n---\n*", "", text, flags=re.DOTALL).strip()


def load_skill_section(skill_name: str, skills_dir: Path, section: str) -> str:
    """Return the content of a specific '## Section' from a SKILL.md body."""
    body = load_skill_body(skill_name, skills_dir)
    match = re.search(
        rf"^##\s+{re.escape(section)}\s*\n(.*?)(?=^##\s|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def route_tool_cli(user_msg: str, recent_context: str = "") -> dict | None:
    """Call GEMINI_TOOL_MODEL to decide which skill to invoke."""
    for router in (_route_finance_schedule_direct, _route_finance_report_direct):
        direct = router(user_msg)
        if direct:
            print(f"[bot] direct_tool → {direct['tool']}", flush=True)
            return direct

    if not GEMINI_TOOL_MODEL:
        return None

    descriptors = load_skill_descriptors(SKILLS_DIR)
    if not descriptors:
        return None

    names = [d["name"] for d in descriptors]
    tool_lines = "\n".join(f"- {d['name']}: {d['description']}" for d in descriptors)
    context_block = (
        f"Recent conversation (use this to extract joke or query content if not in user message):\n"
        f"{recent_context}\n\n"
        if recent_context else ""
    )

    template = (PROMPT_DIR / "tool_router.md").read_text(encoding="utf-8")
    prompt = template.format(
        tool_lines=tool_lines,
        context_block=context_block,
        user_msg=user_msg,
    )

    cmd = ["gemini", "--model", GEMINI_TOOL_MODEL, "-p", prompt]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd="/tmp")
    if result.returncode != 0:
        print(f"[bot] route_tool error: {result.stderr.strip()[:200]}", flush=True)
        return None

    raw = result.stdout.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[bot] route_tool: failed to parse JSON: {raw[:100]}", flush=True)
        return None

    tool_name = parsed.get("tool")
    if tool_name not in names:
        return None

    print(f"[bot] route_tool → {tool_name}", flush=True)
    return {"tool": tool_name, "args": parsed.get("args", {})}


def execute_skill(tool_name: str, args: dict | None = None, *, channel_id: str = "") -> str:
    """Execute a routed skill and return a user-facing response string."""
    args = args or {}
    if tool_name == "finance-report":
        return _run_finance_report(args)
    if tool_name == "finance-schedule":
        return _run_finance_schedule(args, channel_id=channel_id)
    raise RuntimeError(f"unknown skill: {tool_name}")


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

    source_match = re.search(r"(?:\bsource\b|來源)\s*[:=]?\s*([a-zA-Z0-9_-]+)", text, re.IGNORECASE)
    if source_match:
        args["source"] = source_match.group(1)

    workers_match = re.search(r"(?:workers?)\s*[:=]?\s*(\d+)", text, re.IGNORECASE)
    if workers_match:
        args["workers"] = int(workers_match.group(1))

    channel_match = re.search(r"(?:channel)\s*[:=]?\s*(\d+)", text, re.IGNORECASE)
    if channel_match:
        args["channel"] = channel_match.group(1)

    return {"tool": "finance-schedule", "args": args}


def _run_finance_report(args: dict) -> str:
    if args.get("list_sources"):
        cmd = ["python", "-m", "src.finance_report.runner", "--list-sources"]
    else:
        cmd = ["python", "-m", "src.finance_report.runner"]
        workers = args.get("workers", 4)
        cmd.extend(["--workers", str(workers)])
        source = str(args.get("source", "")).strip()
        target_date = str(args.get("target_date", "")).strip()
        if source:
            cmd.extend(["--source", source])
        if target_date:
            cmd.append(target_date)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
        check=False,
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    output = stdout or stderr or "(no output)"

    if result.returncode != 0:
        return f"財經報告執行失敗：\n```text\n{output[:3500]}\n```"
    return f"財經報告執行完成：\n```text\n{output[:3500]}\n```"


def _run_finance_schedule(args: dict, *, channel_id: str = "") -> str:
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
        target_channel = str(args.get("channel", "")).strip() or channel_id
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
        source_id = str(args.get("source", "")).strip() or None
        workers = int(args["workers"]) if "workers" in args else None
        target_channel = str(args.get("channel", "")).strip() or None
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
