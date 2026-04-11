---
name: finance-report
description: Run or inspect the RSS-based finance report pipeline from Discord. Supports listing sources, processing all sources, selecting one source, optional target date, and optional workers.
bypasses_llm: true
pass2_mode: optional
---

Use this skill when the user wants to trigger the finance report pipeline from Discord.

## Triggers

Typical messages:

- `finance report`
- `財經報告`
- `財經報告 source=youtinghao`
- `finance report 20260410`
- `finance report source=youtinghao 2026-04-10`
- `finance report workers=2`
- `finance sources`
- `列出財經來源`

## Arguments

- `source`: optional source id from `config/finance_sources.toml`
- `target_date`: optional `YYYYMMDD` or `YYYY-MM-DD`
- `workers`: optional positive integer, default `4`
- `list_sources`: optional boolean to list configured sources

## Execution

This skill runs the local command line workflow:

- `python -m src.finance_report.runner --list-sources`
- `python -m src.finance_report.runner --workers 4`
- `python -m src.finance_report.runner --workers 4 --source <id> <date>`

## Response

Return the command output to Discord in a concise fenced block so the user can see which sources were processed and whether work was skipped or completed.
