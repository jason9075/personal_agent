"""In-process cron-like scheduler for bot jobs."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

import discord

from .logging_utils import get_logger
from .schedule_db import ScheduledJob, delete_job, get_job, list_jobs, parse_input_json, set_job_run_result


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
        self._running_job_ids: set[int] = set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop(), name="bot-scheduler")

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
            await self._run_job(job, now, trigger="schedule")

    async def run_job_now(self, job_id: int) -> None:
        job = get_job(self.db_path, job_id)
        await self._run_job(job, datetime.now(), trigger="manual")

    async def _run_job(
        self,
        job: ScheduledJob,
        now: datetime,
        *,
        trigger: Literal["manual", "schedule"],
    ) -> None:
        logger = get_logger()
        if job.id in self._running_job_ids:
            raise RuntimeError(f"schedule job {job.id} is already running")

        self._running_job_ids.add(job.id)
        logger.info("Scheduler running job_id=%s name=%s trigger=%s", job.id, job.name, trigger)
        try:
            await self._execute_job(job, now, trigger=trigger)
        finally:
            self._running_job_ids.discard(job.id)

    async def _execute_job(
        self,
        job: ScheduledJob,
        now: datetime,
        *,
        trigger: Literal["manual", "schedule"],
    ) -> None:
        logger = get_logger()

        if job.channel_id:
            channel = cast(Any, self.client.get_channel(int(job.channel_id)))
            if channel is not None:
                trigger_text = "手動觸發" if trigger == "manual" else "排程"
                await channel.send(f"{trigger_text} `{job.name}` 開始執行，處理中…")

        try:
            output = await asyncio.to_thread(self._run_scheduled_job, job)
            status = "ok"
        except Exception as exc:
            status = "error"
            output = str(exc).strip() or f"{type(exc).__name__}"
        logger.info(
            "Scheduler job completed job_id=%s trigger=%s status=%s output_len=%s",
            job.id,
            trigger,
            status,
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
        channel = cast(Any, self.client.get_channel(int(job.channel_id)))
        if channel is None:
            return
        if status == "ok":
            await _send_to_channel(channel, output)
        else:
            trigger_text = "手動觸發" if trigger == "manual" else "排程"
            prefix = f"{trigger_text} `{job.name}` 執行失敗"
            await channel.send(f"{prefix}：\n```text\n{output[:1800]}\n```")

    def _run_scheduled_job(self, job: ScheduledJob) -> str:
        from .config import WORKFLOW_DB_PATH
        from .engine import WorkflowRequest, execute_workflow

        input_payload = parse_input_json(job.input_json)
        args = input_payload.get("args", {})
        if not isinstance(args, dict):
            args = {}
        metadata = input_payload.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        workflow_metadata = {str(key): str(value) for key, value in metadata.items()}
        workflow_metadata.update(
            {
                "trigger": "cron",
                "job_id": str(job.id),
                "job_name": job.name,
            }
        )
        response_metadata: dict[str, str] = {}
        return execute_workflow(
            WorkflowRequest(
                message=str(input_payload.get("message", "")),
                start_node_id=job.start_node_id,
                channel_id=job.channel_id,
                args={str(key): value for key, value in args.items()},
                metadata=workflow_metadata,
            ),
            WORKFLOW_DB_PATH,
            self.repo_root,
            response_metadata=response_metadata,
        ).strip()


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
