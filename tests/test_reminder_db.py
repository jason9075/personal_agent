from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.bot.reminder_db import (
    complete_reminder,
    complete_reminder_for_message,
    create_reminder,
    ensure_reminder_db,
    get_reminder,
    get_reminder_for_message,
    is_reminder_done_reply,
    list_due_reminders,
    list_reminder_events,
    record_reminder_sent,
)


class ReminderDbTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "scheduler.sqlite3"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_month_end_anchor_is_preserved(self) -> None:
        reminder = create_reminder(
            self.db_path,
            name="Month end",
            message="Finish the report",
            channel_id="123",
            cadence="month",
            interval_n=1,
            next_trigger_at="2026-01-31T09:00",
        )

        february = complete_reminder(
            self.db_path,
            reminder.id,
            completed_at=datetime(2026, 1, 31, 10),
        )
        self.assertEqual(february.reminder.next_trigger_at, "2026-02-28T09:00:00")

        march = complete_reminder(
            self.db_path,
            reminder.id,
            completed_at=datetime(2026, 2, 28, 10),
        )
        self.assertEqual(march.reminder.next_trigger_at, "2026-03-31T09:00:00")

    def test_only_explicit_done_replies_are_accepted(self) -> None:
        accepted = ("done", "DONE!", " 👌 ", "👌️", "👌🏻", "<@42> done")
        rejected = (
            "",
            "其他內容",
            "今天還沒做",
            "done later",
            "可以算 done 嗎",
            "👌 thanks",
        )
        self.assertTrue(
            all(is_reminder_done_reply(content, bot_user_id="42") for content in accepted)
        )
        self.assertFalse(
            any(is_reminder_done_reply(content, bot_user_id="42") for content in rejected)
        )

    def test_sent_reminder_retries_next_day_and_reply_completes_cycle(self) -> None:
        reminder = create_reminder(
            self.db_path,
            name="Weekly task",
            message="Do the task",
            channel_id="123",
            cadence="week",
            interval_n=2,
            next_trigger_at="2026-07-20T09:00",
        )
        sent_at = datetime(2026, 7, 20, 9)
        record_reminder_sent(
            self.db_path,
            reminder.id,
            cycle_due_at=reminder.next_trigger_at,
            sent_at=sent_at,
            discord_message_id="456",
            channel_id="123",
        )

        after_send = get_reminder(self.db_path, reminder.id)
        self.assertEqual(after_send.cycle_due_at, "2026-07-20T09:00:00")
        self.assertEqual(after_send.next_trigger_at, "2026-07-21T09:00:00")
        found = get_reminder_for_message(
            self.db_path,
            discord_message_id="456",
            channel_id="123",
        )
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.id, reminder.id)
        self.assertEqual(list_due_reminders(self.db_path, datetime(2026, 7, 20, 18)), [])
        self.assertEqual(list_due_reminders(self.db_path, datetime(2026, 7, 21, 8, 59)), [])
        self.assertEqual(
            [item.id for item in list_due_reminders(self.db_path, datetime(2026, 7, 21, 9))],
            [reminder.id],
        )

        completion = complete_reminder_for_message(
            self.db_path,
            discord_message_id="456",
            channel_id="123",
            completed_at=datetime(2026, 7, 21, 10),
            user_id="789",
        )
        self.assertIsNotNone(completion)
        assert completion is not None
        self.assertEqual(completion.status, "completed")
        self.assertEqual(completion.reminder.next_trigger_at, "2026-08-03T09:00:00")
        self.assertEqual(
            [event.event_type for event in list_reminder_events(self.db_path, reminder.id)],
            ["done", "sent"],
        )

        repeated = complete_reminder_for_message(
            self.db_path,
            discord_message_id="456",
            channel_id="123",
            completed_at=datetime(2026, 7, 21, 11),
            user_id="789",
        )
        self.assertIsNotNone(repeated)
        assert repeated is not None
        self.assertEqual(repeated.status, "already_completed")
        self.assertEqual(repeated.reminder.next_trigger_at, "2026-08-03T09:00:00")

    def test_done_suppresses_the_rest_of_current_calendar_period(self) -> None:
        daily = create_reminder(
            self.db_path,
            name="Daily",
            message="Daily task",
            channel_id="123",
            cadence="day",
            interval_n=1,
            next_trigger_at="2026-07-25T09:00",
        )
        daily_done = complete_reminder(
            self.db_path,
            daily.id,
            completed_at=datetime(2026, 7, 26, 8),
        )
        self.assertEqual(daily_done.reminder.next_trigger_at, "2026-07-27T09:00:00")

        monthly = create_reminder(
            self.db_path,
            name="Monthly",
            message="Monthly task",
            channel_id="123",
            cadence="month",
            interval_n=1,
            next_trigger_at="2026-01-31T09:00",
        )
        monthly_done = complete_reminder(
            self.db_path,
            monthly.id,
            completed_at=datetime(2026, 2, 5, 8),
        )
        self.assertEqual(monthly_done.reminder.next_trigger_at, "2026-03-31T09:00:00")

    def test_every_two_months_uses_calendar_months_not_sixty_days(self) -> None:
        reminder = create_reminder(
            self.db_path,
            name="Every two months",
            message="Calendar task",
            channel_id="123",
            cadence="month",
            interval_n=2,
            next_trigger_at="2026-01-31T09:00",
        )
        march = complete_reminder(
            self.db_path,
            reminder.id,
            completed_at=datetime(2026, 1, 31, 10),
        )
        self.assertEqual(march.reminder.next_trigger_at, "2026-03-31T09:00:00")

        may = complete_reminder(
            self.db_path,
            reminder.id,
            completed_at=datetime(2026, 3, 5, 8),
        )
        self.assertEqual(may.reminder.next_trigger_at, "2026-05-31T09:00:00")

    def test_existing_database_migrates_next_trigger_to_day_after_last_send(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    message TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    cadence TEXT NOT NULL,
                    interval_n INTEGER NOT NULL,
                    next_trigger_at TEXT NOT NULL,
                    anchor_day INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_reminded_at TEXT NOT NULL DEFAULT '',
                    last_done_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO reminders
                    (name, message, channel_id, cadence, interval_n, next_trigger_at,
                     anchor_day, enabled, last_reminded_at, created_at, updated_at)
                VALUES
                    ('Existing', 'Task', '123', 'week', 1, '2026-07-20T09:00:00',
                     20, 1, '2026-07-20T09:00:05', '2026-07-01T00:00:00',
                     '2026-07-20T09:00:05')
                """
            )
            conn.commit()

        ensure_reminder_db(self.db_path)
        migrated = get_reminder(self.db_path, 1)
        self.assertEqual(migrated.cycle_due_at, "2026-07-20T09:00:00")
        self.assertEqual(migrated.next_trigger_at, "2026-07-21T09:00:00")


if __name__ == "__main__":
    unittest.main()
