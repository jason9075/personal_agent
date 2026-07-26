# personal_agent

Private Discord bot for Jason Kuan built around a **node-first N-Pass Workflow Engine**. Incoming mentions enter a DAG stored in `db/workflow.sqlite3`, start at the single `start_node`, and move across edges until a node replies directly or delegates to the next reachable node.

## Setup

```bash
nix develop
cp .env.example .env
# fill in DISCORD_BOT_TOKEN, ALLOWED_USER_ID
just bot
```

Open `http://localhost:8765` to edit the workflow graph.

## Reminder helper

The **Reminders** web page manages recurring Discord reminders independently from
workflow cron jobs. A reminder has a first trigger time, a Discord channel, and an
every-N-days/weeks/months cadence. During an active period, it is sent every day
at the configured local time until the allowed Discord user replies `done` or
👌 directly to the reminder message. Skin-tone variants such as 👌🏻 are also
accepted. Completion suppresses the rest of that calendar day, week, or month.

Completing a reminder advances the next trigger from its original schedule and
records both sent and done events in `db/bot_scheduler.sqlite3`. Month-end anchors
are preserved, so a reminder created for January 31 advances to February 28 and
then back to March 31. Every two months advances by calendar month
(`January → March → May`), never by treating a month as 30 days.

After each unanswered notification, the displayed `Next Trigger` advances to the
next day at the same fixed time. The original cycle anchor is stored separately
so replying `done` can still close the correct day, week, or month.

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
- Prompt text is stored in repo `.md` files. The DB stores prompt file paths such as `nodes/intent-router/node.md`, and the engine loads them at runtime.

## Node stdout protocol

All node executors must output a JSON object to stdout. Two `kind` values are supported:

**Direct reply** — no LLM call:
```json
{"kind": "reply", "reply": "text sent back to Discord"}
```

**LLM infer** — engine calls the LLM and handles its output:
```json
{
  "kind": "infer",
  "response_mode": "decision",
  "run_output": "...",
  "default_args": {},
  "metadata": { "fallback_reply": "..." }
}
```

| field | values | meaning |
|-------|--------|---------|
| `kind` | `"reply"` / `"infer"` | whether to call the LLM |
| `response_mode` | `"decision"` / `"passthrough"` | `"decision"`: LLM must return routing JSON (`reply` or `use_next_node`); `"passthrough"`: LLM output is used directly as the reply |
| `run_output` | any string | context passed to the LLM (tool results, data summaries, etc.) |
| `default_args` | object | args pre-filled into `use_next_node`; LLM-returned `args` override these |
| `metadata.fallback_reply` | string | reply used when the LLM call fails |

`kind` and `response_mode` are intentionally separate: `kind` controls **whether** the LLM is invoked; `response_mode` controls **what to do with** the LLM output. Non-JSON or unknown `kind` values raise a `RuntimeError` at runtime.

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
- `db/bot_scheduler.sqlite3` — workflow schedules, recurring reminders, and reminder event history
- `.local/bot/logs/` — bot logs
- `.local/finance/` — downloads, transcripts, intermediate outputs
- `nodes/finance-report/notes/` — final finance notes
- `nodes/finance/sources.toml` — local RSS source catalog (git-ignored)
