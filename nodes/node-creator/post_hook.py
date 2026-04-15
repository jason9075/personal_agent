"""Post-hook for node-creator — applies the LLM-generated node spec to disk and DB."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.bot.workflow_db import WorkflowEdge, WorkflowNode, get_node, upsert_edge, upsert_node  # noqa: E402


def main() -> int:
    if "--args-json" not in sys.argv:
        return 1

    idx = sys.argv.index("--args-json")
    payload: dict = json.loads(sys.argv[idx + 1])

    run_py_stdout = str(payload.get("stdout", "")).strip()
    if not run_py_stdout:
        print("Node creator: run.py produced no output.")
        return 0

    # Extract output_path from run.py's envelope JSON
    try:
        envelope = json.loads(run_py_stdout)
    except json.JSONDecodeError:
        print("Node creator: run.py stdout is not valid JSON.")
        return 0

    pending_spec_path_str = str(envelope.get("output_path", "")).strip()
    if not pending_spec_path_str:
        print("Node creator: missing output_path in run.py output.")
        return 0

    pending_spec_path = REPO_ROOT / pending_spec_path_str
    if not pending_spec_path.exists():
        print("Node creator: LLM did not produce a spec file. 請再試一次。")
        return 0

    raw_spec = pending_spec_path.read_text(encoding="utf-8").strip()
    # Strip markdown code fences if LLM added them
    raw_spec = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_spec, flags=re.DOTALL).strip()

    try:
        spec = json.loads(raw_spec)
    except json.JSONDecodeError as exc:
        pending_spec_path.unlink(missing_ok=True)
        print(f"Node creator: LLM 輸出的 spec 不是合法 JSON（{exc}）。請描述得更詳細後再試。")
        return 0

    try:
        node_specs = _normalize_node_specs(spec)
        requested_edges = _normalize_edges(spec)
    except ValueError as exc:
        pending_spec_path.unlink(missing_ok=True)
        print(f"Node creator: {exc}。請再試一次。")
        return 0

    db_path = REPO_ROOT / "db" / "workflow.sqlite3"
    applied_nodes: list[dict[str, object]] = []
    applied_edges: list[tuple[str, str]] = []

    for node_spec in node_specs:
        try:
            result = _apply_node_spec(db_path, node_spec)
        except ValueError as exc:
            pending_spec_path.unlink(missing_ok=True)
            print(f"Node creator: {exc}。請再試一次。")
            return 0
        applied_nodes.append(result)
        if result["added_router_edge"]:
            applied_edges.append(("intent-router", str(result["node_id"])))

    for from_node_id, to_node_id in requested_edges:
        upsert_edge(db_path, WorkflowEdge(id=0, from_node_id=from_node_id, to_node_id=to_node_id))
        applied_edges.append((from_node_id, to_node_id))

    pending_spec_path.unlink(missing_ok=True)

    # Plain-text confirmation (engine returns this as-is to Discord)
    lines = []
    if len(applied_nodes) == 1:
        result = applied_nodes[0]
        action_label = "更新" if result["is_update"] else "新增"
        lines.append(f"✅ Node `{result['node_id']}` 已{action_label}！")
    else:
        lines.append(f"✅ 已套用 {len(applied_nodes)} 個 nodes！")

    for result in applied_nodes:
        action_label = "更新" if result["is_update"] else "新增"
        lines.append(f"- `{result['node_id']}`：{action_label}，{result['name']}，Timeout {result['timeout_seconds']}s")
        if result["node_prompt_path"]:
            lines.append(f"  System prompt：`{result['node_prompt_path']}`")
        if result["pre_hook_path"]:
            lines.append(f"  Pre hook：`{result['pre_hook_path']}`")
        if result["post_hook_path"]:
            lines.append(f"  Post hook：`{result['post_hook_path']}`")
    for from_node_id, to_node_id in applied_edges:
        lines.append(f"- 已加入 edge：`{from_node_id} → {to_node_id}`")

    print("\n".join(lines))
    return 0


def _normalize_node_specs(spec: dict) -> list[dict]:
    raw_nodes = spec.get("nodes")
    if raw_nodes is None:
        return [spec]
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("多 node spec 的 nodes 必須是非空陣列")
    nodes = [node for node in raw_nodes if isinstance(node, dict)]
    if len(nodes) != len(raw_nodes):
        raise ValueError("nodes 陣列只能包含 object")
    return nodes


def _normalize_edges(spec: dict) -> list[tuple[str, str]]:
    raw_edges = spec.get("edges", [])
    if raw_edges in (None, ""):
        return []
    if not isinstance(raw_edges, list):
        raise ValueError("edges 必須是陣列")

    edges: list[tuple[str, str]] = []
    for raw_edge in raw_edges:
        if isinstance(raw_edge, dict):
            from_node_id = str(raw_edge.get("from_node_id") or raw_edge.get("from") or "").strip()
            to_node_id = str(raw_edge.get("to_node_id") or raw_edge.get("to") or "").strip()
        elif isinstance(raw_edge, list | tuple) and len(raw_edge) == 2:
            from_node_id = str(raw_edge[0]).strip()
            to_node_id = str(raw_edge[1]).strip()
        else:
            raise ValueError("edges 每個項目必須是 {from_node_id,to_node_id} 或 [from,to]")
        if not from_node_id or not to_node_id:
            raise ValueError("edges 不能有空的 from/to")
        edges.append((from_node_id, to_node_id))
    return edges


def _apply_node_spec(db_path: Path, spec: dict) -> dict[str, object]:
    node_id = str(spec.get("node_id", "")).strip()
    run_py_content = str(spec.get("run_py_content", "")).strip()

    if not node_id:
        raise ValueError("LLM 沒有提供 node_id")
    if not run_py_content:
        raise ValueError(f"Node `{node_id}` 沒有提供 run.py 內容")

    name = str(spec.get("name", node_id)).strip() or node_id
    description = str(spec.get("description", "")).strip()
    raw_model = spec.get("model_name")
    model_name: str | None = str(raw_model).strip() if raw_model else None
    timeout_seconds = int(spec.get("timeout_seconds", 120))
    use_prev_output = bool(spec.get("use_prev_output", True))
    node_md_content = str(spec.get("node_md_content", "")).strip()
    pre_hook_py_content = str(spec.get("pre_hook_py_content", "")).strip()
    post_hook_py_content = str(spec.get("post_hook_py_content", "")).strip()
    add_edge = bool(spec.get("add_edge_from_intent_router", True))

    existing = get_node(db_path, node_id)
    is_update = existing is not None

    node_dir = REPO_ROOT / "nodes" / node_id
    node_dir.mkdir(parents=True, exist_ok=True)

    run_py_path = node_dir / "run.py"
    run_py_path.write_text(run_py_content, encoding="utf-8")

    node_prompt_path = existing.node_prompt_path if existing else None
    if node_md_content:
        node_md_path = node_dir / "node.md"
        node_md_path.write_text(node_md_content, encoding="utf-8")
        node_prompt_path = f"nodes/{node_id}/node.md"

    pre_hook_path = existing.pre_hook_path if existing else None
    if pre_hook_py_content:
        (node_dir / "pre_hook.py").write_text(pre_hook_py_content, encoding="utf-8")
        pre_hook_path = f"nodes/{node_id}/pre_hook.py"

    post_hook_path = existing.post_hook_path if existing else None
    if post_hook_py_content:
        (node_dir / "post_hook.py").write_text(post_hook_py_content, encoding="utf-8")
        post_hook_path = f"nodes/{node_id}/post_hook.py"

    executor_path = f"nodes/{node_id}/run.py"
    new_node = WorkflowNode(
        id=node_id,
        name=name,
        description=description,
        model_name=model_name,
        start_node=existing.start_node if existing else False,
        enabled=existing.enabled if existing else True,
        executor_path=executor_path,
        pre_hook_path=pre_hook_path,
        post_hook_path=post_hook_path,
        node_prompt_path=node_prompt_path,
        use_prev_output=use_prev_output,
        timeout_seconds=timeout_seconds,
    )
    upsert_node(db_path, new_node)

    added_router_edge = False
    if add_edge and not is_update:
        upsert_edge(db_path, WorkflowEdge(id=0, from_node_id="intent-router", to_node_id=node_id))
        added_router_edge = True

    return {
        "node_id": node_id,
        "name": name,
        "is_update": is_update,
        "timeout_seconds": timeout_seconds,
        "node_prompt_path": node_prompt_path or "",
        "pre_hook_path": pre_hook_path or "",
        "post_hook_path": post_hook_path or "",
        "added_router_edge": added_router_edge,
    }


if __name__ == "__main__":
    sys.exit(main())
