"""Intent router node executor."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if "--args-json" not in sys.argv:
        print("usage: run.py --args-json '{\"message\": \"...\"}'", file=sys.stderr)
        return 1

    idx = sys.argv.index("--args-json")
    payload: dict = json.loads(sys.argv[idx + 1])
    system_prompt_path = str(payload.get("system_prompt_path", "")).strip()
    prompt_template_path = str(payload.get("prompt_template_path", "")).strip()
    message = str(payload.get("message", "")).strip()
    recent_context = str(payload.get("recent_context", "")).strip()
    next_nodes = payload.get("next_nodes", [])

    node_dir = Path(__file__).resolve().parent
    system_prompt = _read_text(system_prompt_path, node_dir)
    template = _read_text(prompt_template_path, node_dir)
    prompt = template.format(
        next_nodes_json=json.dumps(next_nodes, ensure_ascii=False, indent=2),
        recent_context=recent_context or "(none)",
        user_message=message or "(empty)",
    )
    if system_prompt:
        prompt = f"{system_prompt}\n\n{prompt}"

    repo_root = node_dir.parents[1]
    model_name = str(payload.get("model_name", "")).strip() or os.getenv("INTENT_ROUTER_MODEL", "").strip() or os.getenv("FINANCE_CODEX_MODEL", "").strip() or "gpt-5.4"
    cmd = [
        "codex",
        "-m",
        model_name,
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "-C",
        str(repo_root),
    ]

    completed = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        capture_output=True,
        cwd=repo_root,
        check=False,
    )
    if completed.returncode != 0:
        print(
            json.dumps(
                {"decision": "reply", "reply": "目前無法完成判斷，請稍後再試。"},
                ensure_ascii=False,
            )
        )
        return 0

    parsed = _parse_json_response(completed.stdout)
    print(json.dumps(parsed, ensure_ascii=False, separators=(",", ":")))
    return 0


def _read_text(path_str: str, node_dir: Path) -> str:
    if not path_str:
        return ""
    raw = Path(path_str)
    path = raw if raw.is_absolute() else node_dir.parents[1] / raw
    return path.read_text(encoding="utf-8")


def _parse_json_response(raw: str) -> dict:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
    if not text:
        return {"decision": "reply", "reply": "目前沒有可處理的內容。"}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"decision": "reply", "reply": "目前無法完成判斷，請稍後再試。"}
    if not isinstance(parsed, dict):
        return {"decision": "reply", "reply": "目前無法完成判斷，請稍後再試。"}
    return parsed


if __name__ == "__main__":
    sys.exit(main())
