"""Node Creator executor — lets LLM generate or modify workflow nodes via chat."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.bot.workflow_db import load_workflow_graph

_PENDING_SPEC_PATH = "nodes/node-creator/.pending_spec.json"


def main() -> int:
    if "--args-json" not in sys.argv:
        print("usage: run.py --args-json '{\"message\": \"...\"}'", file=sys.stderr)
        return 1

    idx = sys.argv.index("--args-json")
    payload: dict = json.loads(sys.argv[idx + 1])
    message = str(payload.get("message", "")).strip()
    recent_context = str(payload.get("recent_context", "")).strip()

    db_path = REPO_ROOT / "db" / "workflow.sqlite3"
    graph = load_workflow_graph(db_path)

    existing_nodes = [
        {
            "id": n.id,
            "name": n.name,
            "description": n.description,
            "executor_path": n.executor_path,
            "pre_hook_path": n.pre_hook_path or "",
            "post_hook_path": n.post_hook_path or "",
            "enabled": n.enabled,
            "timeout_seconds": n.timeout_seconds,
        }
        for n in graph.nodes
    ]

    update_target_id = _detect_update_target(message, graph)
    existing_run_py = ""
    existing_pre_hook_py = ""
    existing_post_hook_py = ""
    if update_target_id:
        run_py_path = REPO_ROOT / f"nodes/{update_target_id}/run.py"
        if run_py_path.exists():
            existing_run_py = run_py_path.read_text(encoding="utf-8")
        pre_hook_path = REPO_ROOT / f"nodes/{update_target_id}/pre_hook.py"
        if pre_hook_path.exists():
            existing_pre_hook_py = pre_hook_path.read_text(encoding="utf-8")
        post_hook_path = REPO_ROOT / f"nodes/{update_target_id}/post_hook.py"
        if post_hook_path.exists():
            existing_post_hook_py = post_hook_path.read_text(encoding="utf-8")

    run_output = json.dumps(
        {
            "user_intent": message,
            "recent_context": recent_context,
            "action": "update" if update_target_id else "create",
            "update_target_id": update_target_id or "",
            "existing_run_py": existing_run_py,
            "existing_pre_hook_py": existing_pre_hook_py,
            "existing_post_hook_py": existing_post_hook_py,
            "existing_nodes": existing_nodes,
        },
        ensure_ascii=False,
        indent=2,
    )

    print(
        json.dumps(
            {
                "kind": "infer",
                "response_mode": "passthrough",
                "run_output": run_output,
                "output_path": _PENDING_SPEC_PATH,
                "metadata": {
                    "node_kind": "node-creator",
                    "pending_spec_path": _PENDING_SPEC_PATH,
                    "action": "update" if update_target_id else "create",
                    "update_target_id": update_target_id or "",
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


def _detect_update_target(message: str, graph) -> str | None:
    """Return a node id if the user message clearly references an existing node for update/fix."""
    update_keywords = ["修改", "更新", "update", "modify", "fix", "改", "調整", "edit"]
    msg_lower = message.lower()
    if not any(kw in msg_lower for kw in update_keywords):
        return None
    for node in graph.nodes:
        if node.id.lower() in msg_lower or node.name.lower() in msg_lower:
            return node.id
    return None


if __name__ == "__main__":
    sys.exit(main())
