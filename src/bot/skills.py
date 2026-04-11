"""Skill loading, routing, action execution, and pass-2 reply generation."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import GEMINI_TOOL_MODEL, SCHEDULE_DB_PATH, SKILLS_DIR
from .prompts import load_prompt
from .schedule_db import create_job, delete_job, ensure_db, list_jobs, update_job
from .scheduler import parse_cron


@dataclass(frozen=True)
class SkillActionResult:
    tool_name: str
    args: dict
    stdout: str
    stderr: str
    returncode: int


@dataclass(frozen=True)
class SkillFrontmatter:
    name: str
    description: str
    bypasses_llm: bool
    pass2_mode: str


def load_skill_descriptors(skills_dir: Path) -> list[dict[str, str]]:
    """Scan skills/*/SKILL.md and return [{name, description}] from frontmatter only."""
    descriptors: list[dict[str, str]] = []
    if not skills_dir.exists():
        return descriptors
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        frontmatter = load_skill_frontmatter(skill_md.parent.name, skills_dir)
        if not frontmatter or not frontmatter.bypasses_llm:
            continue
        descriptors.append({
            "name": frontmatter.name,
            "description": frontmatter.description,
        })
    return descriptors


def load_skill_frontmatter(skill_name: str, skills_dir: Path) -> SkillFrontmatter | None:
    """Return parsed frontmatter metadata for a skill."""
    skill_md = skills_dir / skill_name / "SKILL.md"
    if not skill_md.exists():
        return None
    text = skill_md.read_text(encoding="utf-8")
    fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not fm_match:
        return None
    fm = fm_match.group(1)
    name_m = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
    desc_m = re.search(r"^description:\s*(.+)$", fm, re.MULTILINE)
    bypass_m = re.search(r"^\s*bypasses_llm:\s*(.+)$", fm, re.MULTILINE)
    pass2_m = re.search(r"^\s*pass2_mode:\s*(.+)$", fm, re.MULTILINE)
    if not name_m or not desc_m:
        return None
    return SkillFrontmatter(
        name=name_m.group(1).strip(),
        description=desc_m.group(1).strip(),
        bypasses_llm=(bypass_m.group(1).strip().lower() == "true") if bypass_m else False,
        pass2_mode=(pass2_m.group(1).strip().lower() if pass2_m else "always"),
    )


def load_skill_body(skill_name: str, skills_dir: Path) -> str:
    """Return the body of a SKILL.md (everything after the frontmatter closing ---)."""
    skill_md = skills_dir / skill_name / "SKILL.md"
    if not skill_md.exists():
        return ""
    text = skill_md.read_text(encoding="utf-8")
    return re.sub(r"^---\n.*?\n---\n*", "", text, flags=re.DOTALL).strip()


def route_tool_cli(user_msg: str, recent_context: str = "") -> dict | None:
    """Call GEMINI_TOOL_MODEL to decide which skill to invoke."""
    for router in (_route_finance_schedule_direct, _route_finance_report_direct):
        direct = router(user_msg)
        if direct:
            print(f"[bot] direct_tool -> {direct['tool']}", flush=True)
            return direct

    if not GEMINI_TOOL_MODEL:
        return None

    descriptors = load_skill_descriptors(SKILLS_DIR)
    if not descriptors:
        return None

    names = [d["name"] for d in descriptors]
    tool_lines = "\n".join(f"- {d['name']}: {d['description']}" for d in descriptors)
    context_block = (
        f"Recent conversation (use this to extract query details when needed):\n"
        f"{recent_context}\n\n"
        if recent_context else ""
    )

    template = load_prompt("tool_router.md")
    prompt = template.format(
        tool_lines=tool_lines,
        context_block=context_block,
        user_msg=user_msg,
    )

    cmd = ["gemini", "--model", GEMINI_TOOL_MODEL, "-p", prompt]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd="/tmp", check=False)
    if result.returncode != 0:
        print(f"[bot] route_tool error: {result.stderr.strip()[:200]}", flush=True)
        return None

    raw = result.stdout.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[bot] route_tool parse failure: {raw[:120]}", flush=True)
        return None

    tool_name = parsed.get("tool")
    if tool_name not in names:
        return None

    print(f"[bot] route_tool -> {tool_name}", flush=True)
    return {"tool": tool_name, "args": parsed.get("args", {})}


def execute_skill_action(tool_name: str, args: dict | None = None, *, channel_id: str = "") -> SkillActionResult:
    """Execute a skill action via skills/<name>/run.py and return raw process output."""
    args = args or {}
    skill_dir = SKILLS_DIR / tool_name
    run_py = skill_dir / "run.py"
    if not run_py.exists():
        raise RuntimeError(f"skill run.py not found for {tool_name}")

    cmd = ["python", str(run_py)]
    if tool_name == "finance-report":
        if args.get("list_sources"):
            cmd.append("--list-sources")
        else:
            cmd.extend(["--workers", str(int(args.get("workers", 4)))])
            source = str(args.get("source", "")).strip()
            target_date = str(args.get("target_date", "")).strip()
            if source:
                cmd.extend(["--source", source])
            if target_date:
                cmd.extend(["--target-date", target_date])
    elif tool_name == "finance-schedule":
        cmd.extend(["--action", str(args.get("action", "list"))])
        if "id" in args:
            cmd.extend(["--id", str(int(args["id"]))])
        if "name" in args and str(args["name"]).strip():
            cmd.extend(["--name", str(args["name"]).strip()])
        if "cron" in args and str(args["cron"]).strip():
            cmd.extend(["--cron", str(args["cron"]).strip()])
        if "source" in args and str(args["source"]).strip():
            cmd.extend(["--source", str(args["source"]).strip()])
        if "workers" in args:
            cmd.extend(["--workers", str(int(args["workers"]))])
        if channel_id:
            cmd.extend(["--channel", channel_id])
    else:
        raise RuntimeError(f"unsupported skill action: {tool_name}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=SKILLS_DIR.parents[0],
        check=False,
    )
    return SkillActionResult(
        tool_name=tool_name,
        args=args,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
        returncode=result.returncode,
    )


def render_skill_reply_pass2(
    user_msg: str,
    action_result: SkillActionResult,
    *,
    recent_context: str = "",
) -> str:
    """Run pass-2 synthesis with codex exec over the action result."""
    tool_output = action_result.stdout.strip()
    if not tool_output:
        tool_output = action_result.stderr.strip() or "(no output)"

    prompt_template = load_prompt("skill_pass2.md")
    prompt = prompt_template.format(
        user_msg=user_msg,
        tool_name=action_result.tool_name,
        tool_args=json.dumps(action_result.args, ensure_ascii=False),
        tool_output=tool_output,
        recent_context=recent_context.strip() or "(none)",
    )
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
    if result.returncode != 0:
        fallback = action_result.stdout.strip() or action_result.stderr.strip() or "技能執行完成，但整理回覆失敗。"
        return f"技能已執行，但 Pass 2 整理失敗：\n```text\n{fallback[:3500]}\n```"
    return result.stdout.strip() or "技能已執行完成。"


def render_general_reply(user_msg: str, *, recent_context: str = "") -> str:
    """Generate a normal Discord reply when no skill is selected."""
    prompt_template = load_prompt("general_reply.md")
    prompt = prompt_template.format(
        user_msg=user_msg,
        recent_context=recent_context.strip() or "(none)",
    )
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
    if result.returncode != 0:
        return "目前無法完成一般回覆，請稍後再試。"
    return result.stdout.strip() or "目前沒有可回覆的內容。"


def should_use_pass2(tool_name: str, args: dict | None, action_result: SkillActionResult) -> bool:
    """Decide whether a skill result still needs pass-2 synthesis."""
    args = args or {}
    metadata = load_skill_frontmatter(tool_name, SKILLS_DIR)
    mode = metadata.pass2_mode if metadata else "always"

    if mode == "never":
        return False
    if mode == "always":
        return True

    if mode == "optional":
        if tool_name == "finance-schedule":
            return False
        if tool_name == "finance-report":
            if args.get("list_sources"):
                return False
            if action_result.returncode != 0:
                return False
            output = action_result.stdout.strip()
            if output.startswith("[finance]") or "note written to" in output or "reused existing note" in output:
                return False
            return True

    return True


def format_direct_skill_reply(action_result: SkillActionResult) -> str:
    """Return a direct reply without pass-2 synthesis."""
    output = action_result.stdout.strip() or action_result.stderr.strip() or "(no output)"
    if action_result.returncode != 0:
        return f"技能執行失敗：\n```text\n{output[:3500]}\n```"
    return output[:3500]


def execute_schedule_action(args: dict, *, channel_id: str = "") -> str:
    """Shared implementation for finance schedule run.py."""
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
        source_id = str(args.get("source", "")).strip() if "source" in args else None
        if source_id == "":
            source_id = ""
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
