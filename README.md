# personal_agent

Private Discord bot for Jason Kuan built around a **node-first N-Pass Workflow Engine**. Incoming mentions enter a DAG stored in `db/workflow.sqlite3`, start at the single `start_node`, and move across edges until a node with `send_response=true` returns a Discord reply.

## Setup

```bash
nix develop
cp .env.example .env
# fill in DISCORD_BOT_TOKEN, ALLOWED_USER_ID, FINANCE_REPORT_CHANNEL_ID
just bot
```

Open `http://localhost:8765` to edit the workflow graph.

## Execution model

```text
Discord mention
  -> start_node (usually route)
  -> route selects one reachable next node from its outgoing edges
  -> node lifecycle: pre_hook.py? -> run.py -> post_hook.py?
  -> if send_response=true: send output to Discord
```

Important rules:

- Workflow control is `node + edge`, not legacy skill-centric routing flags.
- Route candidates come only from the current route node's outgoing edges.
- `start_node` is unique. Saving a node with `start_node=true` clears the previous one.
- Hook files are optional. The web UI scans for sibling `pre_hook.py` / `run.py` / `post_hook.py` and shows lifecycle badges.
- Prompt text is stored in repo `.md` files. The DB stores prompt file paths such as `nodes/route/system.md`, and the engine loads them at runtime.

## Key modules

- `src/bot/engine.py` — node execution loop and route-by-edge behavior
- `src/bot/workflow_db.py` — SQLite schema, migration, node CRUD, hook scanning
- `src/bot/nodes.py` — shared helpers for direct-route parsing and node execution
- `src/bot/bot.py` — Discord event handler + FastAPI web server
- `src/web/app.py` — REST API for node and edge management
- `src/web/static/app.js` — LiteGraph DAG editor
- `nodes/*/run.py` — node executors
- `nodes/finance-report/impl/` — RSS download, STT, digest pipeline

## Current built-in nodes

- `route` — start node, LLM router
- `finance-report` — RSS download + STT + digest pipeline, sends final report
- `finance-schedule` — scheduler CRUD
- `general-reply` — fallback reply node
- `echo` — test node

## Development commands

```bash
just bot
just watch
just finance-sources
just finance-report
just finance-report source=youtinghao target_date=20260410
just clean
ruff check src
mypy src
```

## Runtime data

- `db/workflow.sqlite3` — workflow nodes and edges
- `db/bot_scheduler.sqlite3` — finance schedules
- `.local/bot/logs/` — bot logs
- `.local/finance/` — downloads, transcripts, intermediate outputs
- `notes/finance/` — final finance notes
