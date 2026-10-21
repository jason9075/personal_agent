from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.bot import bot
from src.bot.config import ALLOWED_USER_ID, BOSE_CONTROL_CHANNEL_ID


class BoseChannelMessageTest(unittest.IsolatedAsyncioTestCase):
    async def check_delivery(self, channel: str, author: str, mentioned: bool, accepted: bool) -> None:
        bot_user = SimpleNamespace(id=999)
        message = SimpleNamespace(
            author=SimpleNamespace(id=int(author)), channel=SimpleNamespace(id=int(channel)),
            mentions=[bot_user] if mentioned else [], content="pause",
        )
        with (patch.object(bot, "client", SimpleNamespace(user=bot_user)),
              patch.object(bot, "_handle_reminder_done", new_callable=AsyncMock, return_value=False),
              patch.object(bot, "_resolve_referenced_messages", new_callable=AsyncMock, return_value=[]),
              patch.object(bot, "_collect_image_paths", new_callable=AsyncMock, return_value=[]),
              patch.object(bot, "execute_workflow", return_value="已暫停") as execute,
              patch.object(bot, "_send_workflow_response", new_callable=AsyncMock) as send):
            await bot.on_message(message)
            self.assertEqual(execute.called, accepted)
            self.assertEqual(send.called, accepted)
            if accepted:
                self.assertEqual(execute.call_args.kwargs["channel_id"], channel)

    async def test_allowed_user_needs_no_mention_in_bose_channel(self) -> None:
        await self.check_delivery(BOSE_CONTROL_CHANNEL_ID, ALLOWED_USER_ID, False, True)

    async def test_other_channels_still_require_a_mention(self) -> None:
        await self.check_delivery("123", ALLOWED_USER_ID, False, False)

    async def test_mentions_still_work_in_other_channels(self) -> None:
        await self.check_delivery("123", ALLOWED_USER_ID, True, True)

    async def test_bose_channel_does_not_bypass_user_authorization(self) -> None:
        await self.check_delivery(BOSE_CONTROL_CHANNEL_ID, "0", False, False)


class RouterChannelContextTest(unittest.TestCase):
    def test_router_exposes_dedicated_channel_context_only_for_matching_id(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for channel in (BOSE_CONTROL_CHANNEL_ID, "123"):
            with self.subTest(channel=channel):
                result = subprocess.run([
                    sys.executable, str(root / "nodes/intent-router/run.py"), "--args-json",
                    json.dumps({"channel_id": channel, "message": "volume"}),
                ], capture_output=True, text=True, check=True)
                context = json.loads(json.loads(result.stdout)["run_output"])
                self.assertEqual(context["channel_id"], channel)
                self.assertEqual(context["bose_control_channel"], channel == BOSE_CONTROL_CHANNEL_ID)


if __name__ == "__main__":
    unittest.main()
