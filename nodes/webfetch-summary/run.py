"""Webfetch-summary node — passes fetched content to LLM for summarisation."""
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
    prev_output = str(payload.get("prev_output", "")).strip()

    if not prev_output:
        print(json.dumps({"kind": "reply", "reply": "沒有收到網頁內容，請先提供網址。"}, ensure_ascii=False))
        return 0

    run_output = json.dumps(
        {
            "user_instruction": message,
            "fetched_content": prev_output,
        },
        ensure_ascii=False,
        indent=2,
    )

    print(json.dumps(
        {
            "kind": "infer",
            "response_mode": "passthrough",
            "run_output": run_output,
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
