"""Private Discord bot with N-pass skill workflow execution."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import discord
import uvicorn
from dotenv import load_dotenv

load_dotenv()

from .config import ALLOWED_USER_ID, BOT_LOG_DIR, SCHEDULE_DB_PATH, WEB_PORT, WORKFLOW_DB_PATH
from .engine import execute_workflow
from .logging_utils import get_logger, setup_logging
from .schedule_db import ensure_db
from .scheduler import FinanceScheduler
from .workflow_db import ensure_workflow_db

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
repo_root = Path(__file__).resolve().parents[2]
scheduler = FinanceScheduler(SCHEDULE_DB_PATH, repo_root, client)
logger = get_logger()


@client.event
async def on_ready() -> None:
    ensure_db(SCHEDULE_DB_PATH)
    ensure_workflow_db(WORKFLOW_DB_PATH)
    scheduler.start()
    logger.info("Logged in as %s", client.user)


@client.event
async def on_message(message: discord.Message) -> None:
    if message.author == client.user:
        return

    if str(message.author.id) != ALLOWED_USER_ID:
        logger.info(
            "Ignored message from unauthorized user_id=%s channel_id=%s",
            message.author.id,
            message.channel.id,
        )
        return

    if client.user not in message.mentions:
        logger.info(
            "Ignored message without bot mention from user_id=%s channel_id=%s",
            message.author.id,
            message.channel.id,
        )
        return

    content = message.content
    for mention in (f"<@{client.user.id}>", f"<@!{client.user.id}>"):
        content = content.replace(mention, "")
    content = content.strip()
    logger.info(
        "Received mentioned message user_id=%s channel_id=%s content=%r",
        message.author.id,
        message.channel.id,
        content[:500],
    )

    if not content:
        logger.info("Ignored empty mention-only message channel_id=%s", message.channel.id)
        return

    try:
        response = await asyncio.to_thread(
            execute_workflow,
            content,
            WORKFLOW_DB_PATH,
            repo_root,
            recent_context="",
            channel_id=str(message.channel.id),
        )
    except Exception as exc:
        logger.exception("Workflow execution failed")
        response = f"工作流執行失敗：{type(exc).__name__}: {exc}"
    logger.info("Sending workflow response channel_id=%s response_len=%s", message.channel.id, len(response))
    await message.channel.send(response)


async def _run(token: str) -> None:
    """Run Discord bot and FastAPI web server in the same asyncio event loop."""
    from ..web.app import create_app

    web_app = create_app(WORKFLOW_DB_PATH)
    uvicorn_config = uvicorn.Config(
        web_app,
        host="0.0.0.0",
        port=WEB_PORT,
        log_level="warning",
    )
    web_server = uvicorn.Server(uvicorn_config)
    # Prevent uvicorn from overriding discord.py's signal handlers
    web_server.config.install_signal_handlers = False

    logger.info("Starting web server on port %s", WEB_PORT)
    await asyncio.gather(
        client.start(token),
        web_server.serve(),
    )


def main() -> None:
    log_path = setup_logging(BOT_LOG_DIR)
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("[error] DISCORD_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    if not ALLOWED_USER_ID:
        print("[error] ALLOWED_USER_ID not set", file=sys.stderr)
        sys.exit(1)
    logger.info("Bot startup log file: %s", log_path)
    asyncio.run(_run(token))


if __name__ == "__main__":
    main()
