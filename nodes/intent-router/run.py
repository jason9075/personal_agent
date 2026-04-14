"""Intent router node executor."""
from __future__ import annotations

import json
import sys


def main() -> int:
    if "--args-json" not in sys.argv:
        print("usage: run.py --args-json '{\"message\": \"...\"}'", file=sys.stderr)
        return 1

    idx = sys.argv.index("--args-json")
    payload: dict = json.loads(sys.argv[idx + 1])
    message = str(payload.get("message", "")).strip()
    recent_context = str(payload.get("recent_context", "")).strip()
    run_output = json.dumps(
        {
            "recent_context": recent_context or "",
        },
        ensure_ascii=False,
        indent=2,
    )
    print(
        json.dumps(
            {
                "kind": "llm_request",
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
