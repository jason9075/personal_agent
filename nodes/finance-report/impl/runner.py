"""Finance report preparation pipeline (engine-owned LLM call)."""
from __future__ import annotations

from pathlib import Path
from threading import Semaphore

from .analyze import build_analysis_run_output, build_analysis_task_prompt, format_audio_duration
from .config import FinanceConfig
from .fetcher import download_episode_media, resolve_episode
from .logging_utils import get_logger
from .transcribe import get_audio_duration, transcribe_video

WHISPER_CONCURRENCY = 1
CODEX_CONCURRENCY = 4


class FinanceReportPrepared(dict):
    """Prepared context for an engine-owned finance digest LLM call."""


def prepare_finance_report(
    *,
    config: FinanceConfig,
    requested_target_date,
    whisper_slots: Semaphore,
) -> FinanceReportPrepared:
    logger = get_logger()
    selection = resolve_episode(config, requested_target_date)
    note_path = config.note_path_for(selection.target_date)
    transcript_path = config.transcript_path_for(selection.target_date)
    codex_output_path = config.codex_output_path_for(selection.target_date)

    if note_path.exists():
        logger.info("Note already exists; returning cached note: %s", note_path)
        markdown = note_path.read_text(encoding="utf-8").strip()
        audio_duration = _cached_audio_duration(config.download_dir, selection.target_date.isoformat())
        return FinanceReportPrepared(
            audio_duration=format_audio_duration(audio_duration),
            existing_message=_format_discord_message(
                config.source.title,
                selection.target_date.isoformat(),
                markdown,
                audio_duration=format_audio_duration(audio_duration),
            ),
        )

    logger.info("Starting media download stage")
    result = download_episode_media(config, selection)
    logger.info("Download stage completed: %s", result.media_path)
    audio_duration = get_audio_duration(result.media_path)
    logger.info("Audio duration: %.1f seconds", audio_duration)
    logger.info("Waiting for Whisper slot")
    with whisper_slots:
        logger.info("Starting transcription stage with model %s", config.whisper_model)
        transcribe_video(result.media_path, transcript_path, config.whisper_model)
    logger.info("Transcription completed: %s", transcript_path)
    return FinanceReportPrepared(
        source_id=config.source.source_id,
        source_title=config.source.title,
        source_author=config.source.author or "未提供",
        target_date=selection.target_date.isoformat(),
        audio_duration=format_audio_duration(audio_duration),
        transcript_path=str(transcript_path),
        note_path=str(note_path),
        codex_output_path=str(codex_output_path),
        run_output=build_analysis_run_output(
            transcript_path=transcript_path,
            note_path=note_path,
            source_title=config.source.title,
            source_author=config.source.author,
            target_date=selection.target_date,
            audio_duration_seconds=audio_duration,
        ),
        task_prompt=build_analysis_task_prompt(
            transcript_path=transcript_path,
            note_path=note_path,
            target_date=selection.target_date,
            source_title=config.source.title,
            source_author=config.source.author,
        ),
    )


def _cached_audio_duration(download_dir: Path, target_date: str) -> float:
    for media_path in sorted(download_dir.glob(f"finance_{target_date}.*")):
        duration = get_audio_duration(media_path)
        if duration > 0:
            return duration
    return 0.0


def _format_discord_message(source_title: str, target_date: str, markdown: str, *, audio_duration: str = "") -> str:
    header = f"【{source_title}｜{target_date}】"
    duration_line = f"音檔時長：{audio_duration}" if audio_duration else ""
    content = markdown.strip()
    if duration_line and duration_line not in content:
        content = f"{duration_line}\n\n{content}"
    if content.startswith(header):
        return content
    return f"{header}\n\n{content}"
