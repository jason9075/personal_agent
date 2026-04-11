"""Private Discord bot with skill-based command execution."""
from __future__ import annotations

import os
import sys
from asyncio import to_thread
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv()

from .config import ALLOWED_USER_ID, BOT_LOG_DIR, SCHEDULE_DB_PATH
from .logging_utils import get_logger, setup_logging
from .schedule_db import ensure_db
from .scheduler import FinanceScheduler
from .skills import (
    execute_skill_action,
    format_direct_skill_reply,
    render_general_reply,
    render_skill_reply_pass2,
    route_tool_cli,
    should_use_pass2,
)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
repo_root = Path(__file__).resolve().parents[2]
scheduler = FinanceScheduler(SCHEDULE_DB_PATH, repo_root, client)
logger = get_logger()


@client.event
async def on_ready() -> None:
    ensure_db(SCHEDULE_DB_PATH)
    scheduler.start()
    logger.info("Logged in as %s", client.user)


@client.event
async def on_message(message: discord.Message) -> None:
    if message.author == client.user:
        return

    if str(message.author.id) != ALLOWED_USER_ID:
        logger.info("Ignored message from unauthorized user_id=%s channel_id=%s", message.author.id, message.channel.id)
        return

    if client.user not in message.mentions:
        logger.info("Ignored message without bot mention from user_id=%s channel_id=%s", message.author.id, message.channel.id)
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

    routed = await to_thread(route_tool_cli, content, "")
    if routed and routed.get("tool"):
        logger.info("Routed to skill tool=%s args=%s", routed["tool"], routed.get("args", {}))
        activation_message = _format_skill_activation_message(routed["tool"], routed.get("args", {}))
        logger.info("Sending skill activation message channel_id=%s message=%r", message.channel.id, activation_message)
        await message.channel.send(activation_message)
        try:
            action_result = await to_thread(
                execute_skill_action,
                routed["tool"],
                routed.get("args", {}),
                channel_id=str(message.channel.id),
            )
            logger.info(
                "Skill action completed tool=%s returncode=%s stdout_len=%s stderr_len=%s",
                action_result.tool_name,
                action_result.returncode,
                len(action_result.stdout),
                len(action_result.stderr),
            )
            if await to_thread(should_use_pass2, routed["tool"], routed.get("args", {}), action_result):
                logger.info("Using Pass2 tool=%s", routed["tool"])
                response = await to_thread(
                    render_skill_reply_pass2,
                    content,
                    action_result,
                    recent_context="",
                )
            else:
                logger.info("Skipping Pass2 tool=%s", routed["tool"])
                response = await to_thread(format_direct_skill_reply, action_result)
        except Exception as exc:
            logger.exception("Skill execution failed tool=%s", routed.get("tool"))
            response = f"技能執行失敗：{type(exc).__name__}: {exc}"
        logger.info("Sending skill response channel_id=%s response_len=%s", message.channel.id, len(response))
        await message.channel.send(response)
        return

    logger.info("No skill matched; using general reply")
    response = await to_thread(
        render_general_reply,
        content,
        recent_context="",
    )
    logger.info("Sending general reply channel_id=%s response_len=%s", message.channel.id, len(response))
    await message.channel.send(response)


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
    client.run(token)


def _format_skill_activation_message(tool_name: str, args: dict | None) -> str:
    args = args or {}
    if tool_name == "finance-report":
        if args.get("list_sources"):
            return "已啟用 skill: finance-report，正在列出可用來源。"
        source = str(args.get("source", "")).strip() or "全部來源"
        target_date = str(args.get("target_date", "")).strip() or "最新一集"
        workers = int(args.get("workers", 4))
        return f"已啟用 skill: finance-report，正在處理 {source}，目標：{target_date}，workers={workers}。"
    if tool_name == "finance-schedule":
        action = str(args.get("action", "list")).strip() or "list"
        return f"已啟用 skill: finance-schedule，正在執行 {action}。"
    return f"已啟用 skill: {tool_name}。"


if __name__ == "__main__":
    main()
