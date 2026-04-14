"""In-process cron-like scheduler for finance report jobs."""
from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import discord

from .logging_utils import get_logger
from .schedule_db import ScheduledJob, delete_job, list_jobs, set_job_run_result


SCHEDULER_POLL_SECONDS = 30


@dataclass(frozen=True)
class CronSpec:
    minute: set[int]
    hour: set[int]
    day: set[int]
    month: set[int]
    weekday: set[int]


class FinanceScheduler:
    def __init__(self, db_path: Path, repo_root: Path, client: discord.Client) -> None:
        self.db_path = db_path
        self.repo_root = repo_root
        self.client = client
        self._task: asyncio.Task | None = None
        self._last_minute_key = ""

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop(), name="finance-scheduler")

    async def _run_loop(self) -> None:
        logger = get_logger()
        while True:
            try:
                await self._tick()
            except Exception as exc:
                logger.exception("Scheduler tick failed: %s", type(exc).__name__)
            await asyncio.sleep(SCHEDULER_POLL_SECONDS)

    async def _tick(self) -> None:
        now = datetime.now()
        minute_key = now.strftime("%Y-%m-%d %H:%M")
        if minute_key == self._last_minute_key:
            return
        self._last_minute_key = minute_key

        for job in list_jobs(self.db_path):
            if not job.enabled:
                continue
            if not cron_matches(job.cron_expr, now):
                continue
            await self._run_job(job, now)

    async def _run_job(self, job: ScheduledJob, now: datetime) -> None:
        logger = get_logger()
        logger.info("Scheduler running job_id=%s name=%s", job.id, job.name)

        if job.channel_id:
            channel = self.client.get_channel(int(job.channel_id))
            if channel is not None:
                await channel.send(f"排程 `{job.name}` 開始執行，處理中…")

        cmd = ["python", "nodes/finance-report/run.py", "--workers", str(job.workers)]
        if job.source_id:
            cmd.extend(["--source", job.source_id])

        completed = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            cwd=self.repo_root,
            check=False,
        )
        status = "ok" if completed.returncode == 0 else "error"
        if status == "ok":
            output = completed.stdout.strip() or "(no output)"
        else:
            output = completed.stderr.strip() or completed.stdout.strip() or "(no output)"
        logger.info(
            "Scheduler job completed job_id=%s status=%s returncode=%s output_len=%s",
            job.id,
            status,
            completed.returncode,
            len(output),
        )
        set_job_run_result(
            self.db_path,
            job.id,
            ran_at=now.isoformat(timespec="seconds"),
            status=status,
            message=output[:2000],
        )

        if job.run_once:
            delete_job(self.db_path, job.id)
            logger.info("Deleted run_once job_id=%s after execution", job.id)

        if not job.channel_id:
            return
        channel = self.client.get_channel(int(job.channel_id))
        if channel is None:
            return
        if status == "ok":
            await _send_to_channel(channel, output)
        else:
            prefix = f"排程 `{job.name}` 執行失敗"
            await channel.send(f"{prefix}：\n```text\n{output[:1800]}\n```")


def cron_matches(expr: str, current: datetime) -> bool:
    spec = parse_cron(expr)
    weekday = (current.weekday() + 1) % 7
    return (
        current.minute in spec.minute
        and current.hour in spec.hour
        and current.day in spec.day
        and current.month in spec.month
        and weekday in spec.weekday
    )


def parse_cron(expr: str) -> CronSpec:
    parts = expr.split()
    if len(parts) != 5:
        raise RuntimeError("cron expression must have 5 fields: minute hour day month weekday")
    return CronSpec(
        minute=_parse_field(parts[0], 0, 59),
        hour=_parse_field(parts[1], 0, 23),
        day=_parse_field(parts[2], 1, 31),
        month=_parse_field(parts[3], 1, 12),
        weekday=_parse_field(parts[4], 0, 6),
    )


def _parse_field(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for token in field.split(","):
        token = token.strip()
        if not token:
            raise RuntimeError("cron field token cannot be empty")
        if token == "*":
            values.update(range(minimum, maximum + 1))
            continue
        if token.startswith("*/"):
            step = int(token[2:])
            if step <= 0:
                raise RuntimeError("cron step must be positive")
            values.update(range(minimum, maximum + 1, step))
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                raise RuntimeError("cron range start must be <= end")
            _validate_range(start, minimum, maximum)
            _validate_range(end, minimum, maximum)
            values.update(range(start, end + 1))
            continue
        value = int(token)
        _validate_range(value, minimum, maximum)
        values.add(value)
    return values


def _validate_range(value: int, minimum: int, maximum: int) -> None:
    if value < minimum or value > maximum:
        raise RuntimeError(f"cron value {value} out of range {minimum}-{maximum}")


async def _send_to_channel(channel, content: str, limit: int = 1900) -> None:
    text = content.strip() or "(empty)"
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        await channel.send(text[:split_at].strip())
        text = text[split_at:].strip()
    if text:
        await channel.send(text)
