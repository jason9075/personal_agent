---
name: finance-schedule
description: Manage the bot's SQLite-backed finance report schedules from Discord. Supports list, add, update, delete, enable, and disable.
bypasses_llm: true
pass2_mode: never
---

Use this skill when the user wants to manage recurring finance report jobs from Discord.

## Triggers

- `finance schedule list`
- `財經排程列表`
- `finance schedule add name=morning cron="0 8 * * 1-5" source=youtinghao workers=1`
- `finance schedule update 3 cron="30 8 * * 1-5"`
- `finance schedule enable 3`
- `finance schedule disable 3`
- `finance schedule delete 3`

## Fields

- `name`: unique job name
- `cron`: 5-field cron expression: minute hour day month weekday
- `source`: optional source id; empty means all sources
- `workers`: optional positive integer
- `channel`: optional Discord channel id; defaults to current channel

## Notes

- Schedules are stored in `db/bot_scheduler.sqlite3`
- The bot scheduler runs in-process after the bot starts
- Scheduled jobs trigger the same finance report runner used by `just fin`
