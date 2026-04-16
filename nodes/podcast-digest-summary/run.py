from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _extract_channel_id(value: str) -> str:
    value = value.strip()
    mention = re.fullmatch(r"<#(\d+)>", value)
    if mention:
        return mention.group(1)
    if re.fullmatch(r"\d{10,}", value):
        return value
    return ""


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _reply(message: str) -> None:
    print(json.dumps({"kind": "reply", "reply": message}, ensure_ascii=False))


def main() -> int:
    if "--args-json" not in sys.argv:
        print("usage: run.py --args-json '{...}'", file=sys.stderr)
        return 1
    idx = sys.argv.index("--args-json")
    payload: dict[str, Any] = json.loads(sys.argv[idx + 1])
    message = str(payload.get("message", "")).strip()
    args = payload.get("args", {})
    if not isinstance(args, dict):
        args = {}
    prev_output = str(payload.get("prev_output", "")).strip()

    if not prev_output:
        _reply("沒有收到 podcast 逐字稿資料。")
        return 0

    prepared: dict[str, Any] = {}
    try:
        decoded = json.loads(prev_output)
        if isinstance(decoded, dict):
            prepared = decoded
    except json.JSONDecodeError:
        _reply(prev_output)
        return 0

    if not prepared:
        _reply("podcast 節點沒有回傳可用資料。")
        return 0

    status = str(prepared.get("status", "")).strip()
    silent = prepared.get("silent", {})
    if not isinstance(silent, dict):
        silent = {}
    silent_on_error = _as_bool(silent.get("silent_on_error") or args.get("silent_on_error"))
    silent_on_no_match = _as_bool(silent.get("silent_on_no_match") or args.get("silent_on_no_match"))
    silent_on_no_new = _as_bool(silent.get("silent_on_no_new") or args.get("silent_on_no_new") or args.get("silent_if_no_new_episode"))
    skip_if_already_sent = _as_bool(silent.get("skip_if_already_sent") or args.get("skip_if_already_sent"))
    if status == "skipped":
        reason = str(prepared.get("reason", "")).strip()
        title = str(prepared.get("title", "")).strip()
        message_text = str(prepared.get("message", "")).strip()
        if reason == "episode_already_processed" and (silent_on_no_new or skip_if_already_sent):
            _reply("")
            return 0
        if reason in {"episode_not_found", "no_audio_episode"} and silent_on_no_match:
            _reply("")
            return 0
        reply = message_text or f"沒有新的 podcast 單集需要處理。{('最新已處理：' + title) if title else ''}".strip()
        _reply(reply)
        return 0

    if status == "error":
        if silent_on_error:
            _reply("")
            return 0
        _reply(str(prepared.get("message", "")).strip() or "podcast digest 執行失敗。")
        return 0

    transcript = str(prepared.get("transcript", "")).strip()
    if not transcript:
        if silent_on_error:
            _reply("")
            return 0
        _reply("podcast 資料中沒有逐字稿，無法產生 digest。")
        return 0

    target_channel_id = _extract_channel_id(str(args.get("target_channel_id") or prepared.get("target_channel_id") or ""))
    digest_instruction = str(args.get("digest_instruction") or prepared.get("digest_instruction") or message or "請整理成清楚的繁體中文重點摘要。")

    episode = prepared.get("episode", {})
    if not isinstance(episode, dict):
        episode = {}

    run_output = json.dumps({
        "user_instruction": message,
        "digest_instruction": digest_instruction,
        "source": prepared.get("source", ""),
        "episode": {
            "title": episode.get("title", ""),
            "published": episode.get("published", ""),
            "link": episode.get("link", ""),
            "guid": episode.get("guid", ""),
            "description": episode.get("description", ""),
        },
        "dedupe": prepared.get("dedupe", {}),
        "schedule_args_template": prepared.get("schedule_args_template", {}),
        "transcript": transcript,
    }, ensure_ascii=False, indent=2)

    envelope: dict[str, Any] = {
        "kind": "infer",
        "response_mode": "passthrough",
        "run_output": run_output,
        "output_path": "nodes/podcast-digest-summary/output.md",
        "metadata": {},
    }
    if target_channel_id:
        envelope["metadata"] = {"target_channel_id": target_channel_id}
    print(json.dumps(envelope, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
