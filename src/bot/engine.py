"""Node-first N-Pass workflow execution engine."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .logging_utils import get_logger
from .nodes import NodeActionResult, format_direct_node_reply
from .workflow_db import WorkflowGraph, WorkflowNode, load_workflow_graph


@dataclass(frozen=True)
class NodeDecision:
    decision: str
    reply: str = ""
    next_node_id: str = ""
    args: dict = field(default_factory=dict)


@dataclass(frozen=True)
class NodeExecutionResult:
    node_id: str
    output_text: str
    action_result: NodeActionResult | None = None
    decision: NodeDecision | None = None


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
        logger.info("Executing node id=%s model=%s", current.id, current.model_name)
        result = _execute_node(
            current,
            graph,
            user_msg,
            current_input,
            prev_output,
            repo_root,
            recent_context=recent_context,
        )

        if result.decision is not None:
            if result.decision.decision == "reply":
                reply = result.decision.reply.strip() or "目前沒有可回覆的內容。"
                logger.info("Decision node %s replied directly output_len=%s", current.id, len(reply))
                return reply

            next_node = graph.node_by_id(result.decision.next_node_id)
            if next_node is None or not next_node.enabled:
                raise RuntimeError(f"node '{current.id}' selected unknown or disabled next node '{result.decision.next_node_id}'")
            if next_node.id not in {candidate.id for candidate in graph.candidate_targets(current.id)}:
                raise RuntimeError(f"node '{current.id}' selected unreachable next node '{next_node.id}'")

            prev_output = result.output_text
            current_input = {"message": user_msg, "channel_id": channel_id, **result.decision.args}
            current = next_node
            continue

        if current.send_response:
            logger.info("Node %s send_response=true output_len=%s", current.id, len(result.output_text))
            return result.output_text

        next_node = _first_enabled_successor(graph, current.id)
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
    if node.pre_hook_path:
        _run_hook(node.id, "pre_hook", node.pre_hook_path, node_input, repo_root)

    payload = dict(node_input)
    if node.node_type == "router":
        payload["recent_context"] = recent_context
        payload["next_nodes"] = [
            {
                "id": candidate.id,
                "name": candidate.name,
                "description": candidate.description or candidate.name,
                "model_name": candidate.model_name,
            }
            for candidate in graph.candidate_targets(node.id)
            if candidate.enabled
        ]

    action_result = _execute_executor(node, payload, prev_output, repo_root)
    output_text = format_direct_node_reply(action_result)
    decision = _parse_node_decision(action_result) if node.node_type == "router" else None
    if decision and decision.decision == "reply":
        output_text = decision.reply

    if node.post_hook_path:
        post_result = _run_hook(
            node.id,
            "post_hook",
            node.post_hook_path,
            {
                "input": payload,
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
        decision=decision,
    )


def _parse_node_decision(action_result: NodeActionResult) -> NodeDecision:
    if action_result.returncode != 0:
        raise RuntimeError(f"decision node '{action_result.node_id}' failed: {action_result.stderr[:200]}")
    raw = action_result.stdout.strip()
    if not raw:
        raise RuntimeError(f"decision node '{action_result.node_id}' returned empty output")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"decision node '{action_result.node_id}' returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"decision node '{action_result.node_id}' returned non-object JSON")

    decision = str(parsed.get("decision", "")).strip()
    if decision == "reply":
        return NodeDecision(
            decision="reply",
            reply=str(parsed.get("reply", "")).strip(),
        )
    if decision == "use_next_node":
        args = parsed.get("args", {})
        return NodeDecision(
            decision="use_next_node",
            next_node_id=str(parsed.get("next_node_id", "")).strip(),
            args=args if isinstance(args, dict) else {},
        )
    raise RuntimeError(f"decision node '{action_result.node_id}' returned unsupported decision '{decision}'")


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
    if node.model_name and "model_name" not in payload:
        payload["model_name"] = node.model_name

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


def _first_enabled_successor(
    graph: WorkflowGraph,
    from_node_id: str,
) -> WorkflowNode | None:
    for edge in graph.outgoing(from_node_id):
        next_node = graph.node_by_id(edge.to_node_id)
        if next_node and next_node.enabled:
            return next_node
    return None
