"""Run generic schedule management actions as a node executor."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.bot.nodes import execute_schedule_action  # noqa: E402


def main() -> int:
    if "--args-json" in sys.argv:
        idx = sys.argv.index("--args-json")
        payload: dict[str, object] = json.loads(sys.argv[idx + 1])
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument("--action", default="list")
        parser.add_argument("--id", type=int, default=0)
        parser.add_argument("--name", default="")
        parser.add_argument("--cron", default="")
        parser.add_argument("--job-type", default="finance-report")
        parser.add_argument("--task-message", default="")
        parser.add_argument("--source", default="")
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--channel", default="")
        parser.add_argument("--run-once", action="store_true")
        args = parser.parse_args()
        payload = {
            "action": args.action,
            "job_type": args.job_type,
            "task_message": args.task_message,
            "run_once": args.run_once,
        }
        if args.id:
            payload["id"] = args.id
        if args.name:
            payload["name"] = args.name
        if args.cron:
            payload["cron"] = args.cron
        if args.source:
            payload["source"] = args.source
        if args.workers:
            payload["workers"] = args.workers
        channel_id = args.channel
        print(execute_schedule_action(payload, channel_id=channel_id))
        return 0

    channel_id = str(payload.pop("channel_id", "")).strip()
    reply = execute_schedule_action(payload, channel_id=channel_id)
    print(json.dumps({"kind": "reply", "reply": reply}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
