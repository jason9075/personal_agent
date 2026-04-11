"""In-process cron-like scheduler for finance report jobs."""
from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import discord

from .schedule_db import ScheduledJob, list_jobs, set_job_run_result


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
        while True:
            try:
                await self._tick()
            except Exception as exc:
                print(f"[bot] scheduler tick failed: {type(exc).__name__}: {exc}", flush=True)
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
        print(f"[bot] scheduler running job={job.id} name={job.name}", flush=True)
        cmd = ["python", "-m", "src.finance_report.runner", "--workers", str(job.workers)]
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
        output = (completed.stdout.strip() or completed.stderr.strip() or "(no output)")[:1800]
        status = "ok" if completed.returncode == 0 else "error"
        set_job_run_result(
            self.db_path,
            job.id,
            ran_at=now.isoformat(timespec="seconds"),
            status=status,
            message=output,
        )

        if job.channel_id:
            channel = self.client.get_channel(int(job.channel_id))
            if channel is not None:
                prefix = f"排程 `{job.name}` 執行{'成功' if status == 'ok' else '失敗'}"
                await channel.send(f"{prefix}：\n```text\n{output}\n```")


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
