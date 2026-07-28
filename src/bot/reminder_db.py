"""SQLite-backed recurring reminders and completion history."""
from __future__ import annotations

import calendar
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal


Cadence = Literal["day", "week", "month"]
CompletionStatus = Literal["completed", "already_completed"]
_CADENCES = {"day", "week", "month"}
_DONE_REPLY_PATTERN = re.compile(
    r"\s*(?:done|👌\ufe0f?[\U0001F3FB-\U0001F3FF]?)[.!！。]?\s*",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class Reminder:
    id: int
    name: str
    message: str
    channel_id: str
    cadence: Cadence
    interval_n: int
    next_trigger_at: str
    cycle_due_at: str
    anchor_day: int
    enabled: bool
    last_reminded_at: str
    last_done_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ReminderEvent:
    id: int
    reminder_id: int
    event_type: str
    cycle_due_at: str
    occurred_at: str
    discord_message_id: str
    channel_id: str
    user_id: str


@dataclass(frozen=True)
class ReminderCompletion:
    reminder: Reminder
    status: CompletionStatus


def is_reminder_done_reply(content: str, *, bot_user_id: str = "") -> bool:
    normalized = str(content)
    if bot_user_id:
        normalized = normalized.replace(f"<@{bot_user_id}>", "")
        normalized = normalized.replace(f"<@!{bot_user_id}>", "")
    return _DONE_REPLY_PATTERN.fullmatch(normalized) is not None


def ensure_reminder_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        existing_columns = _table_columns(conn, "reminders")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                message TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                cadence TEXT NOT NULL CHECK (cadence IN ('day', 'week', 'month')),
                interval_n INTEGER NOT NULL CHECK (interval_n > 0),
                next_trigger_at TEXT NOT NULL,
                cycle_due_at TEXT NOT NULL,
                anchor_day INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_reminded_at TEXT NOT NULL DEFAULT '',
                last_done_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        if existing_columns and "cycle_due_at" not in existing_columns:
            conn.execute(
                "ALTER TABLE reminders ADD COLUMN cycle_due_at TEXT NOT NULL DEFAULT ''"
            )
            rows = conn.execute(
                """
                SELECT id, next_trigger_at, last_reminded_at
                FROM reminders
                """
            ).fetchall()
            for reminder_id, next_trigger_at, last_reminded_at in rows:
                cycle_due_at = str(next_trigger_at)
                migrated_next_trigger = parse_datetime(cycle_due_at)
                if last_reminded_at:
                    migrated_next_trigger = _next_daily_trigger(
                        migrated_next_trigger,
                        parse_datetime(str(last_reminded_at)),
                    )
                conn.execute(
                    """
                    UPDATE reminders
                    SET next_trigger_at = ?, cycle_due_at = ?
                    WHERE id = ?
                    """,
                    (
                        format_datetime(migrated_next_trigger),
                        cycle_due_at,
                        int(reminder_id),
                    ),
                )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminder_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reminder_id INTEGER NOT NULL,
                event_type TEXT NOT NULL CHECK (event_type IN ('sent', 'done')),
                cycle_due_at TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                discord_message_id TEXT NOT NULL DEFAULT '',
                channel_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (reminder_id) REFERENCES reminders(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reminder_events_message
            ON reminder_events(discord_message_id, channel_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reminder_events_reminder
            ON reminder_events(reminder_id, id DESC)
            """
        )
        conn.commit()


def list_reminders(db_path: Path) -> list[Reminder]:
    ensure_reminder_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"{_REMINDER_SELECT} ORDER BY enabled DESC, next_trigger_at ASC, id ASC"
        ).fetchall()
    return [_row_to_reminder(row) for row in rows]


def list_due_reminders(db_path: Path, now: datetime) -> list[Reminder]:
    reminders = list_reminders(db_path)
    today = now.date()
    due: list[Reminder] = []
    for reminder in reminders:
        trigger = parse_datetime(reminder.next_trigger_at)
        if not reminder.enabled or trigger > now:
            continue
        if now.time() < trigger.time():
            continue
        if reminder.last_reminded_at and parse_datetime(reminder.last_reminded_at).date() == today:
            continue
        due.append(reminder)
    return due


def get_reminder(db_path: Path, reminder_id: int) -> Reminder:
    ensure_reminder_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            f"{_REMINDER_SELECT} WHERE id = ?",
            (reminder_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"reminder {reminder_id} not found")
    return _row_to_reminder(row)


def create_reminder(
    db_path: Path,
    *,
    name: str,
    message: str,
    channel_id: str,
    cadence: str,
    interval_n: int,
    next_trigger_at: str,
    enabled: bool = True,
) -> Reminder:
    normalized_name = _require_text(name, "name")
    normalized_message = _require_text(message, "message")
    normalized_channel_id = _require_channel_id(channel_id)
    normalized_cadence = validate_cadence(cadence)
    normalized_interval = validate_interval(interval_n)
    trigger = parse_datetime(next_trigger_at)
    now_text = datetime.now().isoformat(timespec="seconds")

    ensure_reminder_db(db_path)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO reminders
                (name, message, channel_id, cadence, interval_n, next_trigger_at,
                 cycle_due_at, anchor_day, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_name,
                normalized_message,
                normalized_channel_id,
                normalized_cadence,
                normalized_interval,
                format_datetime(trigger),
                format_datetime(trigger),
                trigger.day,
                1 if enabled else 0,
                now_text,
                now_text,
            ),
        )
        conn.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("failed to create reminder")
        reminder_id = int(cursor.lastrowid)
    return get_reminder(db_path, reminder_id)


def update_reminder(
    db_path: Path,
    reminder_id: int,
    *,
    name: str | None = None,
    message: str | None = None,
    channel_id: str | None = None,
    cadence: str | None = None,
    interval_n: int | None = None,
    next_trigger_at: str | None = None,
    enabled: bool | None = None,
) -> Reminder:
    current = get_reminder(db_path, reminder_id)
    trigger = parse_datetime(current.next_trigger_at if next_trigger_at is None else next_trigger_at)
    cadence_value = current.cadence if cadence is None else validate_cadence(cadence)
    interval_value = current.interval_n if interval_n is None else validate_interval(interval_n)
    schedule_changed = next_trigger_at is not None or cadence is not None or interval_n is not None
    cycle_due_at = format_datetime(trigger) if schedule_changed else current.cycle_due_at

    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE reminders
            SET name = ?, message = ?, channel_id = ?, cadence = ?, interval_n = ?,
                next_trigger_at = ?, cycle_due_at = ?, anchor_day = ?, enabled = ?,
                last_reminded_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                current.name if name is None else _require_text(name, "name"),
                current.message if message is None else _require_text(message, "message"),
                current.channel_id if channel_id is None else _require_channel_id(channel_id),
                cadence_value,
                interval_value,
                format_datetime(trigger),
                cycle_due_at,
                trigger.day if schedule_changed else current.anchor_day,
                1 if (current.enabled if enabled is None else enabled) else 0,
                "" if schedule_changed else current.last_reminded_at,
                datetime.now().isoformat(timespec="seconds"),
                reminder_id,
            ),
        )
        conn.commit()
    return get_reminder(db_path, reminder_id)


def delete_reminder(db_path: Path, reminder_id: int) -> None:
    ensure_reminder_db(db_path)
    with _connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()
    if cursor.rowcount == 0:
        raise RuntimeError(f"reminder {reminder_id} not found")


def list_reminder_events(
    db_path: Path,
    reminder_id: int,
    *,
    limit: int = 100,
) -> list[ReminderEvent]:
    get_reminder(db_path, reminder_id)
    bounded_limit = max(1, min(limit, 500))
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, reminder_id, event_type, cycle_due_at, occurred_at,
                   discord_message_id, channel_id, user_id
            FROM reminder_events
            WHERE reminder_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (reminder_id, bounded_limit),
        ).fetchall()
    return [_row_to_event(row) for row in rows]


def record_reminder_sent(
    db_path: Path,
    reminder_id: int,
    *,
    cycle_due_at: str,
    sent_at: datetime,
    discord_message_id: str,
    channel_id: str,
) -> None:
    ensure_reminder_db(db_path)
    sent_at_text = format_datetime(sent_at)
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT next_trigger_at, cycle_due_at FROM reminders WHERE id = ?",
            (reminder_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"reminder {reminder_id} not found")
        if str(row[1]) != cycle_due_at:
            conn.rollback()
            return
        next_trigger_at = _next_daily_trigger(parse_datetime(str(row[0])), sent_at)
        conn.execute(
            """
            INSERT INTO reminder_events
                (reminder_id, event_type, cycle_due_at, occurred_at,
                 discord_message_id, channel_id)
            VALUES (?, 'sent', ?, ?, ?, ?)
            """,
            (reminder_id, cycle_due_at, sent_at_text, discord_message_id, channel_id),
        )
        conn.execute(
            """
            UPDATE reminders
            SET next_trigger_at = ?, last_reminded_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (format_datetime(next_trigger_at), sent_at_text, sent_at_text, reminder_id),
        )
        conn.commit()


def complete_reminder(
    db_path: Path,
    reminder_id: int,
    *,
    completed_at: datetime,
    user_id: str = "",
    expected_cycle_due_at: str | None = None,
) -> ReminderCompletion:
    ensure_reminder_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"{_REMINDER_SELECT} WHERE id = ?",
            (reminder_id,),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise RuntimeError(f"reminder {reminder_id} not found")
        reminder = _row_to_reminder(row)
        if expected_cycle_due_at is not None and reminder.cycle_due_at != expected_cycle_due_at:
            conn.rollback()
            return ReminderCompletion(reminder=reminder, status="already_completed")

        next_trigger = calculate_next_trigger(reminder, completed_at)
        completed_at_text = format_datetime(completed_at)
        conn.execute(
            """
            INSERT INTO reminder_events
                (reminder_id, event_type, cycle_due_at, occurred_at, channel_id, user_id)
            VALUES (?, 'done', ?, ?, ?, ?)
            """,
            (
                reminder.id,
                reminder.cycle_due_at,
                completed_at_text,
                reminder.channel_id,
                user_id,
            ),
        )
        conn.execute(
            """
            UPDATE reminders
            SET next_trigger_at = ?, cycle_due_at = ?, last_reminded_at = '',
                last_done_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                format_datetime(next_trigger),
                format_datetime(next_trigger),
                completed_at_text,
                completed_at_text,
                reminder.id,
            ),
        )
        conn.commit()
    return ReminderCompletion(reminder=get_reminder(db_path, reminder_id), status="completed")


def complete_reminder_for_message(
    db_path: Path,
    *,
    discord_message_id: str,
    channel_id: str,
    completed_at: datetime,
    user_id: str,
) -> ReminderCompletion | None:
    ensure_reminder_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT reminder_id, cycle_due_at
            FROM reminder_events
            WHERE event_type = 'sent' AND discord_message_id = ? AND channel_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (discord_message_id, channel_id),
        ).fetchone()
    if row is None:
        return None
    return complete_reminder(
        db_path,
        int(row[0]),
        completed_at=completed_at,
        user_id=user_id,
        expected_cycle_due_at=str(row[1]),
    )


def get_reminder_for_message(
    db_path: Path,
    *,
    discord_message_id: str,
    channel_id: str,
) -> Reminder | None:
    ensure_reminder_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT e.reminder_id
            FROM reminder_events e
            WHERE e.event_type = 'sent'
              AND e.discord_message_id = ?
              AND e.channel_id = ?
            ORDER BY e.id DESC
            LIMIT 1
            """,
            (discord_message_id, channel_id),
        ).fetchone()
    return None if row is None else get_reminder(db_path, int(row[0]))


def calculate_next_trigger(reminder: Reminder, completed_at: datetime) -> datetime:
    candidate = _add_period(
        parse_datetime(reminder.cycle_due_at),
        cadence=reminder.cadence,
        interval_n=reminder.interval_n,
        anchor_day=reminder.anchor_day,
    )
    while _period_start(candidate, reminder.cadence) <= _period_start(completed_at, reminder.cadence):
        candidate = _add_period(
            candidate,
            cadence=reminder.cadence,
            interval_n=reminder.interval_n,
            anchor_day=reminder.anchor_day,
        )
    return candidate


def _next_daily_trigger(current_trigger: datetime, sent_at: datetime) -> datetime:
    candidate = current_trigger
    while candidate <= sent_at:
        candidate += timedelta(days=1)
    return candidate


def _period_start(value: datetime, cadence: Cadence) -> tuple[int, ...]:
    if cadence == "day":
        return (value.year, value.month, value.day)
    if cadence == "week":
        monday = value.date() - timedelta(days=value.weekday())
        return (monday.year, monday.month, monday.day)
    return (value.year, value.month)


def parse_datetime(value: str) -> datetime:
    text = str(value).strip()
    if not text:
        raise RuntimeError("next_trigger_at is required")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RuntimeError("next_trigger_at must be an ISO local date/time") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def format_datetime(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat(timespec="seconds")


def validate_cadence(value: str) -> Cadence:
    normalized = str(value).strip().lower()
    if normalized not in _CADENCES:
        raise RuntimeError("cadence must be day, week, or month")
    return normalized  # type: ignore[return-value]


def validate_interval(value: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("interval_n must be a positive integer") from exc
    if normalized <= 0:
        raise RuntimeError("interval_n must be a positive integer")
    return normalized


def _add_period(
    value: datetime,
    *,
    cadence: Cadence,
    interval_n: int,
    anchor_day: int,
) -> datetime:
    if cadence == "day":
        return value + timedelta(days=interval_n)
    if cadence == "week":
        return value + timedelta(weeks=interval_n)

    month_index = value.year * 12 + (value.month - 1) + interval_n
    target_year, zero_based_month = divmod(month_index, 12)
    target_month = zero_based_month + 1
    target_day = min(anchor_day, calendar.monthrange(target_year, target_month)[1])
    return value.replace(year=target_year, month=target_month, day=target_day)


def _require_text(value: str, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise RuntimeError(f"{field_name} is required")
    return normalized


def _require_channel_id(value: str) -> str:
    normalized = _require_text(value, "channel_id")
    if not normalized.isdigit():
        raise RuntimeError("channel_id must contain only digits")
    return normalized


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


_REMINDER_SELECT = """
SELECT id, name, message, channel_id, cadence, interval_n, next_trigger_at,
       cycle_due_at, anchor_day, enabled, last_reminded_at, last_done_at,
       created_at, updated_at
FROM reminders
"""


def _row_to_reminder(row: tuple) -> Reminder:
    cadence = validate_cadence(str(row[4]))
    return Reminder(
        id=int(row[0]),
        name=str(row[1]),
        message=str(row[2]),
        channel_id=str(row[3]),
        cadence=cadence,
        interval_n=int(row[5]),
        next_trigger_at=str(row[6]),
        cycle_due_at=str(row[7]),
        anchor_day=int(row[8]),
        enabled=bool(row[9]),
        last_reminded_at=str(row[10] or ""),
        last_done_at=str(row[11] or ""),
        created_at=str(row[12]),
        updated_at=str(row[13]),
    )


def _row_to_event(row: tuple) -> ReminderEvent:
    return ReminderEvent(
        id=int(row[0]),
        reminder_id=int(row[1]),
        event_type=str(row[2]),
        cycle_due_at=str(row[3]),
        occurred_at=str(row[4]),
        discord_message_id=str(row[5] or ""),
        channel_id=str(row[6] or ""),
        user_id=str(row[7] or ""),
    )
