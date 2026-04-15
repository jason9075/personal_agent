"""YT Fetch node — downloads YouTube audio and transcribes with Whisper.

Transcripts are cached under .local/yt/<video_id>/ so repeated requests for
the same video skip the download and transcription steps.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_CACHE_DIR = REPO_ROOT / ".local" / "yt"

_YT_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:"
    r"youtube\.com/(?:watch\?(?:.*&)?v=|shorts/)|"
    r"youtu\.be/"
    r")([a-zA-Z0-9_-]{11})"
)

_WHISPER_MODEL = "base"


def main() -> int:
    if "--args-json" not in sys.argv:
        print("usage: run.py --args-json '{...}'", file=sys.stderr)
        return 1

    idx = sys.argv.index("--args-json")
    payload: dict = json.loads(sys.argv[idx + 1])
    message = str(payload.get("message", "")).strip()
    prev_output = str(payload.get("prev_output", "")).strip()

    match = _YT_URL_RE.search(message) or _YT_URL_RE.search(prev_output)
    if not match:
        print(json.dumps({"kind": "reply", "reply": "請提供 YouTube 網址。"}, ensure_ascii=False))
        return 0

    video_id = match.group(1)
    url = f"https://www.youtube.com/watch?v={video_id}"
    video_dir = _CACHE_DIR / video_id
    transcript_path = video_dir / "transcript.txt"
    duration_path = video_dir / "duration.txt"
    audio_duration_seconds = 0.0

    try:
        if transcript_path.exists():
            transcript = transcript_path.read_text(encoding="utf-8").strip()
            audio_duration_seconds = _read_cached_duration(duration_path)
            if audio_duration_seconds <= 0:
                audio_duration_seconds = _duration_from_cached_audio(video_dir)
        else:
            audio_path = _download_audio(url, video_dir)
            audio_duration_seconds = _get_audio_duration(audio_path)
            if audio_duration_seconds > 0:
                duration_path.write_text(f"{audio_duration_seconds:.3f}\n", encoding="utf-8")
            transcript = _transcribe(audio_path, transcript_path)
    except Exception as exc:
        print(json.dumps({"kind": "reply", "reply": f"處理失敗：{exc}"}, ensure_ascii=False))
        return 0

    if not transcript:
        print(json.dumps({"kind": "reply", "reply": "轉錄結果為空，無法繼續。"}, ensure_ascii=False))
        return 0

    # kind:reply auto-chains to yt-summary if the edge exists;
    # returned as-is to the user if there is no successor.
    print(json.dumps(
        {
            "kind": "reply",
            "reply": transcript,
            "metadata": {
                "video_id": video_id,
                "url": url,
                "audio_duration": _format_audio_duration(audio_duration_seconds),
                "audio_duration_seconds": str(int(audio_duration_seconds)) if audio_duration_seconds > 0 else "",
            },
        },
        ensure_ascii=False,
    ))
    return 0


def _download_audio(url: str, video_dir: Path) -> Path:
    video_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(video_dir / "audio.%(ext)s")
    subprocess.run(
        [
            "yt-dlp",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "5",   # VBR ~130 kbps — sufficient for Whisper
            "--no-playlist",
            "--output", output_template,
            url,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    audio_files = list(video_dir.glob("audio.*"))
    if not audio_files:
        raise RuntimeError("yt-dlp did not produce an audio file")
    return audio_files[0]


def _transcribe(audio_path: Path, transcript_path: Path) -> str:
    import whisper  # type: ignore[import]

    # Whisper prints progress to stdout; redirect so it doesn't pollute our JSON output.
    _sink = io.StringIO()
    with contextlib.redirect_stdout(_sink):
        model = whisper.load_model(_WHISPER_MODEL)
        result = model.transcribe(str(audio_path), fp16=False, verbose=False)

    text: str = result["text"].strip()
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(text + "\n", encoding="utf-8")
    return text


def _read_cached_duration(duration_path: Path) -> float:
    if not duration_path.exists():
        return 0.0
    try:
        return float(duration_path.read_text(encoding="utf-8").strip())
    except ValueError:
        return 0.0


def _duration_from_cached_audio(video_dir: Path) -> float:
    for audio_path in sorted(video_dir.glob("audio.*")):
        duration = _get_audio_duration(audio_path)
        if duration > 0:
            return duration
    return 0.0


def _get_audio_duration(audio_path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _format_audio_duration(audio_duration_seconds: float) -> str:
    if audio_duration_seconds <= 0:
        return ""
    minutes, seconds = divmod(int(audio_duration_seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{seconds:02d}s"
    return f"{minutes}m{seconds:02d}s"


if __name__ == "__main__":
    sys.exit(main())
