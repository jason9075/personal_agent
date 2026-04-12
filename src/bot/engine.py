"""N-pass workflow execution engine.

Replaces the hardcoded 2-pass routing block in bot.py / skills.py.
The execution model:

  Pass 1: Route user message → select WorkflowNode → execute skill subprocess
  Pass N: Determined by workflow edges from previous pass (future extension)

For now (Phase 1), the engine implements the same 2-pass behavior as before
but driven by WorkflowGraph loaded from DB instead of hardcoded if/elif.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .logging_utils import get_logger
from .skills import (
    SkillActionResult,
    _route_finance_report_direct,
    _route_finance_schedule_direct,
    format_direct_skill_reply,
    render_skill_reply_pass2,
)
from .workflow_db import (
    SkillDef,
    WorkflowGraph,
    WorkflowNode,
    load_workflow_graph,
    try_pattern_route,
)


def route_pass1(
    user_msg: str,
    db_path: Path,
    *,
    recent_context: str = "",
) -> tuple[WorkflowNode, dict] | None:
    """Attempt to route user message to a Pass 1 node.

    Strategy (ordered):
    1. For each direct_regex node: try built-in router, then named-group regex patterns.
    2. For llm-mode nodes: call LLM router with their descriptions.

    Returns (WorkflowNode, args_dict) or None if no match.
    """
    logger = get_logger()
    graph = load_workflow_graph(db_path)
    pass1_nodes = graph.nodes_at_pass(1)

    # --- Direct routing pass ---
    direct_nodes = [n for n in pass1_nodes if _router_mode(n, graph) == "direct_regex"]
    for node in direct_nodes:
        args = _try_direct_route(node.skill_id, graph.skills.get(node.skill_id), user_msg)
        if args is not None:
            logger.info("Direct route matched skill=%s args=%s", node.skill_id, args)
            return node, args

    # --- LLM routing pass ---
    llm_nodes = [n for n in pass1_nodes if _router_mode(n, graph) == "llm"]
    if llm_nodes:
        result = _llm_route(user_msg, llm_nodes, graph, recent_context)
        if result:
            node_id, args = result
            matched = next((n for n in llm_nodes if n.id == node_id), None)
            if matched:
                logger.info("LLM route matched node=%s args=%s", node_id, args)
                return matched, args

    return None


def execute_and_synthesize(
    user_msg: str,
    node: WorkflowNode,
    args: dict,
    db_path: Path,
    repo_root: Path,
    channel_id: str = "",
) -> str:
    """Execute the selected skill subprocess and apply Pass 2 synthesis if needed.

    Returns the final string to send to Discord.
    """
    logger = get_logger()
    graph = load_workflow_graph(db_path)
    skill = graph.skills.get(node.skill_id)
    if skill is None:
        raise RuntimeError(f"skill '{node.skill_id}' not found in DB")

    # Inject channel_id into args for skills that need it (finance-schedule)
    exec_args = dict(args)
    if channel_id:
        exec_args.setdefault("channel_id", channel_id)

    result = _execute_skill(skill, exec_args, repo_root)
    logger.info(
        "Skill executed skill=%s returncode=%s stdout_len=%s stderr_len=%s",
        skill.id,
        result.returncode,
        len(result.stdout),
        len(result.stderr),
    )

    if _should_synthesize(skill, args, result):
        logger.info("Pass2 synthesis triggered skill=%s", skill.id)
        return render_skill_reply_pass2(user_msg, result)
    else:
        logger.info("Returning direct reply skill=%s", skill.id)
        return format_direct_skill_reply(result)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _router_mode(node: WorkflowNode, graph: WorkflowGraph) -> str:
    skill = graph.skills.get(node.skill_id)
    return skill.router_mode if skill else "llm"


def _try_direct_route(skill_id: str, skill_def: SkillDef | None, user_msg: str) -> dict | None:
    """Try to directly route a message to a skill. Returns args dict or None."""
    # Built-in routers for complex skills (regex + arg extraction logic)
    if skill_id == "finance-schedule":
        result = _route_finance_schedule_direct(user_msg)
        if result:
            return result.get("args", {})

    if skill_id == "finance-report":
        result = _route_finance_report_direct(user_msg)
        if result:
            return result.get("args", {})

    # Pattern-based routing from DB (named capture groups → args dict)
    if skill_def:
        return try_pattern_route(skill_def, user_msg)

    return None


def _execute_skill(skill: SkillDef, args: dict, repo_root: Path) -> SkillActionResult:
    """Run the skill's run.py via --args-json protocol."""
    run_py = repo_root / skill.script_path
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
    return SkillActionResult(
        tool_name=skill.id,
        args=args,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
        returncode=result.returncode,
    )


def _should_synthesize(skill: SkillDef, args: dict, result: SkillActionResult) -> bool:
    """Decide whether Pass 2 LLM synthesis should run for this result."""
    mode = skill.pass2_mode

    if mode == "never":
        return False
    if mode == "always":
        return True

    # "optional" — skill-specific logic (currently only finance-report uses this)
    if mode == "optional":
        if skill.id == "finance-report":
            if args.get("list_sources"):
                return False
            if result.returncode != 0:
                return False
            out = result.stdout.strip()
            if out.startswith("[finance]") or "note written to" in out or "reused existing note" in out:
                return False
            return True
        # Default for other optional skills: synthesize on success
        return result.returncode == 0

    return True


def _llm_route(
    user_msg: str,
    llm_nodes: list[WorkflowNode],
    graph: WorkflowGraph,
    recent_context: str,
) -> tuple[str, dict] | None:
    """Call GEMINI_TOOL_MODEL to select among llm-routable nodes.

    Returns (node_id, args) or None.
    """
    from .config import GEMINI_TOOL_MODEL
    from .prompts import load_prompt

    logger = get_logger()

    if not GEMINI_TOOL_MODEL:
        logger.info("No GEMINI_TOOL_MODEL configured; LLM routing disabled")
        return None

    descriptors = []
    for node in llm_nodes:
        skill = graph.skills.get(node.skill_id)
        if skill and skill.enabled:
            descriptors.append({"node_id": node.id, "name": skill.id, "description": skill.description})

    if not descriptors:
        return None

    tool_lines = "\n".join(f"- {d['name']}: {d['description']}" for d in descriptors)
    valid_names = [d["name"] for d in descriptors]
    node_by_name = {d["name"]: d["node_id"] for d in descriptors}

    context_block = (
        f"Recent conversation (use this to extract query details when needed):\n{recent_context}\n\n"
        if recent_context
        else ""
    )
    template = load_prompt("tool_router.md")
    prompt = template.format(
        tool_lines=tool_lines,
        context_block=context_block,
        user_msg=user_msg,
    )

    cmd = ["gemini", "--model", GEMINI_TOOL_MODEL, "-p", prompt]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd="/tmp", check=False)
    if proc.returncode != 0:
        logger.warning("LLM router error: %s", proc.stderr.strip()[:200])
        return None

    import json as _json
    import re as _re

    raw = _re.sub(r"^```(?:json)?\s*|\s*```$", "", proc.stdout.strip(), flags=_re.DOTALL).strip()
    try:
        parsed = _json.loads(raw)
    except _json.JSONDecodeError:
        logger.warning("LLM router parse failure: %s", raw[:120])
        return None

    tool_name = parsed.get("tool")
    if tool_name not in valid_names:
        return None

    node_id = node_by_name[tool_name]
    return node_id, parsed.get("args", {})
