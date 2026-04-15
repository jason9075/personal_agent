"""Run the finance report workflow as a node executor."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from threading import Semaphore


def main() -> int:
    node_dir = Path(__file__).resolve().parent
    if str(node_dir) not in sys.path:
        sys.path.insert(0, str(node_dir))
    repo_root = node_dir.parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from impl.config import load_configs
    from impl.logging_utils import get_logger, setup_logging
    from impl.runner import WHISPER_CONCURRENCY, prepare_finance_report

    if "--args-json" not in sys.argv:
        print("usage: run.py --args-json '{...}'", file=sys.stderr)
        return 1

    idx = sys.argv.index("--args-json")
    payload: dict = json.loads(sys.argv[idx + 1])
    prev_output = str(payload.get("prev_output", "")).strip()
    prev_payload = _parse_prev_output(prev_output)
    source = str(payload.get("source") or prev_payload.get("source", "")).strip()
    target_date_str = str(payload.get("target_date") or prev_payload.get("target_date", "")).strip()
    workers = int(payload.get("workers") or prev_payload.get("workers", 4) or 4)
    if workers <= 0:
        workers = 1

    configs = load_configs(source)
    if len(configs) != 1:
        print(json.dumps({"kind": "reply", "reply": "請先指定單一財經來源，再執行報告生成。"}, ensure_ascii=False))
        return 0

    config = configs[0]
    setup_logging(config.log_dir, f"finance_report.{config.source.slug}")
    logger = get_logger()
    prepared = prepare_finance_report(
        config=config,
        requested_target_date=_parse_date(target_date_str),
        whisper_slots=Semaphore(WHISPER_CONCURRENCY),
    )
    existing_message = str(prepared.get("existing_message", "")).strip()
    if existing_message:
        print(json.dumps({"kind": "reply", "reply": existing_message}, ensure_ascii=False))
        return 0

    logger.info("Prepared finance-report llm request source=%s target_date=%s", config.source.source_id, prepared["target_date"])
    print(
        json.dumps(
            {
                "kind": "infer",
                "response_mode": "passthrough",
                "run_output": str(prepared["run_output"]),
                "task_prompt": str(prepared["task_prompt"]),
                "output_path": str(prepared["note_path"]),
                "metadata": {
                    "source_id": str(prepared["source_id"]),
                    "target_date": str(prepared["target_date"]),
                    "audio_duration": str(prepared.get("audio_duration", "")),
                    "codex_output_path": str(prepared["codex_output_path"]),
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    if len(value) == 8 and value.isdigit():
        return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        return date.fromisoformat(value)
    return None


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
