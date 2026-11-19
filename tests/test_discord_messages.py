from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.bot import bot
from src.bot.discord_messages import split_discord_message


class DiscordMessageSplitTest(unittest.TestCase):
    def test_short_and_exact_limit_messages_are_unchanged(self) -> None:
        for content in ("hello", "文" * 2000, "😀" * 2000):
            self.assertEqual(split_discord_message(content), [content])

    def test_large_reports_preserve_text_and_stay_within_limit(self) -> None:
        for content in ("x" * 8001, "財經摘要😀\n\n" * 1000, "word " * 1000):
            chunks = split_discord_message(content)
            self.assertEqual("".join(chunks), content)
            self.assertTrue(all(0 < len(chunk) <= 2000 for chunk in chunks))

    def test_prefers_paragraph_then_line_then_word_boundaries(self) -> None:
        for separator in ("\n\n", "\n", " "):
            content = "a" * 1000 + separator + "b" * 1500
            self.assertEqual(split_discord_message(content)[0], "a" * 1000 + separator)

    def test_reopens_code_blocks_with_language(self) -> None:
        for marker in ("```", "~~~"):
            content = f"{marker}python\n" + "print('hello')\n" * 400 + f"{marker}"
            chunks = split_discord_message(content)
            restored = chunks[0]
            for chunk in chunks[1:]:
                self.assertTrue(restored.endswith(f"\n{marker}"))
                self.assertTrue(chunk.startswith(f"{marker}python\n"))
                restored = restored[:-4] + chunk[len(f"{marker}python\n"):]
            self.assertEqual(restored, content)
            self.assertTrue(all(len(chunk) <= 2000 for chunk in chunks))


class WorkflowResponseTest(unittest.IsolatedAsyncioTestCase):
    async def test_all_routes_send_the_entire_report_in_bounded_chunks(self) -> None:
        response = "report\n" * 1000
        for route in ("same", "explicit-same", "target", "unknown"):
            with self.subTest(route=route):
                channel = SimpleNamespace(id=1, send=AsyncMock())
                source = SimpleNamespace(channel=channel, reply=AsyncMock())
                target = SimpleNamespace(send=AsyncMock())
                metadata = {} if route == "same" else {
                    "target_channel_id": "1" if route == "explicit-same" else "2",
                }
                with patch.object(
                    bot, "_resolve_sendable_channel", new=AsyncMock(
                        return_value=None if route == "unknown" else target,
                    ),
                ):
                    await bot._send_workflow_response(source, response, metadata)
                if route == "target":
                    calls = target.send.await_args_list
                    source.reply.assert_awaited_once_with(
                        "已發送到 <#2>。", mention_author=False,
                    )
                else:
                    calls = source.reply.await_args_list + channel.send.await_args_list
                    source.reply.assert_awaited_once()
                    self.assertFalse(source.reply.await_args.kwargs["mention_author"])
                sent = [call.args[0] for call in calls]
                self.assertTrue(all(0 < len(chunk) <= 2000 for chunk in sent))
                expected = response.strip()
                if route == "unknown":
                    expected = "找不到可發送的目標頻道 `<#2>`，原訊息如下：\n\n" + expected
                self.assertEqual("".join(sent), expected)


if __name__ == "__main__":
    unittest.main()
