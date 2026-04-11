"""Run finance schedule management actions as a skill action."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.bot.skills import execute_schedule_action


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", default="list")
    parser.add_argument("--id", type=int, default=0)
    parser.add_argument("--name", default="")
    parser.add_argument("--cron", default="")
    parser.add_argument("--source", default="")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--channel", default="")
    args = parser.parse_args()

    payload: dict[str, object] = {
        "action": args.action,
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

    print(execute_schedule_action(payload, channel_id=args.channel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
