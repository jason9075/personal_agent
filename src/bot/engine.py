"""Node-first N-Pass workflow execution engine."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import GEMINI_TOOL_MODEL
from .logging_utils import get_logger
from .nodes import (
    NodeActionResult,
    _route_finance_report_direct,
    _route_finance_schedule_direct,
    format_direct_node_reply,
)
from .prompts import load_prompt, load_prompt_path
from .workflow_db import WorkflowEdge, WorkflowGraph, WorkflowNode, load_workflow_graph, try_pattern_route


@dataclass(frozen=True)
class RouteDecision:
    next_node_id: str
    args: dict


@dataclass(frozen=True)
class NodeExecutionResult:
    node_id: str
    output_text: str
    action_result: NodeActionResult | None = None
    route_decision: RouteDecision | None = None


def execute_workflow(
    user_msg: str,
    db_path: Path,
    repo_root: Path,
    *,
    recent_context: str = "",
    channel_id: str = "",
) -> str:
    """Execute the configured workflow starting from the single start node."""
    logger = get_logger()
    graph = load_workflow_graph(db_path)
    current = graph.start_node()
    if current is None:
        raise RuntimeError("workflow has no enabled start_node")

    current_input: dict = {"message": user_msg, "channel_id": channel_id}
    prev_output = ""

    while current:
        logger.info("Executing node id=%s type=%s", current.id, current.node_type)
        result = _execute_node(
            current,
            graph,
            user_msg,
            current_input,
            prev_output,
            repo_root,
            recent_context=recent_context,
        )

        if current.send_response:
            logger.info("Node %s send_response=true output_len=%s", current.id, len(result.output_text))
            return result.output_text

        if current.node_type == "router":
            if result.route_decision is None:
                raise RuntimeError(f"router node '{current.id}' produced no route decision")
            next_node = graph.node_by_id(result.route_decision.next_node_id)
            if next_node is None:
                raise RuntimeError(f"router selected unknown node '{result.route_decision.next_node_id}'")
            prev_output = result.output_text
            current_input = dict(result.route_decision.args)
            current = next_node
            continue

        next_node = _select_successor(graph, current.id, result.action_result)
        if next_node is None:
            logger.info("Node %s has no matching successor; returning direct output", current.id)
            return result.output_text
        prev_output = result.output_text
        current_input = {"message": user_msg, "prev_output": prev_output, "channel_id": channel_id}
        current = next_node

    return "目前沒有可執行的節點。"


def _execute_node(
    node: WorkflowNode,
    graph: WorkflowGraph,
    user_msg: str,
    node_input: dict,
    prev_output: str,
    repo_root: Path,
    *,
    recent_context: str = "",
) -> NodeExecutionResult:
    if node.node_type == "router":
        return _execute_router_node(node, graph, user_msg, recent_context=recent_context)

    if node.pre_hook_path:
        _run_hook(node.id, "pre_hook", node.pre_hook_path, node_input, repo_root)

    action_result = _execute_executor(node, node_input, prev_output, repo_root)
    output_text = format_direct_node_reply(action_result)

    if node.post_hook_path:
        post_result = _run_hook(
            node.id,
            "post_hook",
            node.post_hook_path,
            {
                "input": node_input,
                "prev_output": prev_output,
                "stdout": action_result.stdout,
                "stderr": action_result.stderr,
                "returncode": action_result.returncode,
            },
            repo_root,
        )
        if post_result.stdout.strip():
            output_text = post_result.stdout.strip()

    return NodeExecutionResult(
        node_id=node.id,
        output_text=output_text,
        action_result=action_result,
    )


def _execute_router_node(
    node: WorkflowNode,
    graph: WorkflowGraph,
    user_msg: str,
    *,
    recent_context: str = "",
) -> NodeExecutionResult:
    logger = get_logger()
    candidates = [candidate for candidate in graph.candidate_targets(node.id) if candidate.enabled]
    if not candidates:
        raise RuntimeError(f"router node '{node.id}' has no enabled outgoing candidates")

    direct_candidates = sorted(candidates, key=_direct_route_priority)
    for candidate in direct_candidates:
        args = _try_direct_route(candidate, user_msg)
        if args is not None:
            logger.info("Direct route matched next_node=%s args=%s", candidate.id, args)
            return NodeExecutionResult(
                node_id=node.id,
                output_text=json.dumps(
                    {"next_node_id": candidate.id, "args": args},
                    ensure_ascii=False,
                ),
                route_decision=RouteDecision(next_node_id=candidate.id, args=args),
            )

    decision = _llm_route(node, candidates, user_msg, recent_context)
    if decision is None:
        fallback = next((candidate for candidate in candidates if candidate.id == "general-reply"), None)
        if fallback is None:
            raise RuntimeError(f"router node '{node.id}' could not choose a next node")
        decision = RouteDecision(next_node_id=fallback.id, args={"message": user_msg})

    logger.info("Router selected next_node=%s args=%s", decision.next_node_id, decision.args)
    return NodeExecutionResult(
        node_id=node.id,
        output_text=json.dumps(
            {"next_node_id": decision.next_node_id, "args": decision.args},
            ensure_ascii=False,
        ),
        route_decision=decision,
    )


def _try_direct_route(node: WorkflowNode, user_msg: str) -> dict | None:
    if node.id == "finance-schedule" or node.id.endswith(":finance-schedule"):
        result = _route_finance_schedule_direct(user_msg)
        if result:
            return result.get("args", {})

    if node.id == "finance-report" or node.id.endswith(":finance-report"):
        result = _route_finance_report_direct(user_msg)
        if result:
            return result.get("args", {})

    return try_pattern_route(node, user_msg)


def _direct_route_priority(node: WorkflowNode) -> tuple[int, str]:
    if node.id == "finance-schedule" or node.id.endswith(":finance-schedule"):
        return (0, node.id)
    if node.id == "finance-report" or node.id.endswith(":finance-report"):
        return (1, node.id)
    if node.router_mode == "direct_regex":
        return (2, node.id)
    return (3, node.id)


def _llm_route(
    router_node: WorkflowNode,
    candidates: list[WorkflowNode],
    user_msg: str,
    recent_context: str,
) -> RouteDecision | None:
    logger = get_logger()
    if not GEMINI_TOOL_MODEL:
        logger.info("No GEMINI_TOOL_MODEL configured; router LLM disabled")
        return None

    tool_lines = "\n".join(
        f"- {candidate.id}: {(candidate.route_description or candidate.description or candidate.name).strip()}"
        for candidate in candidates
    )
    context_block = (
        f"Recent conversation (use this to extract query details when needed):\n{recent_context}\n\n"
        if recent_context
        else ""
    )
    system_prompt = load_prompt_path(router_node.system_prompt_path).strip()
    template = load_prompt("tool_router.md")
    prompt = template.format(
        tool_lines=tool_lines,
        context_block=context_block,
        user_msg=user_msg,
    )
    if system_prompt:
        prompt = f"{system_prompt}\n\n{prompt}"

    cmd = ["gemini", "--model", GEMINI_TOOL_MODEL, "-p", prompt]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd="/tmp", check=False)
    if proc.returncode != 0:
        logger.warning("Router LLM error: %s", proc.stderr.strip()[:200])
        return None

    raw = proc.stdout.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Router LLM parse failure: %s", raw[:200])
        return None

    next_node_id = str(parsed.get("tool", "")).strip()
    if next_node_id not in {candidate.id for candidate in candidates}:
        return None
    args = parsed.get("args", {})
    return RouteDecision(next_node_id=next_node_id, args=args if isinstance(args, dict) else {})


def _execute_executor(
    node: WorkflowNode,
    node_input: dict,
    prev_output: str,
    repo_root: Path,
) -> NodeActionResult:
    if not node.executor_path:
        raise RuntimeError(f"node '{node.id}' has no executor_path")

    run_py = repo_root / node.executor_path
    if not run_py.exists():
        raise RuntimeError(f"node executor not found: {run_py}")

    payload = dict(node_input)
    if node.use_prev_output and prev_output and "prev_output" not in payload:
        payload["prev_output"] = prev_output
    if node.system_prompt_path and "system_prompt_path" not in payload:
        payload["system_prompt_path"] = node.system_prompt_path
    if node.prompt_template_path and "prompt_template_path" not in payload:
        payload["prompt_template_path"] = node.prompt_template_path

    result = subprocess.run(
        ["python", str(run_py), "--args-json", json.dumps(payload, ensure_ascii=False)],
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=False,
        timeout=node.timeout_seconds,
    )
    return NodeActionResult(
        node_id=node.id,
        args=payload,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
        returncode=result.returncode,
    )


def _run_hook(
    node_id: str,
    stage_name: str,
    hook_path: str,
    payload: dict,
    repo_root: Path,
) -> NodeActionResult:
    script_path = repo_root / hook_path
    if not script_path.exists():
        raise RuntimeError(f"{stage_name} for node '{node_id}' not found: {script_path}")

    result = subprocess.run(
        ["python", str(script_path), "--args-json", json.dumps(payload, ensure_ascii=False)],
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=False,
    )
    return NodeActionResult(
        node_id=f"{node_id}:{stage_name}",
        args=payload,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
        returncode=result.returncode,
    )


def _select_successor(
    graph: WorkflowGraph,
    from_node_id: str,
    result: NodeActionResult | None,
) -> WorkflowNode | None:
    if result is None:
        return None
    for edge in graph.outgoing(from_node_id):
        if _edge_condition_met(edge, result):
            next_node = graph.node_by_id(edge.to_node_id)
            if next_node and next_node.enabled:
                return next_node
    return None


def _edge_condition_met(edge: WorkflowEdge, result: NodeActionResult) -> bool:
    if edge.condition_type == "always":
        return True
    if edge.condition_type == "returncode_eq":
        try:
            return result.returncode == int(edge.condition_value)
        except (TypeError, ValueError):
            return False
    if edge.condition_type == "output_contains":
        return edge.condition_value in (result.stdout or "")
    return False
