"""Discord notification helpers for the finance report pipeline."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


DISCORD_API_BASE = "https://discord.com/api/v10"


def send_markdown_message(bot_token: str, channel_id: str, content: str) -> None:
    for chunk in _split_message(content):
        _send_message(bot_token, channel_id, chunk)


def _split_message(content: str, limit: int = 1900) -> list[str]:
    text = content.strip()
    if not text:
        return ["(empty message)"]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    chunks.append(remaining)
    return chunks


def _send_message(bot_token: str, channel_id: str, content: str) -> None:
    payload = json.dumps({"content": content}).encode("utf-8")
    request = urllib.request.Request(
        f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
        data=payload,
        headers={
            "Authorization": f"Bot {bot_token}",
            "Content-Type": "application/json",
            "User-Agent": "personal-agent-finance-reporter/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            if response.status >= 300:
                raise RuntimeError(f"discord send failed with status {response.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"discord send failed: HTTP {exc.code} {body}") from exc
