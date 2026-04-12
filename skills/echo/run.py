"""Echo skill — outputs the extracted text back to stdout.

Triggered by: '啟用echo skill <text>'
Pass 1 router extracts the text via named-group regex: (?P<text>.+)
Pass 2 mode: never (stdout is returned directly as the bot reply)
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
    text = str(payload.get("text", "")).strip()
    if not text:
        print("(empty echo)", file=sys.stderr)
        return 1

    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
