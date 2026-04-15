"""Post-hook for node-creator — applies the LLM-generated node spec to disk and DB."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.bot.workflow_db import WorkflowEdge, WorkflowNode, get_node, upsert_edge, upsert_node


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

    node_id = str(spec.get("node_id", "")).strip()
    run_py_content = str(spec.get("run_py_content", "")).strip()

    if not node_id:
        pending_spec_path.unlink(missing_ok=True)
        print("Node creator: LLM 沒有提供 node_id。請再試一次。")
        return 0

    if not run_py_content:
        pending_spec_path.unlink(missing_ok=True)
        print("Node creator: LLM 沒有提供 run.py 內容。請再試一次。")
        return 0

    name = str(spec.get("name", node_id)).strip() or node_id
    description = str(spec.get("description", "")).strip()
    raw_model = spec.get("model_name")
    model_name: str | None = str(raw_model).strip() if raw_model else None
    timeout_seconds = int(spec.get("timeout_seconds", 120))
    use_prev_output = bool(spec.get("use_prev_output", True))
    system_md_content = str(spec.get("system_md_content", "")).strip()
    pre_hook_py_content = str(spec.get("pre_hook_py_content", "")).strip()
    post_hook_py_content = str(spec.get("post_hook_py_content", "")).strip()
    add_edge = bool(spec.get("add_edge_from_intent_router", True))

    db_path = REPO_ROOT / "db" / "workflow.sqlite3"
    is_update = get_node(db_path, node_id) is not None

    # Write node files
    node_dir = REPO_ROOT / "nodes" / node_id
    node_dir.mkdir(parents=True, exist_ok=True)

    run_py_path = node_dir / "run.py"
    run_py_path.write_text(run_py_content, encoding="utf-8")

    node_prompt_path: str | None = None
    if system_md_content:
        system_md_path = node_dir / "node.md"
        system_md_path.write_text(system_md_content, encoding="utf-8")
        node_prompt_path = f"nodes/{node_id}/node.md"

    pre_hook_path: str | None = None
    if pre_hook_py_content:
        (node_dir / "pre_hook.py").write_text(pre_hook_py_content, encoding="utf-8")
        pre_hook_path = f"nodes/{node_id}/pre_hook.py"

    post_hook_path: str | None = None
    if post_hook_py_content:
        (node_dir / "post_hook.py").write_text(post_hook_py_content, encoding="utf-8")
        post_hook_path = f"nodes/{node_id}/post_hook.py"

    executor_path = f"nodes/{node_id}/run.py"

    # Upsert node in DB (preserves start_node / enabled for existing nodes)
    existing = get_node(db_path, node_id)
    new_node = WorkflowNode(
        id=node_id,
        name=name,
        description=description,
        model_name=model_name,
        start_node=existing.start_node if existing else False,
        enabled=existing.enabled if existing else True,
        executor_path=executor_path,
        pre_hook_path=pre_hook_path if pre_hook_py_content else (existing.pre_hook_path if existing else None),
        post_hook_path=post_hook_path if post_hook_py_content else (existing.post_hook_path if existing else None),
        node_prompt_path=node_prompt_path,
        use_prev_output=use_prev_output,
        timeout_seconds=timeout_seconds,
    )
    upsert_node(db_path, new_node)

    # Add edge from intent-router if requested and not already present
    if add_edge and not is_update:
        edge = WorkflowEdge(id=0, from_node_id="intent-router", to_node_id=node_id)
        upsert_edge(db_path, edge)

    pending_spec_path.unlink(missing_ok=True)

    # Plain-text confirmation (engine returns this as-is to Discord)
    action_label = "更新" if is_update else "新增"
    lines = [f"✅ Node `{node_id}` 已{action_label}！"]
    lines.append(f"- 名稱：{name}")
    if description:
        lines.append(f"- 描述：{description}")
    lines.append(f"- 執行器：`{executor_path}`")
    if pre_hook_path:
        lines.append(f"- Pre hook：`{pre_hook_path}`")
    if post_hook_path:
        lines.append(f"- Post hook：`{post_hook_path}`")
    lines.append(f"- Timeout：{timeout_seconds}s")
    if node_prompt_path:
        lines.append(f"- System prompt：`{node_prompt_path}`")
    if add_edge and not is_update:
        lines.append(f"- 已加入 edge：`intent-router → {node_id}`")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
