"""Minimal Discord bot: reply only when tagged by the allowed user and echo the content."""
import os
import sys

import discord
from dotenv import load_dotenv

load_dotenv()

from .config import ALLOWED_USER_ID

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


@client.event
async def on_ready() -> None:
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
