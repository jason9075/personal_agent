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

**Pass 1 — Decision:** decision nodes inspect only their enabled outgoing edges. Each decision node may either reply directly to the user or delegate to one reachable next node by returning structured JSON.

**Execution — `--args-json` protocol:** the engine calls `python nodes/<id>/run.py --args-json '{...}'`. Decision nodes must return either `{"decision":"reply","reply":"..."}` or `{"decision":"use_next_node","next_node_id":"...","args":{...}}`. Execution nodes write normal stdout replies.

**Combined process:** Discord bot + FastAPI web server share the same asyncio event loop via `asyncio.gather(client.start(), uvicorn_server.serve())`.

### Key Files

| File | Role |
|------|------|
| `src/bot/bot.py` | Discord event loop + asyncio.gather with web server |
| `src/bot/engine.py` | workflow execution loop, routing, node lifecycle |
| `src/bot/workflow_db.py` | SQLite schema/CRUD for nodes and edges; seed data |
| `src/bot/nodes.py` | Execution helpers and reply formatters |
| `src/bot/scheduler.py` | In-process cron scheduler (polls every 30 s) |
| `src/bot/schedule_db.py` | SQLite schema/CRUD for scheduled jobs |
| `src/bot/prompts.py` | Loads prompt templates from node-local markdown files |
| `src/web/app.py` | FastAPI: `create_app(db_path)` — REST API for workflow graph |
| `src/web/templates/index.html` | LiteGraph.js graph editor (English UI, Nord colours) |
| `src/web/static/app.js` | Graph rendering, API integration, connection change sync |
| `src/web/static/app.css` | Nord colour scheme |
| `nodes/intent-router/` | Top-level intent router prompt assets |
| `nodes/finance/` | Finance decision node and source catalog |
| `nodes/finance-report/` | Finance node prompts and generated notes |
| `nodes/finance-report/impl/runner.py` | Finance report pipeline entry point |
| `nodes/*/run.py` | Node executors (`--args-json` protocol) |

### Workflow Graph DB Schema

Tables in `db/workflow.sqlite3`:

```sql
workflow_nodes(id, pass_index, start_node, enabled)
workflow_edges(id, from_node_id, to_node_id, condition_type, condition_value)
```

### Node Contract

To add a node:
1. Create `nodes/<name>/run.py` — accepts `--args-json '{"key":"val"}'`, writes to stdout, exits 0
2. Add the node via web UI or `_SEED_NODES`
3. For decision nodes, return either `reply` or `use_next_node` JSON

### Finance Report Pipeline

Triggered via Discord or in-process cron scheduler:
1. `finance` loads `nodes/finance/sources.toml`, inspects local notes, and decides whether to reply directly or hand off to a finance subflow
2. `finance-report` fetches RSS → resolves episode → downloads audio → transcribes (Whisper, concurrency 1) → analyses (LLM, concurrency 4) → writes `nodes/finance-report/notes/<id>/note_<date>.md` → posts to `FINANCE_REPORT_CHANNEL_ID`

Previously written notes are reused (skips reprocessing if note exists).

## Configuration

Copy `.env.example` → `.env`. Required keys:
- `DISCORD_BOT_TOKEN`, `ALLOWED_USER_ID`, `FINANCE_REPORT_CHANNEL_ID`

Optional:
- `WEB_PORT` — web server port (default: `8765`)

Finance sources in `nodes/finance/sources.toml` (see `nodes/finance/sources.example.toml`).

## Generated Artifacts (git-ignored)

| Path | Contents |
|------|----------|
| `db/workflow.sqlite3` | Workflow graph state (created on first run) |
| `db/bot_scheduler.sqlite3` | Scheduled job state |
| `.local/finance/` | Downloads, transcripts, codex output, logs |
| `.local/bot/logs/` | Bot runtime logs |
| `nodes/finance-report/notes/` | Final markdown notes per source |
