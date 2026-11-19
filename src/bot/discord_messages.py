"""Deterministic splitting for Discord's message content limit."""
from __future__ import annotations

import re

DISCORD_MESSAGE_LIMIT = 2000
_FENCE = re.compile(r"^ {0,3}(```|~~~)([^\n]*)$", re.MULTILINE)


def split_discord_message(content: str) -> list[str]:
    """Prefer paragraph, line, and word boundaries; reopen triple-fenced code."""
    chunks: list[str] = []
    opening = ""
    marker = ""
    while content:
        prefix = f"{opening}\n" if opening else ""
        if len(prefix) + len(content) <= DISCORD_MESSAGE_LIMIT:
            chunks.append(prefix + content)
            break

        # Reserve space for a newline and closing fence.
        budget = DISCORD_MESSAGE_LIMIT - len(prefix) - 4
        end = budget
        for separator in ("\n\n", "\n", " "):
            boundary = content.rfind(separator, 0, budget + 1)
            if boundary > 0:
                end = min(boundary + len(separator), budget)
                break

        # Avoid bisecting a fence line when another boundary is available.
        for fence in _FENCE.finditer(content):
            if fence.start() < end < fence.end() and fence.start() > 0:
                end = fence.start()
                break
            if fence.start() >= end:
                break

        piece = content[:end]
        for fence in _FENCE.finditer(piece):
            token, info = fence.groups()
            if not marker:
                marker = token
                opening = token + info[:64]
            elif token == marker and not info.strip():
                marker = ""
                opening = ""
        suffix = f"\n{marker}" if marker else ""
        chunks.append(prefix + piece + suffix)
        content = content[end:]
    return chunks
