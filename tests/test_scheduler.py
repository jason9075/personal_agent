from __future__ import annotations

import unittest
from pathlib import Path

from src.bot.scheduler import (
    FinanceScheduler,
    _matching_member_ids,
    _replace_plain_user_mentions,
)


class SchedulerMentionTest(unittest.TestCase):
    def test_plain_username_is_converted_to_discord_mention(self) -> None:
        content = "本週生生喝鮮乳換了嗎？@ajle2721"
        converted = _replace_plain_user_mentions(
            content,
            {"ajle2721": "853285381885788170"},
        )
        self.assertEqual(
            converted,
            "本週生生喝鮮乳換了嗎？<@853285381885788170>",
        )

    def test_existing_mentions_and_email_addresses_are_untouched(self) -> None:
        content = "通知 <@123>，信箱 test@example.com，未知 @nobody"
        converted = _replace_plain_user_mentions(content, {})
        self.assertEqual(converted, content)

    def test_member_search_requires_an_exact_unique_name(self) -> None:
        members = [
            {
                "user": {
                    "id": "853285381885788170",
                    "username": "ajle2721",
                    "global_name": "AliceOu",
                },
                "nick": None,
            },
            {
                "user": {
                    "id": "2",
                    "username": "someone_else",
                    "global_name": "Else",
                },
                "nick": None,
            },
        ]
        self.assertEqual(
            _matching_member_ids(members, "AJLE2721"),
            {"853285381885788170"},
        )
        self.assertEqual(_matching_member_ids(members, "ajle"), set())


class SchedulerMentionResolutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_resolver_uses_exact_guild_member_search_result(self) -> None:
        class FakeHttp:
            async def request(self, route, **kwargs):
                self.route = route
                self.kwargs = kwargs
                return [
                    {
                        "user": {
                            "id": "853285381885788170",
                            "username": "ajle2721",
                            "global_name": "AliceOu",
                        },
                        "nick": None,
                    }
                ]

        class FakeClient:
            def __init__(self) -> None:
                self.http = FakeHttp()

        class FakeGuild:
            id = 1492448721441525782
            members = []

        class FakeChannel:
            guild = FakeGuild()

        scheduler = FinanceScheduler(
            Path("/tmp/test-reminder.sqlite3"),
            Path("/tmp"),
            FakeClient(),  # type: ignore[arg-type]
        )
        resolved = await scheduler._resolve_plain_user_mentions(
            "本週生生喝鮮乳換了嗎？@ajle2721",
            FakeChannel(),
        )
        self.assertEqual(
            resolved,
            "本週生生喝鮮乳換了嗎？<@853285381885788170>",
        )


if __name__ == "__main__":
    unittest.main()
