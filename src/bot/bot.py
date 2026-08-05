"""Private Discord bot with node-first workflow execution."""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import discord
import uvicorn
from dotenv import load_dotenv

load_dotenv()

from .config import ALLOWED_USER_ID, BOT_LOG_DIR, SCHEDULE_DB_PATH, WEB_PORT, WORKFLOW_DB_PATH, WORKFLOW_TRACE_DB_PATH  # noqa: E402
from .engine import execute_workflow  # noqa: E402
from .logging_utils import get_logger, setup_logging  # noqa: E402
from .reminder_db import (  # noqa: E402
    ReminderCompletion,
    ReminderEvent,
    complete_reminder_for_message,
    ensure_reminder_db,
    get_reminder_for_message,
    is_reminder_done_reaction,
    is_reminder_done_reply,
    list_pending_reminder_sent_events,
)
from .schedule_db import ensure_db  # noqa: E402
from .scheduler import FinanceScheduler  # noqa: E402
from .workflow_db import ensure_workflow_db  # noqa: E402
from .workflow_trace_db import ensure_trace_db  # noqa: E402

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
client = discord.Client(intents=intents)
repo_root = Path(__file__).resolve().parents[2]
_IMAGE_ATTACHMENT_DIR = repo_root / ".local" / "discord-images"
_IMAGE_EXTENSIONS = {".apng", ".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
_MAX_IMAGE_ATTACHMENTS = 5
_MAX_REFERENCE_DEPTH = 20
_REMINDER_RECONCILE_HISTORY_LIMIT = 500
scheduler = FinanceScheduler(SCHEDULE_DB_PATH, repo_root, client)
logger = get_logger()


@client.event
async def on_ready() -> None:
    ensure_db(SCHEDULE_DB_PATH)
    ensure_reminder_db(SCHEDULE_DB_PATH)
    ensure_workflow_db(WORKFLOW_DB_PATH)
    ensure_trace_db(WORKFLOW_TRACE_DB_PATH)
    scheduler.start()
    logger.info("Logged in as %s", client.user)
    await _reconcile_missed_reminder_completions()


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

    if await _handle_reminder_done(message, bot_user):
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
    referenced_messages = await _resolve_referenced_messages(message)
    image_paths = await _collect_image_paths(message, referenced_messages)
    workflow_message = _build_workflow_message(content, referenced_messages, image_paths)
    logger.info(
        "Received mentioned message user_id=%s channel_id=%s reference_depth=%s image_count=%s content=%r",
        message.author.id,
        message.channel.id,
        len(referenced_messages),
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


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    if str(payload.user_id) != ALLOWED_USER_ID:
        return
    if not is_reminder_done_reaction(str(payload.emoji)):
        return

    completion = complete_reminder_for_message(
        SCHEDULE_DB_PATH,
        discord_message_id=str(payload.message_id),
        channel_id=str(payload.channel_id),
        completed_at=datetime.now(),
        user_id=str(payload.user_id),
    )
    if completion is None:
        return

    await _send_reaction_completion_reply(
        channel_id=str(payload.channel_id),
        message_id=str(payload.message_id),
        completion=completion,
    )
    logger.info(
        "Reminder reaction completion status=%s reminder_id=%s user_id=%s "
        "message_id=%s emoji=%s next_trigger_at=%s",
        completion.status,
        completion.reminder.id,
        payload.user_id,
        payload.message_id,
        payload.emoji,
        completion.reminder.next_trigger_at,
    )


async def _reconcile_missed_reminder_completions() -> None:
    """Recover completion signals created while the Discord gateway was offline."""
    bot_user = client.user
    if bot_user is None:
        return

    events = list_pending_reminder_sent_events(SCHEDULE_DB_PATH)
    if not events:
        return

    events_by_channel: dict[str, list[ReminderEvent]] = {}
    for event in events:
        events_by_channel.setdefault(event.channel_id, []).append(event)

    completed_reminder_ids: set[int] = set()
    for channel_id, channel_events in events_by_channel.items():
        channel = await _resolve_sendable_channel(channel_id)
        if channel is None:
            logger.warning(
                "Could not reconcile reminder completions in channel_id=%s: channel unavailable",
                channel_id,
            )
            continue

        await _reconcile_reminder_reactions(
            channel,
            channel_events,
            completed_reminder_ids,
        )
        await _reconcile_reminder_replies(
            channel,
            channel_events,
            completed_reminder_ids,
            bot_user_id=str(bot_user.id),
        )


async def _reconcile_reminder_reactions(
    channel: Any,
    events: list[ReminderEvent],
    completed_reminder_ids: set[int],
) -> None:
    if not hasattr(channel, "fetch_message"):
        return

    for event in events:
        if event.reminder_id in completed_reminder_ids:
            continue
        try:
            reminder_message = await channel.fetch_message(int(event.discord_message_id))
            reacted = await _allowed_user_completed_with_reaction(reminder_message)
            await reminder_message.add_reaction("👌")
        except (ValueError, discord.DiscordException):
            logger.exception(
                "Could not inspect reminder reactions reminder_id=%s message_id=%s",
                event.reminder_id,
                event.discord_message_id,
            )
            continue
        if not reacted:
            continue

        completion = complete_reminder_for_message(
            SCHEDULE_DB_PATH,
            discord_message_id=event.discord_message_id,
            channel_id=event.channel_id,
            completed_at=datetime.now(),
            user_id=ALLOWED_USER_ID,
        )
        if completion is None or completion.status != "completed":
            continue
        completed_reminder_ids.add(event.reminder_id)
        await _send_reaction_completion_reply(
            channel_id=event.channel_id,
            message_id=event.discord_message_id,
            completion=completion,
        )
        logger.info(
            "Recovered missed reminder reaction reminder_id=%s message_id=%s "
            "next_trigger_at=%s",
            event.reminder_id,
            event.discord_message_id,
            completion.reminder.next_trigger_at,
        )


async def _allowed_user_completed_with_reaction(message: discord.Message) -> bool:
    for reaction in message.reactions:
        if not is_reminder_done_reaction(str(reaction.emoji)):
            continue
        async for user in reaction.users():
            if str(user.id) == ALLOWED_USER_ID:
                return True
    return False


async def _reconcile_reminder_replies(
    channel: Any,
    events: list[ReminderEvent],
    completed_reminder_ids: set[int],
    *,
    bot_user_id: str,
) -> None:
    if not hasattr(channel, "history"):
        return

    events_by_message_id = {event.discord_message_id: event for event in events}
    try:
        oldest_message_id = min(int(message_id) for message_id in events_by_message_id)
        messages = channel.history(
            limit=_REMINDER_RECONCILE_HISTORY_LIMIT,
            after=discord.Object(id=oldest_message_id),
            oldest_first=False,
        )
        async for message in messages:
            if str(message.author.id) != ALLOWED_USER_ID:
                continue
            if not is_reminder_done_reply(message.content, bot_user_id=bot_user_id):
                continue
            reference = message.reference
            reference_message_id = (
                str(reference.message_id)
                if reference is not None and reference.message_id is not None
                else ""
            )
            event = events_by_message_id.get(reference_message_id)
            if event is None or event.reminder_id in completed_reminder_ids:
                continue

            completion = complete_reminder_for_message(
                SCHEDULE_DB_PATH,
                discord_message_id=reference_message_id,
                channel_id=event.channel_id,
                completed_at=datetime.now(),
                user_id=str(message.author.id),
            )
            if completion is None or completion.status != "completed":
                continue
            completed_reminder_ids.add(event.reminder_id)
            await message.reply(_completion_reply_text(completion), mention_author=False)
            logger.info(
                "Recovered missed reminder reply reminder_id=%s reply_message_id=%s "
                "reference_message_id=%s next_trigger_at=%s",
                event.reminder_id,
                message.id,
                reference_message_id,
                completion.reminder.next_trigger_at,
            )
    except (ValueError, discord.DiscordException):
        logger.exception("Could not inspect reminder reply history channel_id=%s", channel.id)


async def _handle_reminder_done(message: discord.Message, bot_user: discord.ClientUser) -> bool:
    reference = message.reference
    reference_message_id = (
        str(reference.message_id)
        if reference is not None and reference.message_id is not None
        else ""
    )
    if not is_reminder_done_reply(message.content, bot_user_id=str(bot_user.id)):
        if reference_message_id:
            reminder = get_reminder_for_message(
                SCHEDULE_DB_PATH,
                discord_message_id=reference_message_id,
                channel_id=str(message.channel.id),
            )
            if reminder is not None:
                logger.info(
                    "Ignored non-completion reminder reply reminder_id=%s "
                    "reference_message_id=%s content=%r",
                    reminder.id,
                    reference_message_id,
                    message.content,
                )
                return True
        return False

    completion: ReminderCompletion | None = None
    if reference_message_id:
        completion = complete_reminder_for_message(
            SCHEDULE_DB_PATH,
            discord_message_id=reference_message_id,
            channel_id=str(message.channel.id),
            completed_at=datetime.now(),
            user_id=str(message.author.id),
        )

    mentioned_bot = bot_user in message.mentions
    if completion is None:
        if mentioned_bot:
            await message.reply(
                "請直接回覆要完成的提醒訊息並輸入 `done`，"
                "或在該提醒上按 👌 reaction。",
                mention_author=False,
            )
            return True
        return False

    await message.reply(_completion_reply_text(completion), mention_author=False)
    logger.info(
        "Reminder completion status=%s reminder_id=%s user_id=%s "
        "reference_message_id=%s content=%r next_trigger_at=%s",
        completion.status,
        completion.reminder.id,
        message.author.id,
        reference_message_id,
        message.content,
        completion.reminder.next_trigger_at,
    )
    return True


def _completion_reply_text(completion: ReminderCompletion) -> str:
    if completion.status == "already_completed":
        prefix = f"提醒「{completion.reminder.name}」已經回報完成。"
    else:
        prefix = f"✅ 已完成「{completion.reminder.name}」。"
    return f"{prefix}\n下次提醒：`{completion.reminder.next_trigger_at}`"


async def _send_reaction_completion_reply(
    *,
    channel_id: str,
    message_id: str,
    completion: ReminderCompletion,
) -> None:
    channel = await _resolve_sendable_channel(channel_id)
    if channel is None:
        logger.warning(
            "Could not acknowledge reminder reaction in unavailable channel_id=%s",
            channel_id,
        )
        return

    try:
        if hasattr(channel, "fetch_message"):
            reminder_message = await channel.fetch_message(int(message_id))
            await reminder_message.reply(
                _completion_reply_text(completion),
                mention_author=False,
            )
            return
        await channel.send(_completion_reply_text(completion))
    except (ValueError, discord.DiscordException):
        logger.exception(
            "Could not acknowledge reminder reaction channel_id=%s message_id=%s",
            channel_id,
            message_id,
        )


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


async def _resolve_referenced_messages(message: discord.Message) -> list[discord.Message]:
    """Return the replied-to message chain, closest reply first."""
    referenced_messages: list[discord.Message] = []
    seen_message_ids = {message.id}
    current = message

    for _ in range(_MAX_REFERENCE_DEPTH):
        referenced_message = await _resolve_single_referenced_message(current)
        if referenced_message is None:
            break
        if referenced_message.id in seen_message_ids:
            logger.warning(
                "Stopped Discord reference traversal because a cycle was detected message_id=%s",
                referenced_message.id,
            )
            break
        referenced_messages.append(referenced_message)
        seen_message_ids.add(referenced_message.id)
        current = referenced_message

    if len(referenced_messages) >= _MAX_REFERENCE_DEPTH:
        logger.warning(
            "Stopped Discord reference traversal at max_depth=%s source_message_id=%s",
            _MAX_REFERENCE_DEPTH,
            message.id,
        )

    return referenced_messages


async def _resolve_single_referenced_message(message: discord.Message) -> discord.Message | None:
    """Return the Discord message that *message* replied to."""
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
    referenced_messages: list[discord.Message],
) -> list[str]:
    paths: list[str] = []
    for source_message in [*reversed(referenced_messages), message]:
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
    referenced_messages: list[discord.Message],
    image_paths: list[str],
) -> str:
    image_text = _format_image_paths(image_paths)
    if not referenced_messages:
        return "\n\n".join(part for part in (user_content, image_text) if part)

    reference_text = _format_reference_chain(referenced_messages)
    if user_content:
        return "\n\n".join(part for part in (
            "使用者在 Discord 回覆一則訊息並 tag 你。"
            "以下是沿著 Discord reply/reference 往上追溯到源頭的引用鏈。\n\n"
            f"使用者補充：\n{user_content}\n\n"
            f"引用鏈：\n{reference_text}",
            image_text,
        ) if part)
    return "\n\n".join(part for part in (
        "使用者在 Discord 回覆一則訊息並 tag 你。"
        "請根據整條引用鏈的上下文接著回覆。\n\n"
        f"引用鏈：\n{reference_text}",
        image_text,
    ) if part)


def _format_image_paths(image_paths: list[str]) -> str:
    if not image_paths:
        return ""
    formatted_paths = "\n".join(f"- {path}" for path in image_paths)
    return f"附加圖片已提供給 LLM，路徑如下：\n{formatted_paths}"


def _format_reference_chain(referenced_messages: list[discord.Message]) -> str:
    chronological_messages = list(reversed(referenced_messages))
    formatted_messages = []
    total = len(chronological_messages)
    for index, message in enumerate(chronological_messages, start=1):
        formatted_messages.append(
            f"[{index}/{total}]\n{_format_referenced_message(message)}"
        )
    return "\n\n".join(formatted_messages)


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
