"""Helpers for preparing and saving finance report digest prompts."""
from __future__ import annotations

from datetime import date
from pathlib import Path


PROMPT_TEMPLATE_PATH = Path(__file__).with_name("prompt").joinpath("finance_report_analysis.md")


def build_analysis_task_prompt(
    *,
    transcript_path: Path,
    note_path: Path,
    target_date: date,
    source_title: str,
    source_author: str,
) -> str:
    prompt_template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    return prompt_template.format(
        transcript_path=transcript_path,
        note_date=target_date.isoformat(),
        note_path=note_path,
        source_title=source_title,
        source_author=source_author or "未提供",
    )


def build_analysis_run_output(
    *,
    transcript_path: Path,
    note_path: Path,
    source_title: str,
    source_author: str,
    target_date: date,
    audio_duration_seconds: float = 0.0,
) -> str:
    lines = [
        "transcription_completed=true",
        f"transcript_path={transcript_path}",
        f"note_path={note_path}",
        f"source_title={source_title}",
        f"source_author={source_author or '未提供'}",
        f"target_date={target_date.isoformat()}",
    ]
    duration_str = format_audio_duration(audio_duration_seconds)
    if duration_str:
        lines.append(f"audio_duration={duration_str} ({int(audio_duration_seconds)}s)")
    return "\n".join(lines)


def format_audio_duration(audio_duration_seconds: float) -> str:
    if audio_duration_seconds <= 0:
        return ""
    minutes, seconds = divmod(int(audio_duration_seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{seconds:02d}s"
    return f"{minutes}m{seconds:02d}s"


def save_markdown_outputs(markdown: str, *, note_path: Path, codex_output_path: Path) -> None:
    content = markdown.rstrip() + "\n"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    codex_output_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(content, encoding="utf-8")
    codex_output_path.write_text(content, encoding="utf-8")
