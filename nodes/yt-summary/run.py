"""YT Summary node — passes Whisper transcript to LLM for summarisation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    if "--args-json" not in sys.argv:
        print("usage: run.py --args-json '{...}'", file=sys.stderr)
        return 1

    idx = sys.argv.index("--args-json")
    payload: dict = json.loads(sys.argv[idx + 1])
    message = str(payload.get("message", "")).strip()
    prev_output = str(payload.get("prev_output", "")).strip()
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    audio_duration = str(metadata.get("audio_duration", "")).strip()
    audio_duration_seconds = str(metadata.get("audio_duration_seconds", "")).strip()

    if not prev_output:
        print(json.dumps({"kind": "reply", "reply": "沒有收到逐字稿，請先提供 YouTube 網址。"}, ensure_ascii=False))
        return 0

    run_output = json.dumps(
        {
            "user_instruction": message,
            "audio_duration": audio_duration,
            "audio_duration_seconds": audio_duration_seconds,
            "transcript": prev_output,
        },
        ensure_ascii=False,
        indent=2,
    )

    print(json.dumps(
        {
            "kind": "infer",
            "response_mode": "passthrough",
            "run_output": run_output,
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
