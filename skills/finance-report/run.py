"""Run the finance report workflow as a skill action."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    # --args-json protocol: engine passes a JSON blob as the sole argument.
    # Fall back to individual flags for backward compatibility (e.g. `just finance-report`).
    if "--args-json" in sys.argv:
        idx = sys.argv.index("--args-json")
        payload: dict = json.loads(sys.argv[idx + 1])
        source = str(payload.get("source", "")).strip()
        target_date = str(payload.get("target_date", "")).strip()
        workers = int(payload.get("workers", 4))
        list_sources = bool(payload.get("list_sources", False))
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument("--source", default="")
        parser.add_argument("--target-date", default="")
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--list-sources", action="store_true")
        parsed = parser.parse_args()
        source = parsed.source
        target_date = parsed.target_date
        workers = parsed.workers
        list_sources = parsed.list_sources

    repo_root = Path(__file__).resolve().parents[2]
    cmd = ["python", "-m", "src.finance_report.runner"]
    if list_sources:
        cmd.append("--list-sources")
    else:
        cmd.extend(["--workers", str(workers)])
        if source:
            cmd.extend(["--source", source])
        if target_date:
            cmd.append(target_date)

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
