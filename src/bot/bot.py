"""Private Discord bot with skill-based command execution."""
from __future__ import annotations

import os
import sys
from asyncio import to_thread
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv()

from .config import ALLOWED_USER_ID, SCHEDULE_DB_PATH
from .schedule_db import ensure_db
from .scheduler import FinanceScheduler
from .skills import execute_skill, route_tool_cli

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
repo_root = Path(__file__).resolve().parents[2]
scheduler = FinanceScheduler(SCHEDULE_DB_PATH, repo_root, client)


@client.event
async def on_ready() -> None:
    ensure_db(SCHEDULE_DB_PATH)
    scheduler.start()
    print(f"[bot] logged in as {client.user}", flush=True)


@client.event
async def on_message(message: discord.Message) -> None:
    if message.author == client.user:
        return

    if str(message.author.id) != ALLOWED_USER_ID:
        return

    if client.user not in message.mentions:
        return

    content = message.content
    for mention in (f"<@{client.user.id}>", f"<@!{client.user.id}>"):
        content = content.replace(mention, "")
    content = content.strip()

    if not content:
        return

    routed = await to_thread(route_tool_cli, content, "")
    if routed and routed.get("tool"):
        try:
            response = await to_thread(
                execute_skill,
                routed["tool"],
                routed.get("args", {}),
                channel_id=str(message.channel.id),
            )
        except Exception as exc:
            response = f"技能執行失敗：{type(exc).__name__}: {exc}"
        await message.channel.send(response)
        return

    await message.channel.send(content)


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("[error] DISCORD_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    if not ALLOWED_USER_ID:
        print("[error] ALLOWED_USER_ID not set", file=sys.stderr)
        sys.exit(1)
    client.run(token)


if __name__ == "__main__":
    main()
