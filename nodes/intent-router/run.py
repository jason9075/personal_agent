"""Intent router node executor."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.bot.config import BOSE_CONTROL_CHANNEL_ID  # noqa: E402


def main() -> int:
    if "--args-json" not in sys.argv:
        print("usage: run.py --args-json '{\"message\": \"...\"}'", file=sys.stderr)
        return 1

    idx = sys.argv.index("--args-json")
    payload: dict = json.loads(sys.argv[idx + 1])
    args = payload.get("args", {})
    if not isinstance(args, dict):
        args = {}
    message = str(args.get("message") or payload.get("message", "")).strip()
    next_nodes = payload.get("next_nodes", [])
    if (str(payload.get("channel_id", "")) == BOSE_CONTROL_CHANNEL_ID
            and any(isinstance(node, dict) and node.get("id") == "bose-router" for node in next_nodes)):
        print(json.dumps({
            "kind": "reply", "decision": "use_next_node", "next_node_id": "bose-router",
            "args": args,
        }, ensure_ascii=False))
        return 0
    recent_context = str(payload.get("recent_context", "")).strip()
    run_output = json.dumps(
        {
            "recent_context": recent_context or "",
            "channel_id": str(payload.get("channel_id", "")),
            "bose_control_channel": str(payload.get("channel_id", "")) == BOSE_CONTROL_CHANNEL_ID,
        },
        ensure_ascii=False,
        indent=2,
    )
    print(
        json.dumps(
            {
                "kind": "infer",
                "response_mode": "decision",
                "run_output": run_output,
                "metadata": {
                    "node_kind": "intent-router",
                    "message": message[:200],
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
