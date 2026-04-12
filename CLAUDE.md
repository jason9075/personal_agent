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

**Pass 1 — Routing:** `engine.route_pass1()` iterates enabled Pass-1 nodes from `db/workflow.sqlite3`. For each node with `router_mode='direct_regex'`: tries built-in router (finance-report, finance-schedule) or named-group regex patterns. Falls back to LLM router for `router_mode='llm'` nodes — the LLM only sees skills reachable at this pass.

If a node matches: sends `"已啟用 skill: {id}"` to Discord, then executes.

**Execution — `--args-json` protocol:** `engine.execute_and_synthesize()` calls `python skills/<id>/run.py --args-json '{...}'`. Subprocess writes result to stdout; engine captures it.

**Pass 2 — Synthesis:** controlled by `pass2_mode` in the skill's DB record:
- `never` → return stdout directly
- `always` → `codex exec` LLM synthesis
- `optional` → skill-specific logic in `engine._should_synthesize()`

**General fallback:** no skill matched → `codex exec` general reply.

**Combined process:** Discord bot + FastAPI web server share the same asyncio event loop via `asyncio.gather(client.start(), uvicorn_server.serve())`.

### Key Files

| File | Role |
|------|------|
| `src/bot/bot.py` | Discord event loop + asyncio.gather with web server |
| `src/bot/engine.py` | `route_pass1()`, `execute_and_synthesize()` |
| `src/bot/workflow_db.py` | SQLite schema/CRUD for skills, nodes, edges; seed data |
| `src/bot/skills.py` | Execution helpers, reply formatters, direct-route functions |
| `src/bot/scheduler.py` | In-process cron scheduler (polls every 30 s) |
| `src/bot/schedule_db.py` | SQLite schema/CRUD for scheduled jobs |
| `src/bot/prompts.py` | Loads prompt templates from `src/bot/prompt/` |
| `src/web/app.py` | FastAPI: `create_app(db_path)` — REST API for workflow + skills |
| `src/web/templates/index.html` | LiteGraph.js graph editor (English UI, Nord colours) |
| `src/web/static/app.js` | Graph rendering, API integration, connection change sync |
| `src/web/static/app.css` | Nord colour scheme |
| `src/finance_report/runner.py` | Finance report pipeline entry point |
| `skills/*/run.py` | Skill subprocesses (`--args-json` protocol) |

### Workflow Graph DB Schema

Tables in `db/workflow.sqlite3`:

```sql
skills(id, display_name, description, router_mode, router_patterns,
       script_path, system_prompt, pass2_mode, enabled)
workflow_nodes(id, pass_index, skill_id, enabled)
workflow_edges(id, from_node_id, to_node_id, condition_type, condition_value)
```

Node ID convention: `p{pass_index}:{skill_id}` (e.g. `p1:echo`).
No SKILL.md files — all skill metadata lives in the DB, editable via web UI.

### Skill Contract

To add a skill:
1. Create `skills/<name>/run.py` — accepts `--args-json '{"key":"val"}'`, writes to stdout, exits 0
2. Insert skill row via web UI (`/api/skills`) or add to `_SEED_SKILLS` in `workflow_db.py`
3. Add node via web UI or `_SEED_NODES`
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
| `db/workflow.sqlite3` | Skills + workflow graph (created on first run) |
| `db/bot_scheduler.sqlite3` | Scheduled job state |
| `.local/finance/` | Downloads, transcripts, codex output, logs |
| `.local/bot/logs/` | Bot runtime logs |
| `notes/finance/` | Final markdown notes per source |
