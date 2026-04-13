"""General reply node executor."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.bot.skills import render_general_reply


def main() -> int:
    if "--args-json" not in sys.argv:
        print("usage: run.py --args-json '{\"message\": \"...\"}'", file=sys.stderr)
        return 1

    idx = sys.argv.index("--args-json")
    payload: dict = json.loads(sys.argv[idx + 1])
    message = str(payload.get("message", "")).strip()
    recent_context = str(payload.get("recent_context", "")).strip()
    system_prompt_path = str(payload.get("system_prompt_path", "")).strip() or None
    print(
        render_general_reply(
            message,
            recent_context=recent_context,
            system_prompt_path=system_prompt_path,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
