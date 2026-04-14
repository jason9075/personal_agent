# Repository Guidelines

## Project Overview

`personal_agent` is a private Discord bot built around a **node-first N-Pass Workflow Engine**. Workflow state lives in `db/workflow.sqlite3` and is managed from the web UI at `http://localhost:8765`.

## Architecture

### N-Pass Engine

```text
message -> start_node -> route by outgoing edges -> node lifecycle -> Discord reply?
```

Node lifecycle:

```text
pre_hook.py? -> run.py -> post_hook.py?
```

Core rules:

- `start_node` is unique and is usually the intent router.
- Route nodes may only choose nodes reachable through enabled outgoing edges.
- Nodes control prompt, tooling, hooks, and `send_response`.
- Hook files are discovered by scanning the node directory for `pre_hook.py`, `run.py`, and `post_hook.py`.
- Prompt bodies live in repo `.md` files. The workflow DB stores prompt file paths, not long prompt text blobs.

Key modules:

| File | Role |
|------|------|
| `src/bot/engine.py` | workflow execution loop, router behavior, node lifecycle |
| `src/bot/workflow_db.py` | SQLite schema, migrations, node/edge CRUD, hook scanning |
| `src/bot/nodes.py` | shared route parsing and node execution helpers |
| `src/bot/bot.py` | Discord event handler + FastAPI server |
| `src/web/app.py` | REST API for DAG management |
| `src/web/static/app.js` | LiteGraph DAG editor |
| `nodes/*/run.py` | node executors |
| `nodes/finance-report/impl/` | finance RSS pipeline, STT, digest |

## Module Organisation

- `src/bot/` — bot runtime, engine, workflow DB, scheduler, prompts
- `src/web/` — FastAPI app, templates, static files
- `nodes/*/` — executor scripts and node-local pipelines
- `config/` — local finance source config
- `db/` — runtime SQLite state, git-ignored

Keep workflow semantics in `src/bot/engine.py` and `src/bot/workflow_db.py`. Keep node-specific work in executor scripts, not in the Discord event handler.

## Development Commands

```bash
nix develop
just bot
just watch
just finance-report
ruff check src
mypy src
```

Prefer `just` targets over ad hoc shell commands.

## Coding Style

- 4-space indentation, type hints on public functions.
- `snake_case` for functions and variables, `UPPER_SNAKE_CASE` for env-backed constants.
- Prompts must live in repo markdown files and load dynamically at runtime.
- Avoid hardcoding workflow structure in Python when the DAG can express it.

## Testing

No formal test suite yet. Minimum validation:

```bash
python -m compileall src nodes
ruff check src
mypy src
```

For manual verification, run `just bot` and inspect `http://localhost:8765`.

## Commit Style

Use Conventional Commits such as `feat:`, `fix:`, `refactor:`, `chore:`.

## Security

- Never commit `.env`, real tokens, or private feed configuration.
- `db/`, `.local/`, and `notes/finance/` are runtime artifacts and must stay out of git.
- `ALLOWED_USER_ID` is the only user allowed to trigger Discord replies.
