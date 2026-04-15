"""Local Whisper transcription for downloaded finance media."""
from __future__ import annotations

import subprocess
from pathlib import Path

import whisper

from .logging_utils import get_logger


def get_audio_duration(media_path: Path) -> float:
    """Return audio duration in seconds using ffprobe, or 0.0 on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(media_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def transcribe_video(video_path: Path, transcript_path: Path, model_name: str) -> str:
    logger = get_logger()
    transcript_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Loading Whisper model: %s", model_name)
    model = whisper.load_model(model_name)
    logger.info("Beginning transcription for %s", video_path)
    result = model.transcribe(str(video_path), fp16=False, verbose=False)
    text = result["text"].strip()
    transcript_path.write_text(text + "\n", encoding="utf-8")
    logger.info("Transcript written to %s (%s chars)", transcript_path, len(text))
    return text
