"""Run the finance report workflow as a node executor."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from threading import Semaphore


def main() -> int:
    node_dir = Path(__file__).resolve().parent
    if str(node_dir) not in sys.path:
        sys.path.insert(0, str(node_dir))
    repo_root = node_dir.parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from impl.cli import parse_cli_args
    from impl.config import load_configs
    from impl.logging_utils import get_logger, setup_logging
    from impl.runner import WHISPER_CONCURRENCY, main as run_finance_report, prepare_finance_report

    if "--args-json" in sys.argv:
        idx = sys.argv.index("--args-json")
        payload: dict = json.loads(sys.argv[idx + 1])
        prev_output = str(payload.get("prev_output", "")).strip()
        prev_payload = _parse_prev_output(prev_output)
        source = str(payload.get("source") or prev_payload.get("source", "")).strip()
        target_date = str(payload.get("target_date") or prev_payload.get("target_date", "")).strip()
        workers = int(payload.get("workers") or prev_payload.get("workers", 4) or 4)
        if workers <= 0:
            workers = 1
        node_prompt_path = str(payload.get("node_prompt_path", "")).strip()

        cli_args = parse_cli_args(
            [
                "--workers",
                str(workers),
                *(["--source", source] if source else []),
                *(["--node-prompt-path", node_prompt_path] if node_prompt_path else []),
                *([target_date] if target_date else []),
            ]
        )
        configs = load_configs(cli_args.source_id)
        if len(configs) != 1:
            print(json.dumps({"kind": "reply", "reply": "請先指定單一財經來源，再執行報告生成。"}, ensure_ascii=False))
            return 0

        config = configs[0]
        logger_name = f"finance_report.{config.source.slug}"
        setup_logging(config.log_dir, logger_name)
        logger = get_logger()
        prepared = prepare_finance_report(
            config=config,
            requested_target_date=cli_args.target_date,
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
                        "codex_output_path": str(prepared["codex_output_path"]),
                    },
                },
                ensure_ascii=False,
            )
        )
        return 0

    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="")
    parser.add_argument("--target-date", default="")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--list-sources", action="store_true")
    parser.add_argument("--node-prompt-path", default="")
    parser.add_argument("--notify-discord", action="store_true")
    parser.add_argument("--channel-id", default="")
    parser.add_argument("target_date_positional", nargs="?", default="")
    args = parser.parse_args()

    argv: list[str] = []
    if args.list_sources:
        argv.append("--list-sources")
    else:
        argv.extend(["--workers", str(args.workers)])
        if args.source:
            argv.extend(["--source", args.source])
        if args.node_prompt_path:
            argv.extend(["--node-prompt-path", args.node_prompt_path])
        if args.notify_discord:
            argv.append("--notify-discord")
        if args.channel_id:
            argv.extend(["--channel-id", args.channel_id])
        target_date = args.target_date or args.target_date_positional
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
