"""Run the finance report workflow as a skill action."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="")
    parser.add_argument("--target-date", default="")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--list-sources", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    cmd = ["python", "-m", "src.finance_report.runner"]
    if args.list_sources:
        cmd.append("--list-sources")
    else:
        cmd.extend(["--workers", str(args.workers)])
        if args.source:
            cmd.extend(["--source", args.source])
        if args.target_date:
            cmd.append(args.target_date)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=False,
    )
    output = result.stdout.strip() or result.stderr.strip() or "(no output)"
    print(output)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
