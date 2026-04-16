"""Image Analysis node - passes Discord image attachments to LLM."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_MAX_IMAGES = 5


def main() -> int:
    if "--args-json" not in sys.argv:
        print("usage: run.py --args-json '{...}'", file=sys.stderr)
        return 1

    idx = sys.argv.index("--args-json")
    payload: dict = json.loads(sys.argv[idx + 1])
    args = payload.get("args", {})
    if not isinstance(args, dict):
        args = {}
    message = str(args.get("message") or payload.get("message", "")).strip()
    image_paths = _valid_image_paths(args.get("image_paths") or payload.get("image_paths", []))

    if not image_paths:
        print(json.dumps({"kind": "reply", "reply": "沒有收到可分析的圖片。"}, ensure_ascii=False))
        return 0

    run_output = json.dumps(
        {
            "user_instruction": message,
            "image_count": len(image_paths),
            "image_paths": image_paths,
        },
        ensure_ascii=False,
        indent=2,
    )

    print(json.dumps(
        {
            "kind": "infer",
            "response_mode": "passthrough",
            "run_output": run_output,
            "metadata": {
                "image_count": str(len(image_paths)),
            },
        },
        ensure_ascii=False,
    ))
    return 0


def _valid_image_paths(raw_paths: object) -> list[str]:
    if not isinstance(raw_paths, list):
        return []

    paths: list[str] = []
    for raw_path in raw_paths:
        if len(paths) >= _MAX_IMAGES:
            break
        path = Path(str(raw_path))
        if path.exists() and path.is_file():
            paths.append(str(path))
    return paths


if __name__ == "__main__":
    sys.exit(main())
