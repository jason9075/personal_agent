# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**personal_agent** is a minimal private Discord bot for Jason Kuan implementing an **N-Pass Workflow Engine** with a web management interface. The bot only responds to a single authorised user (`ALLOWED_USER_ID`) when mentioned.

## Development Commands

```bash
nix develop          # enter dev shell (fastapi, uvicorn, aiofiles, discordpy, whisper …)
just bot             # run bot + web server (same process, web on :8765)
just watch           # auto-restart on .py / .toml changes

just finance-sources                        # list configured RSS sources
just finance-report                         # process all sources (latest episode)
just finance-report source=<id>             # single source
just finance-report target_date=YYYYMMDD    # specific date
just finance-report workers=2               # override worker count
just clean                                  # delete generated notes/
```

Type checking and linting:
```bash
mypy src/
ruff check src/
```

## Architecture: N-Pass Workflow Engine

**Pass 1 — Routing:** router nodes inspect enabled outgoing edges from `db/workflow.sqlite3`. For candidates with `router_mode='direct_regex'`, the engine tries built-in direct routing first (finance-report, finance-schedule) and then named-group regex patterns. Otherwise it falls back to the LLM router, which only sees reachable next nodes.

If a node matches: sends `"已啟用 {id}"` to Discord, then executes.

**Execution — `--args-json` protocol:** the engine calls `python nodes/<id>/run.py --args-json '{...}'`. Subprocess writes result to stdout; engine captures it.

**General fallback:** if no workflow-specific node matches, `general-reply` produces a normal `codex exec` reply.

**Combined process:** Discord bot + FastAPI web server share the same asyncio event loop via `asyncio.gather(client.start(), uvicorn_server.serve())`.

### Key Files

| File | Role |
|------|------|
| `src/bot/bot.py` | Discord event loop + asyncio.gather with web server |
| `src/bot/engine.py` | workflow execution loop, routing, node lifecycle |
| `src/bot/workflow_db.py` | SQLite schema/CRUD for nodes and edges; seed data |
| `src/bot/nodes.py` | Execution helpers, reply formatters, direct-route functions |
| `src/bot/scheduler.py` | In-process cron scheduler (polls every 30 s) |
| `src/bot/schedule_db.py` | SQLite schema/CRUD for scheduled jobs |
| `src/bot/prompts.py` | Loads prompt templates from `src/bot/prompt/` |
| `src/web/app.py` | FastAPI: `create_app(db_path)` — REST API for workflow graph |
| `src/web/templates/index.html` | LiteGraph.js graph editor (English UI, Nord colours) |
| `src/web/static/app.js` | Graph rendering, API integration, connection change sync |
| `src/web/static/app.css` | Nord colour scheme |
| `nodes/finance-report/impl/runner.py` | Finance report pipeline entry point |
| `nodes/*/run.py` | Node executors (`--args-json` protocol) |

### Workflow Graph DB Schema

Tables in `db/workflow.sqlite3`:

```sql
workflow_nodes(id, pass_index, skill_id, enabled)
workflow_edges(id, from_node_id, to_node_id, condition_type, condition_value)
```

Legacy rows may still use `skill_id`, but the current model is node-first. No SKILL.md files are required; node metadata lives in the DB and prompt paths point to repo markdown files.

### Node Contract

To add a node:
1. Create `nodes/<name>/run.py` — accepts `--args-json '{"key":"val"}'`, writes to stdout, exits 0
2. Add the node via web UI or `_SEED_NODES`
4. For direct routing: add named-group regex to `router_patterns` (e.g. `(?P<text>.+)`) or register a built-in function in `engine._try_direct_route()`

### Finance Report Pipeline

Triggered via Discord or in-process cron scheduler:
1. Load `config/finance_sources.toml`
2. For each source (ThreadPool): fetch RSS → resolve episode → download audio → transcribe (Whisper, concurrency 1) → analyse (LLM, concurrency 4) → write `notes/finance/<id>/note_<date>.md` → post to `FINANCE_REPORT_CHANNEL_ID`

Previously written notes are reused (skips reprocessing if note exists).

## Configuration

Copy `.env.example` → `.env`. Required keys:
- `DISCORD_BOT_TOKEN`, `ALLOWED_USER_ID`, `FINANCE_REPORT_CHANNEL_ID`

Optional:
- `GEMINI_TOOL_MODEL` — enables LLM routing (e.g. `gemini-2.5-flash-lite`)
- `WEB_PORT` — web server port (default: `8765`)

Finance sources in `config/finance_sources.toml` (see `config/finance_sources.example.toml`).

## Generated Artifacts (git-ignored)

| Path | Contents |
|------|----------|
| `db/workflow.sqlite3` | Workflow graph state (created on first run) |
| `db/bot_scheduler.sqlite3` | Scheduled job state |
| `.local/finance/` | Downloads, transcripts, codex output, logs |
| `.local/bot/logs/` | Bot runtime logs |
| `notes/finance/` | Final markdown notes per source |
