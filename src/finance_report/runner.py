"""RSS entrypoint for the finance report pipeline."""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Semaphore

from dotenv import load_dotenv

from .analyze import analyze_transcript
from .cli import parse_cli_args
from .config import FinanceConfig, list_available_sources, load_configs
from .env_guard import assert_clean_pythonpath
from .fetcher import EpisodeNotFoundError, download_episode_media, resolve_episode
from .logging_utils import get_logger, set_current_logger, setup_logging
from .notify import send_markdown_message
from .transcribe import transcribe_video

WHISPER_CONCURRENCY = 1
CODEX_CONCURRENCY = 4


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    assert_clean_pythonpath()

    cli_args = parse_cli_args(argv or sys.argv[1:])
    if cli_args.list_sources:
        for source in list_available_sources():
            author = f" | author={source.author}" if source.author else ""
            print(f"{source.source_id} | title={source.title}{author} | rss={source.rss_url}", flush=True)
        return

    bot_token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("DISCORD_BOT_TOKEN not set")

    repo_root = Path(__file__).resolve().parents[2]
    configs = load_configs(cli_args.source_id)
    worker_count = min(cli_args.workers, len(configs))
    print(f"[finance] processing {len(configs)} source(s) with {worker_count} worker(s)", flush=True)
    whisper_slots = Semaphore(WHISPER_CONCURRENCY)
    codex_slots = Semaphore(CODEX_CONCURRENCY)

    had_error = False
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="finance") as executor:
        futures = [
            executor.submit(
                _process_source,
                config,
                cli_args.target_date,
                bot_token,
                repo_root,
                whisper_slots,
                codex_slots,
            )
            for config in configs
        ]
        for future in as_completed(futures):
            try:
                print(future.result(), flush=True)
            except Exception as exc:
                had_error = True
                print(f"[finance] {exc}", file=sys.stderr, flush=True)

    if had_error:
        raise RuntimeError("one or more finance sources failed")


def _process_source(
    config: FinanceConfig,
    requested_target_date,
    bot_token: str,
    repo_root: Path,
    whisper_slots: Semaphore,
    codex_slots: Semaphore,
) -> str:
    config.ensure_directories()
    logger_name = f"finance_report.{config.source.slug}"
    set_current_logger(logger_name)
    log_path = setup_logging(config.log_dir, logger_name)
    logger = get_logger()
    logger.info(
        "Finance report started for source=%s requested_target_date=%s",
        config.source.source_id,
        requested_target_date.isoformat() if requested_target_date else "(latest)",
    )
    logger.info("Log file: %s", log_path)

    try:
        selection = resolve_episode(config, requested_target_date)
        note_path = config.note_path_for(selection.target_date)
        transcript_path = config.transcript_path_for(selection.target_date)
        codex_output_path = config.codex_output_path_for(selection.target_date)

        if note_path.exists():
            logger.info("Note already exists; sending without reprocessing: %s", note_path)
            markdown = note_path.read_text(encoding="utf-8").strip()
            send_markdown_message(
                bot_token,
                config.channel_id,
                _format_discord_message(config.source.title, selection.target_date.isoformat(), markdown),
            )
            return f"[finance] reused existing note for {config.source.source_id}: {note_path}"

        logger.info("Starting media download stage")
        result = download_episode_media(config, selection)
        logger.info("Download stage completed: %s", result.media_path)
        logger.info("Waiting for Whisper slot")
        with whisper_slots:
            logger.info("Starting transcription stage with model %s", config.whisper_model)
            transcribe_video(result.media_path, transcript_path, config.whisper_model)
        logger.info("Transcription completed: %s", transcript_path)
        logger.info("Waiting for Codex slot")
        with codex_slots:
            logger.info("Starting analysis stage")
            markdown = analyze_transcript(
                transcript_path=transcript_path,
                note_path=note_path,
                codex_output_path=codex_output_path,
                target_date=selection.target_date,
                codex_model=config.codex_model,
                repo_root=repo_root,
                source_title=config.source.title,
                source_author=config.source.author,
            )
        logger.info("Analysis completed: %s", note_path)
        logger.info("Sending Discord notification")
        send_markdown_message(
            bot_token,
            config.channel_id,
            _format_discord_message(config.source.title, selection.target_date.isoformat(), markdown),
        )
        logger.info("Discord notification sent to channel %s", config.channel_id)
        return f"[finance] note written to {note_path}"
    except EpisodeNotFoundError as exc:
        logger.exception("No matching episode was found")
        if config.notify_on_no_episode:
            send_markdown_message(
                bot_token,
                config.channel_id,
                f"【{config.source.title}】指定日期沒有找到可處理的新集數。({exc})",
            )
        raise RuntimeError(f"{config.source.source_id}: no matching episode") from exc
    except Exception as exc:
        logger.exception("Finance report failed")
        send_markdown_message(
            bot_token,
            config.channel_id,
            f"【{config.source.title}】每日財經報告失敗：{type(exc).__name__}。請查看本地 log 後重試。",
        )
        raise RuntimeError(f"{config.source.source_id}: {type(exc).__name__}") from exc


def _format_discord_message(source_title: str, target_date: str, markdown: str) -> str:
    header = f"【{source_title}｜{target_date}】"
    content = markdown.strip()
    if content.startswith(header):
        return content
    return f"{header}\n\n{content}"


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[finance] {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
