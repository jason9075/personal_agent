# personal_agent

Private Discord bot for Jason Kuan built around a **node-first N-Pass Workflow Engine**. Incoming mentions enter a DAG stored in `db/workflow.sqlite3`, start at the single `start_node`, and move across edges until a node replies directly or delegates to the next reachable node.

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
  -> start_node (usually intent-router)
  -> decision node replies directly or selects one reachable next node
  -> node lifecycle: pre_hook.py? -> run.py -> post_hook.py?
  -> if no next node is selected, the current node output is returned to Discord
```

Important rules:

- Workflow control is `node + edge`, not legacy skill-centric routing flags.
- Decision nodes may only hand off to their current outgoing edges.
- `start_node` is unique. Saving a node with `start_node=true` clears the previous one.
- Each node has its own `model_name`. The default is `gpt-5.4`, and the web UI colors nodes by model rather than pass index.
- Hook files are optional. The web UI scans for sibling `pre_hook.py` / `run.py` / `post_hook.py` and shows lifecycle badges.
- Prompt text is stored in repo `.md` files. The DB stores prompt file paths such as `nodes/intent-router/system.md`, and the engine loads them at runtime.

## Key modules

- `src/bot/engine.py` — node execution loop and decision-node handoff behavior
- `src/bot/workflow_db.py` — SQLite schema, seed workflow, node CRUD, hook scanning
- `src/bot/nodes.py` — shared helpers for direct execution nodes
- `src/bot/bot.py` — Discord event handler + FastAPI web server
- `src/web/app.py` — REST API for node and edge management
- `src/web/static/app.js` — LiteGraph DAG editor
- `nodes/*/run.py` — node executors
- `nodes/intent-router/` — top-level intent decision node
- `nodes/finance/` — finance domain decision node and source catalog
- `nodes/finance-report/` — execution node, prompts, and generated notes
- `nodes/finance-report/impl/` — RSS download, STT, digest pipeline

## Current built-in nodes

- `intent-router` — start node, replies directly or delegates to top-level domains
- `finance` — finance domain node; can reply directly or delegate to finance subflows
- `finance-report` — downloads the selected feed, runs STT, and sends the final report
- `finance-schedule` — finance scheduler CRUD
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
- `nodes/finance-report/notes/` — final finance notes
- `nodes/finance/sources.toml` — local RSS source catalog (git-ignored)
