# Repository Guidelines

## Project Overview

`personal_agent` is a private Discord bot built around an **N-Pass Workflow Engine**. Incoming Discord messages are routed through a configurable graph of skill nodes across multiple passes. The workflow topology and all skill metadata are stored in `db/workflow.sqlite3` and managed through the web UI at `http://localhost:8765`.

## Architecture

### N-Pass Engine

```
message → Pass 1 routing → skill execution → Pass 2 synthesis? → Discord reply
                                                     │
                                          (graph edges define
                                           which passes follow)
```

Key modules:

| File | Role |
|------|------|
| `src/bot/engine.py` | `route_pass1()`, `execute_and_synthesize()` — the execution loop |
| `src/bot/workflow_db.py` | SQLite schema + CRUD for `skills`, `workflow_nodes`, `workflow_edges` |
| `src/bot/skills.py` | Subprocess helpers, reply formatters, direct-route functions |
| `src/bot/bot.py` | Discord event handler + `asyncio.gather` with FastAPI web server |
| `src/web/app.py` | FastAPI app: REST API for workflow and skill management |
| `src/web/templates/index.html` | LiteGraph.js graph editor UI |
| `src/web/static/app.js` | Graph rendering, API integration, connection change handling |
| `skills/*/run.py` | Skill subprocess entry points (`--args-json` protocol) |

### Skill execution protocol

All skills use `--args-json`:

```bash
python skills/<name>/run.py --args-json '{"key": "value"}'
```

Old-style individual CLI flags remain supported for `just` targets (backward compatibility).

### Skill metadata — DB only

Skills are defined entirely in `db/workflow.sqlite3` (`skills` table). There are **no SKILL.md files**. The web UI (`/api/skills`, `/api/workflow`) is the canonical way to read and write skill metadata (name, description, router patterns, system prompt, pass2_mode, script_path).

### pass2_mode

| Value | Behaviour |
|-------|-----------|
| `never` | Return subprocess stdout directly |
| `always` | LLM synthesis via `codex exec` |
| `optional` | Skill-specific logic in `engine._should_synthesize()` |

### Workflow graph schema

```sql
skills(id, display_name, description, router_mode, router_patterns,
       script_path, system_prompt, pass2_mode, enabled)
workflow_nodes(id, pass_index, skill_id, enabled)
workflow_edges(id, from_node_id, to_node_id, condition_type, condition_value)
```

Node IDs follow convention `p{pass_index}:{skill_id}` (e.g. `p1:echo`).

### Adding a skill

1. Create `skills/<name>/run.py` — accepts `--args-json`, writes to stdout, exits 0 on success.
2. Insert a skill row via web UI or add to `_SEED_SKILLS` in `workflow_db.py`.
3. Add a `WorkflowNode` row via web UI or `_SEED_NODES`.
4. For direct routing: add a named-group regex pattern in the skill's `router_patterns` field (e.g. `(?P<text>.+)`), or add a built-in router function in `skills.py` and register it in `engine._try_direct_route()`.

### Combined process

The Discord bot and FastAPI web server run in the **same asyncio event loop**:

```python
await asyncio.gather(client.start(token), uvicorn_server.serve())
```

`uvicorn_server.config.install_signal_handlers = False` prevents conflicts with discord.py's signal handling.

## Module Organisation

- `src/bot/` — Discord bot runtime, engine, DB, scheduler, prompts
- `src/web/` — FastAPI app, HTML templates, static assets
- `src/finance_report/` — RSS finance pipeline (independent of bot routing)
- `skills/*/` — Skill subprocess entry points
- `config/` — Finance sources TOML
- `db/` — SQLite runtime state (git-ignored)

Keep new bot logic in `src/bot/`. Keep new web routes in `src/web/app.py`. Do not embed skill-specific logic in `engine.py` — use the skill's `script_path` and `pass2_mode`.

## Development Commands

```bash
nix develop          # required: loads all Python deps including fastapi, uvicorn, aiofiles
just bot             # run bot + web UI (port 8765)
just watch           # auto-restart on .py or .toml changes
just finance-report  # run finance pipeline directly
ruff check src
mypy src
```

`just --list` shows the full task surface. Prefer `just` targets over ad hoc commands.

## Coding Style

- 4-space indentation, type hints on all public functions.
- `snake_case` for functions/variables, `UPPER_SNAKE_CASE` for env-backed constants.
- Keep Discord event handlers narrow; push logic into `engine.py` or helper modules.
- Prompts live in `src/bot/prompt/` and are loaded at runtime — never hardcode prompt text in Python.
- Use `ruff` and `mypy` before committing.

## Testing

No automated test suite yet. Minimum gate: `ruff check src && mypy src`, then manual `just bot`.

When adding tests: place in `tests/`, name `test_*.py`, focus on routing logic, cron parsing, and skill argument extraction.

## Commit Style

Conventional Commits: `feat:`, `fix:`, `chore:`, `refactor:` followed by a brief imperative summary.

PRs should list validation steps and note any `.env` key changes or Discord permission changes.

## Security

- Never commit `.env` or real tokens.
- `db/` is runtime state — do not commit SQLite files.
- `ALLOWED_USER_ID` gates all Discord responses; only one user ID is supported.
