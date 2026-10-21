"""Infer Bose-channel commands with the node's model and Max reasoning."""
from __future__ import annotations

import json
import sys


def main() -> int:
    index = sys.argv.index("--args-json")
    payload = json.loads(sys.argv[index + 1])
    print(json.dumps({
        "kind": "infer",
        "response_mode": "decision",
        "run_output": json.dumps({
            "bose_control_channel": True,
            "channel_id": str(payload.get("channel_id", "")),
            "recent_context": str(payload.get("recent_context", "")),
        }, ensure_ascii=False),
        "metadata": {"node_kind": "bose-router", "reasoning_effort": "max"},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
