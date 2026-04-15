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

_NODE_PROTOCOL_DOC = """\
## Node 協議

每個 Node 是一個 Python 腳本（`nodes/<node_id>/run.py`），透過 `--args-json` 接收 JSON 參數並將結果輸出到 stdout。

### 標準 run.py 骨架

```python
from __future__ import annotations
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

def main() -> int:
    if "--args-json" not in sys.argv:
        print("usage: run.py --args-json '{...}'", file=sys.stderr)
        return 1
    idx = sys.argv.index("--args-json")
    payload: dict = json.loads(sys.argv[idx + 1])
    message = str(payload.get("message", "")).strip()
    # ... 你的邏輯 ...
    print(json.dumps({"kind": "reply", "reply": "..."}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

### Engine 自動注入的參數

- `message`: 使用者訊息
- `model_name`: 模型名稱（來自 DB）
- `node_prompt_path`: 節點 prompt 路徑（來自 DB）
- `prev_output`: 前一個 node 的輸出（若 use_prev_output=true）
- `recent_context`: 最近對話脈絡
- `next_nodes`: 可達下一個節點清單 `[{"id":"...","name":"...","description":"..."}]`

### 輸出格式

**直接回覆 (tool/utility node)：**
```python
print(json.dumps({"kind": "reply", "reply": "回覆內容"}, ensure_ascii=False))
```

**要求 LLM 做決策 (router node)：**
```python
print(json.dumps({
    "kind": "infer",
    "response_mode": "decision",
    "run_output": json.dumps({...}, ensure_ascii=False, indent=2),
    "default_args": {},
    "metadata": {"fallback_reply": "無法判斷，請重試。"},
}, ensure_ascii=False))
```

**要求 LLM 生成內容 (agent node)：**
```python
print(json.dumps({
    "kind": "infer",
    "response_mode": "passthrough",
    "run_output": "給 LLM 的資訊",
    "task_prompt": "具體任務指示",
    "output_path": "nodes/<id>/output.md",  # LLM 輸出會寫入此路徑
    "metadata": {},
}, ensure_ascii=False))
```

### Pre/Post Hook（選用）

每個 node 可在 `run.py` 前後各有一個 hook 腳本：

- `pre_hook.py` — 在 `run.py` **之前**執行，可做準備工作（下載資料、暖機等）
  - 接收與 `run.py` 相同的 `--args-json` payload
  - stdout 不影響 workflow，僅用於副作用
- `post_hook.py` — 在 `run.py` 和 LLM 推論**之後**執行，可做後處理或傳送通知
  - payload 包含：`input`（原始 args）、`prev_output`、`stdout`（run.py stdout）、`stderr`、`returncode`
  - 若 post_hook.py 有 stdout 輸出，引擎會以此作為最終回覆取代 run.py 的輸出

Hook 骨架（與 run.py 相同協議）：
```python
from __future__ import annotations
import json, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

def main() -> int:
    idx = sys.argv.index("--args-json")
    payload: dict = json.loads(sys.argv[idx + 1])
    # ... 你的邏輯 ...
    return 0

if __name__ == "__main__":
    sys.exit(main())
```
"""

_TASK_PROMPT = """\
這是一個 passthrough 任務，不需要 decision JSON。

根據使用者需求和現有節點資訊，生成一個符合協議的新 Workflow Node 定義。

你必須「只」輸出一個合法 JSON 物件（不要有任何說明文字、markdown 標記、程式碼區塊圍欄）：

{
  "node_id": "kebab-case-id",
  "name": "節點顯示名稱（英文或中文）",
  "description": "節點功能說明（30字以內，供路由 LLM 判斷）",
  "run_py_content": "完整 Python run.py 程式碼字串",
  "pre_hook_py_content": "",
  "post_hook_py_content": "",
  "system_md_content": "",
  "model_name": "gpt-5.4",
  "timeout_seconds": 120,
  "use_prev_output": true,
  "add_edge_from_intent_router": true
}

規則：
- run_py_content 必須是完整、可執行的 Python 程式碼（使用 \\n 換行）
- pre_hook_py_content：若需要在 run.py 前執行的準備邏輯，填入完整 pre_hook.py 程式碼；否則留空字串
- post_hook_py_content：若需要在 LLM 推論後執行後處理（如傳送通知、寫入檔案），填入完整 post_hook.py 程式碼；否則留空字串
- 若 node 使用 kind="infer"，在 system_md_content 提供 LLM system prompt
- 若 node 不使用 LLM，model_name 設為 null，system_md_content 留空字串
- add_edge_from_intent_router=true 才能從主路由觸發此節點
- 若是更新現有節點，node_id 必須與現有節點 id 完全一致
- 只輸出 JSON，不輸出任何其他內容
"""


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
            "node_protocol": _NODE_PROTOCOL_DOC,
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
                "task_prompt": _TASK_PROMPT,
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
