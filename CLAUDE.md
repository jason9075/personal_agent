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

```text
message -> start_node (router) -> decision: reply or use_next_node -> node lifecycle -> Discord reply?
```

**Node lifecycle:**

```text
pre_hook.py? -> run.py -> post_hook.py?
```

**Node behaviour — determined by stdout format, not a stored type field:**

| Behaviour | Condition |
|-----------|-----------|
| Decision/routing | stdout is valid JSON with a `decision` field (`reply` or `use_next_node`) |
| Direct reply | stdout is plain text (non-JSON), or JSON without a `decision` field |

> The `node_type` label used in documentation (`router` / `agent` / `tool`) is conceptual only — there is no `node_type` column in the database.

**Execution — `--args-json` protocol:** the engine calls `python nodes/<id>/run.py --args-json '{...}'`.

- Decision nodes must return one of:
  - `{"decision":"reply","reply":"..."}` — respond directly to user
  - `{"decision":"use_next_node","next_node_id":"...","args":{...}}` — delegate to a reachable node
- Non-decision nodes write the reply text to stdout.

**Pass / routing rules:**

- `start_node` is unique (enforced by DB unique index on `start_node=1`).
- Decision routing only selects nodes reachable through enabled outgoing edges.
- If a node produces plain-text stdout (no decision JSON) and has no enabled successor, stdout is returned as-is.
- If a node produces plain-text stdout and has an enabled successor, execution advances to the first enabled successor.

**Hook discovery:** engine auto-scans the node directory for `pre_hook.py` and `post_hook.py` alongside `run.py`. Explicit `pre_hook_path`/`post_hook_path` in the DB take precedence.

**Combined process:** Discord bot + FastAPI web server share the same asyncio event loop via `asyncio.gather(client.start(), uvicorn_server.serve())`.

### Key Files

| File | Role |
|------|------|
| `src/bot/bot.py` | Discord event loop + asyncio.gather with web server |
| `src/bot/engine.py` | workflow execution loop, routing, node lifecycle |
| `src/bot/workflow_db.py` | SQLite schema/CRUD for nodes and edges; seed data; hook scanning |
| `src/bot/nodes.py` | Execution helpers and reply formatters |
| `src/bot/scheduler.py` | In-process cron scheduler (polls every 30 s) |
| `src/bot/schedule_db.py` | SQLite schema/CRUD for scheduled jobs |
| `src/bot/prompts.py` | Loads prompt templates from node-local markdown files |
| `src/bot/config.py` | Env-backed constants |
| `src/bot/logging_utils.py` | Shared logger setup |
| `src/web/app.py` | FastAPI: `create_app(db_path)` — REST API for workflow graph |
| `src/web/templates/index.html` | LiteGraph.js graph editor (English UI, Nord colours) |
| `src/web/static/app.js` | Graph rendering, API integration, connection change sync |
| `src/web/static/app.css` | Nord colour scheme |
| `nodes/intent-router/` | Top-level intent router prompt assets |
| `nodes/finance/` | Finance decision node and source catalog |
| `nodes/finance-report/` | Finance node prompts and generated notes |
| `nodes/finance-report/impl/runner.py` | Finance report pipeline entry point |
| `nodes/finance-schedule/` | Schedule management node |
| `nodes/echo/` | Testing/echo node |
| `nodes/*/run.py` | Node executors (`--args-json` protocol) |

### Workflow Graph DB Schema

Tables in `db/workflow.sqlite3`:

```sql
workflow_nodes(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL DEFAULT 'gpt-5.4',
    start_node INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    executor_path TEXT NOT NULL DEFAULT '',
    pre_hook_path TEXT NOT NULL DEFAULT '',
    post_hook_path TEXT NOT NULL DEFAULT '',
    node_prompt_path TEXT NOT NULL DEFAULT '',   -- passed as-is into --args-json payload
    use_prev_output INTEGER NOT NULL DEFAULT 1,  -- pass previous node stdout into payload
    timeout_seconds INTEGER NOT NULL DEFAULT 600
)

workflow_edges(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node_id TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    FOREIGN KEY (from_node_id) REFERENCES workflow_nodes(id),
    FOREIGN KEY (to_node_id) REFERENCES workflow_nodes(id)
)
```

### Node Contract

To add a node:
1. Create `nodes/<name>/run.py` — accepts `--args-json '{"key":"val"}'`, writes to stdout, exits 0
2. Optionally add `nodes/<name>/pre_hook.py` and/or `post_hook.py` (same `--args-json` protocol)
3. Add the node via web UI or `_SEED_NODES` in `workflow_db.py`
4. For `router` nodes, return `reply` or `use_next_node` JSON; for `agent`/`tool` nodes, write reply to stdout

Engine injects into `--args-json` payload automatically:
- `node_prompt_path`, `model_name` (from node config, if set and not already in payload)
- `prev_output` (if `use_prev_output=True` and there is previous node output)
- `recent_context` (if provided and not already in payload)
- `next_nodes` (list of enabled outgoing neighbours, if any exist and not already in payload)

### Seed Nodes

| Node ID | Behaviour | Role |
|---------|-----------|------|
| `intent-router` | decision (JSON) | Start node; routes to finance or echo |
| `finance` | decision (JSON) | Finance domain; routes to finance-report or finance-schedule |
| `finance-report` | direct reply (stdout) | RSS fetch → transcribe → LLM digest → Discord post |
| `finance-schedule` | direct reply (stdout) | CRUD for scheduled finance jobs |
| `echo` | direct reply (stdout) | Testing node; returns input text directly |

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
