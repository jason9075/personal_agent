"""Run the finance report workflow as a node executor."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    # --args-json protocol: engine passes a JSON blob as the sole argument.
    # Fall back to individual flags for backward compatibility (e.g. `just finance-report`).
    if "--args-json" in sys.argv:
        idx = sys.argv.index("--args-json")
        payload: dict = json.loads(sys.argv[idx + 1])
        prev_output = str(payload.get("prev_output", "")).strip()
        prev_payload = _parse_prev_output(prev_output)
        source = str(payload.get("source") or prev_payload.get("source", "")).strip()
        target_date = str(payload.get("target_date") or prev_payload.get("target_date", "")).strip()
        workers = int(payload.get("workers") or prev_payload.get("workers", 4) or 4)
        list_sources = bool(payload.get("list_sources", False))
        node_prompt_path = str(payload.get("node_prompt_path", "")).strip()
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument("--source", default="")
        parser.add_argument("--target-date", default="")
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--list-sources", action="store_true")
        parser.add_argument("--node-prompt-path", default="")
        parsed = parser.parse_args()
        source = parsed.source
        target_date = parsed.target_date
        workers = parsed.workers
        list_sources = parsed.list_sources
        node_prompt_path = parsed.node_prompt_path

    node_dir = Path(__file__).resolve().parent
    if str(node_dir) not in sys.path:
        sys.path.insert(0, str(node_dir))
    from impl.runner import main as run_finance_report

    argv: list[str] = []
    if list_sources:
        argv.append("--list-sources")
    else:
        argv.extend(["--workers", str(workers)])
        if source:
            argv.extend(["--source", source])
        if node_prompt_path:
            argv.extend(["--node-prompt-path", node_prompt_path])
        if target_date:
            argv.append(target_date)

    try:
        run_finance_report(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    return 0

def _parse_prev_output(prev_output: str) -> dict:
    if not prev_output:
        return {}
    try:
        parsed = json.loads(prev_output)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


if __name__ == "__main__":
    sys.exit(main())
