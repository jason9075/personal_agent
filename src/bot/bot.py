"""Private Discord bot with node-first workflow execution."""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any

import discord
import uvicorn
from dotenv import load_dotenv

load_dotenv()

from .config import ALLOWED_USER_ID, BOT_LOG_DIR, SCHEDULE_DB_PATH, WEB_PORT, WORKFLOW_DB_PATH, WORKFLOW_TRACE_DB_PATH  # noqa: E402
from .engine import execute_workflow  # noqa: E402
from .logging_utils import get_logger, setup_logging  # noqa: E402
from .schedule_db import ensure_db  # noqa: E402
from .scheduler import FinanceScheduler  # noqa: E402
from .workflow_db import ensure_workflow_db  # noqa: E402
from .workflow_trace_db import ensure_trace_db  # noqa: E402

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
repo_root = Path(__file__).resolve().parents[2]
_IMAGE_ATTACHMENT_DIR = repo_root / ".local" / "discord-images"
_IMAGE_EXTENSIONS = {".apng", ".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
_MAX_IMAGE_ATTACHMENTS = 5
scheduler = FinanceScheduler(SCHEDULE_DB_PATH, repo_root, client)
logger = get_logger()


@client.event
async def on_ready() -> None:
    ensure_db(SCHEDULE_DB_PATH)
    ensure_workflow_db(WORKFLOW_DB_PATH)
    ensure_trace_db(WORKFLOW_TRACE_DB_PATH)
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
    image_paths = await _collect_image_paths(message, referenced_message)
    workflow_message = _build_workflow_message(content, referenced_message, image_paths)
    logger.info(
        "Received mentioned message user_id=%s channel_id=%s has_reference=%s image_count=%s content=%r",
        message.author.id,
        message.channel.id,
        referenced_message is not None,
        len(image_paths),
        workflow_message[:500],
    )

    if not workflow_message and not image_paths:
        logger.info("Ignored empty mention-only message channel_id=%s", message.channel.id)
        return

    try:
        response_metadata: dict[str, str] = {}
        response = await asyncio.to_thread(
            execute_workflow,
            workflow_message,
            WORKFLOW_DB_PATH,
            repo_root,
            recent_context="",
            channel_id=str(message.channel.id),
            image_paths=image_paths,
            response_metadata=response_metadata,
        )
    except Exception as exc:
        logger.exception("Workflow execution failed")
        response = f"工作流執行失敗：{type(exc).__name__}: {exc}"
        response_metadata = {}
    await _send_workflow_response(message, response, response_metadata)


async def _send_workflow_response(
    source_message: discord.Message,
    response: str,
    response_metadata: dict[str, str],
) -> None:
    response = response.strip() or "目前沒有可回覆的內容。"
    source_channel_id = str(source_message.channel.id)
    target_channel_id = str(response_metadata.get("target_channel_id", "")).strip()
    if not target_channel_id or target_channel_id == source_channel_id:
        logger.info(
            "Sending workflow response channel_id=%s response_len=%s",
            source_message.channel.id,
            len(response),
        )
        await source_message.reply(response, mention_author=False)
        return

    target_channel = await _resolve_sendable_channel(target_channel_id)
    if target_channel is None:
        logger.warning(
            "Workflow requested unknown or unsendable target_channel_id=%s source_channel_id=%s",
            target_channel_id,
            source_channel_id,
        )
        await source_message.reply(
            f"找不到可發送的目標頻道 `<#{target_channel_id}>`，原訊息如下：\n\n{response}",
            mention_author=False,
        )
        return

    logger.info(
        "Sending workflow response source_channel_id=%s target_channel_id=%s response_len=%s",
        source_channel_id,
        target_channel_id,
        len(response),
    )
    try:
        await target_channel.send(response)
    except discord.DiscordException as exc:
        logger.exception("Failed to send workflow response target_channel_id=%s", target_channel_id)
        await source_message.reply(
            f"無法發送到目標頻道 `<#{target_channel_id}>`：{type(exc).__name__}",
            mention_author=False,
        )
        return

    await source_message.reply(f"已發送到 <#{target_channel_id}>。", mention_author=False)


async def _resolve_sendable_channel(channel_id: str) -> Any | None:
    try:
        channel = client.get_channel(int(channel_id)) or await client.fetch_channel(int(channel_id))
    except (ValueError, discord.DiscordException):
        return None
    if not hasattr(channel, "send"):
        return None
    return channel


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


async def _collect_image_paths(
    message: discord.Message,
    referenced_message: discord.Message | None,
) -> list[str]:
    paths: list[str] = []
    for source_message in (referenced_message, message):
        if source_message is None:
            continue
        for attachment in source_message.attachments:
            if len(paths) >= _MAX_IMAGE_ATTACHMENTS:
                return paths
            if not _is_image_attachment(attachment):
                continue
            saved_path = await _save_image_attachment(source_message, attachment)
            if saved_path is not None:
                paths.append(str(saved_path))
    return paths


def _is_image_attachment(attachment: discord.Attachment) -> bool:
    content_type = attachment.content_type or ""
    if content_type.lower().startswith("image/"):
        return True
    return Path(attachment.filename).suffix.lower() in _IMAGE_EXTENSIONS


async def _save_image_attachment(
    message: discord.Message,
    attachment: discord.Attachment,
) -> Path | None:
    try:
        _IMAGE_ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
        filename = _safe_filename(attachment.filename)
        target_path = _IMAGE_ATTACHMENT_DIR / f"{message.id}-{attachment.id}-{filename}"
        await attachment.save(target_path, seek_begin=False, use_cached=True)
    except (discord.DiscordException, OSError):
        logger.exception(
            "Failed to save Discord image attachment message_id=%s attachment_id=%s",
            message.id,
            attachment.id,
        )
        return None
    return target_path


def _safe_filename(filename: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")
    return safe_name or "image"


def _build_workflow_message(
    user_content: str,
    referenced_message: discord.Message | None,
    image_paths: list[str],
) -> str:
    image_text = _format_image_paths(image_paths)
    if referenced_message is None:
        return "\n\n".join(part for part in (user_content, image_text) if part)

    reference_text = _format_referenced_message(referenced_message)
    if user_content:
        return "\n\n".join(part for part in (
            "使用者在 Discord 回覆一則訊息並 tag 你。\n\n"
            f"使用者補充：\n{user_content}\n\n"
            f"被回覆的訊息：\n{reference_text}",
            image_text,
        ) if part)
    return "\n\n".join(part for part in (
        "使用者在 Discord 回覆一則訊息並 tag 你。"
        "請根據被回覆的訊息內容接著回覆。\n\n"
        f"被回覆的訊息：\n{reference_text}",
        image_text,
    ) if part)


def _format_image_paths(image_paths: list[str]) -> str:
    if not image_paths:
        return ""
    formatted_paths = "\n".join(f"- {path}" for path in image_paths)
    return f"附加圖片已提供給 LLM，路徑如下：\n{formatted_paths}"


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

    web_app = create_app(WORKFLOW_DB_PATH, SCHEDULE_DB_PATH, WORKFLOW_TRACE_DB_PATH, scheduler=scheduler)
    uvicorn_config = uvicorn.Config(
        web_app,
        host="0.0.0.0",
        port=WEB_PORT,
        log_level="warning",
    )
    web_server = uvicorn.Server(uvicorn_config)
    # Prevent uvicorn from overriding discord.py's signal handlers
    web_server.config.install_signal_handlers = False  # type: ignore[attr-defined]

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
