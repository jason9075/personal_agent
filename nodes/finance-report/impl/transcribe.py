"""Local Whisper transcription for downloaded finance media."""
from __future__ import annotations

from pathlib import Path

import whisper

from .logging_utils import get_logger


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
