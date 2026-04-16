"""Echo node executor — outputs the extracted text back to stdout.

Triggered by: '啟用echo node <text>'
Direct router extracts the text via named-group regex: (?P<text>.+)
Stdout is returned directly as the bot reply.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    if "--args-json" not in sys.argv:
        print("usage: run.py --args-json '{\"text\": \"...\"}'", file=sys.stderr)
        return 1

    idx = sys.argv.index("--args-json")
    payload: dict = json.loads(sys.argv[idx + 1])
    args = payload.get("args", {})
    if not isinstance(args, dict):
        args = {}
    text = str(
        payload.get("text")
        or args.get("text")
        or args.get("message")
        or payload.get("message")
        or ""
    ).strip()
    if not text:
        print("(empty echo)", file=sys.stderr)
        return 1

    print(json.dumps({"kind": "reply", "reply": text}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
