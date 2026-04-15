"""Private Discord bot with node-first workflow execution."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import discord
import uvicorn
from dotenv import load_dotenv

load_dotenv()

from .config import ALLOWED_USER_ID, BOT_LOG_DIR, SCHEDULE_DB_PATH, WEB_PORT, WORKFLOW_DB_PATH  # noqa: E402
from .engine import execute_workflow  # noqa: E402
from .logging_utils import get_logger, setup_logging  # noqa: E402
from .schedule_db import ensure_db  # noqa: E402
from .scheduler import FinanceScheduler  # noqa: E402
from .workflow_db import ensure_workflow_db  # noqa: E402

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

    bot_user = client.user
    if bot_user is None:
        logger.info("Ignored message before client user was ready channel_id=%s", message.channel.id)
        return

    if bot_user not in message.mentions:
        logger.info(
            "Ignored message without bot mention from user_id=%s channel_id=%s",
            message.author.id,
            message.channel.id,
        )
        return

    content = message.content
    for mention in (f"<@{bot_user.id}>", f"<@!{bot_user.id}>"):
        content = content.replace(mention, "")
    content = content.strip()
    referenced_message = await _resolve_referenced_message(message)
    workflow_message = _build_workflow_message(content, referenced_message)
    logger.info(
        "Received mentioned message user_id=%s channel_id=%s has_reference=%s content=%r",
        message.author.id,
        message.channel.id,
        referenced_message is not None,
        workflow_message[:500],
    )

    if not workflow_message:
        logger.info("Ignored empty mention-only message channel_id=%s", message.channel.id)
        return

    try:
        response = await asyncio.to_thread(
            execute_workflow,
            workflow_message,
            WORKFLOW_DB_PATH,
            repo_root,
            recent_context="",
            channel_id=str(message.channel.id),
        )
    except Exception as exc:
        logger.exception("Workflow execution failed")
        response = f"工作流執行失敗：{type(exc).__name__}: {exc}"
    logger.info("Sending workflow response channel_id=%s response_len=%s", message.channel.id, len(response))
    await message.reply(response, mention_author=False)


async def _resolve_referenced_message(message: discord.Message) -> discord.Message | None:
    """Return the Discord message that the triggering message replied to."""
    reference = message.reference
    if reference is None:
        return None

    if isinstance(reference.resolved, discord.Message):
        return reference.resolved

    if reference.message_id is None:
        return None

    try:
        channel: Any = message.channel
        if reference.channel_id and reference.channel_id != message.channel.id:
            fetched_channel = client.get_channel(reference.channel_id) or await client.fetch_channel(reference.channel_id)
            channel = fetched_channel
        if not hasattr(channel, "fetch_message"):
            return None
        return await channel.fetch_message(reference.message_id)
    except discord.DiscordException:
        logger.exception(
            "Failed to fetch referenced message channel_id=%s message_id=%s",
            reference.channel_id,
            reference.message_id,
        )
        return None


def _build_workflow_message(user_content: str, referenced_message: discord.Message | None) -> str:
    if referenced_message is None:
        return user_content

    reference_text = _format_referenced_message(referenced_message)
    if user_content:
        return (
            "使用者在 Discord 回覆一則訊息並 tag 你。\n\n"
            f"使用者補充：\n{user_content}\n\n"
            f"被回覆的訊息：\n{reference_text}"
        )
    return (
        "使用者在 Discord 回覆一則訊息並 tag 你。"
        "請根據被回覆的訊息內容接著回覆。\n\n"
        f"被回覆的訊息：\n{reference_text}"
    )


def _format_referenced_message(message: discord.Message) -> str:
    parts = [
        f"作者：{message.author.display_name}",
        f"內容：{message.clean_content.strip() or '(無文字內容)'}",
    ]

    if message.attachments:
        attachment_urls = [attachment.url for attachment in message.attachments if attachment.url]
        if attachment_urls:
            parts.append("附件：\n" + "\n".join(f"- {url}" for url in attachment_urls))

    embed_summaries = []
    for embed in message.embeds:
        summary = []
        if embed.title:
            summary.append(f"標題：{embed.title}")
        if embed.url:
            summary.append(f"連結：{embed.url}")
        if embed.description:
            summary.append(f"描述：{embed.description}")
        if summary:
            embed_summaries.append("\n".join(summary))
    if embed_summaries:
        parts.append("嵌入內容：\n" + "\n\n".join(embed_summaries))

    return "\n".join(parts)


async def _run(token: str) -> None:
    """Run Discord bot and FastAPI web server in the same asyncio event loop."""
    from ..web.app import create_app

    web_app = create_app(WORKFLOW_DB_PATH, SCHEDULE_DB_PATH, scheduler=scheduler)
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
