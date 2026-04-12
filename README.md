# personal_agent

A minimal private Discord bot for Jason Kuan built around an **N-Pass Workflow Engine** — a configurable, graph-driven skill pipeline with a web management UI.

## Setup

```bash
nix develop                  # enter dev shell (Python 3.12 + all deps)
cp .env.example .env
# fill in DISCORD_BOT_TOKEN, ALLOWED_USER_ID, FINANCE_REPORT_CHANNEL_ID
just bot                     # start bot + web UI together
```

Open `http://localhost:8765` to manage the workflow graph.

## How it works

The bot only responds when the authorised user (`ALLOWED_USER_ID`) tags it in Discord.

### N-Pass Workflow Engine

Every incoming message travels through a configurable sequence of passes driven by a **workflow graph** stored in `db/workflow.sqlite3`.

```
Discord message
       │
  ┌────▼─────────────────────────────────────────────┐
  │ Pass 1 — Route                                   │
  │  1. Try direct-regex routers for each node       │
  │  2. Fall back to LLM router (only sees Pass 1    │
  │     nodes, not the full skill catalogue)         │
  └────┬─────────────────────────────────────────────┘
       │ skill selected → "已啟用 skill: <id>"
  ┌────▼─────────────────────────────────────────────┐
  │ Execute — python skills/<id>/run.py              │
  │  --args-json '{"key": "val"}'  →  stdout         │
  └────┬─────────────────────────────────────────────┘
       │ pass2_mode controls next step
  ┌────▼──────────────────────┐   ┌───────────────┐
  │ Pass 2 — LLM Synthesis    │   │ Direct reply  │
  │ codex exec + tool output  │   │ (stdout as-is)│
  └────┬──────────────────────┘   └───────┬───────┘
       └──────────────┬───────────────────┘
                 Discord reply

  No skill matched → LLM general reply (codex exec)
```

**Workflow graph:** nodes are `(pass_index, skill_id)` pairs; edges are conditional transitions (`always`, `returncode_eq`, `output_contains`). Future passes beyond 2 can be added by drawing edges to Pass 3+ nodes in the web UI — no code change required.

**Skill routing per pass:** the LLM router at each pass only receives the skill descriptors that are reachable at that pass. This keeps routing prompts small and reduces hallucination.

### Skill execution protocol

Every `skills/<name>/run.py` accepts:

```bash
python skills/<name>/run.py --args-json '{"key": "value"}'
```

Old-style individual flags are still supported for direct `just` invocations.

### pass2_mode

| Value | Behaviour |
|-------|-----------|
| `never` | Return `run.py` stdout directly to Discord |
| `always` | Send tool output through `codex exec` for LLM synthesis |
| `optional` | Skill-specific logic (e.g. finance-report skips synthesis for list actions) |

## Web UI

```bash
just bot   # starts Discord bot + FastAPI server on :8765 in one process
```

Open `http://localhost:8765`. Features:

- **LiteGraph.js canvas** — drag-and-drop node graph with Nord colour scheme
- **Node editor** — enable/disable nodes, edit the skill's description, router patterns, system prompt, pass2_mode
- **Live edge editing** — draw connections on canvas (auto-saved to DB); right-click a connection to set its condition type and value
- **Skills tab** — edit any skill definition without touching a node
- **API** — `GET/PUT /api/workflow`, `GET/PUT /api/skills/{id}`, `POST/DELETE /api/workflow/edges`

## Development commands

```bash
nix develop          # required: loads fastapi, uvicorn, aiofiles, etc.
just bot             # run bot + web (combined asyncio process)
just watch           # auto-restart on source file changes
just finance-sources # list configured RSS sources
just finance-report  # run finance pipeline (all sources, latest episode)
just finance-report source=youtinghao target_date=20260410
just clean           # delete notes/finance/
ruff check src
mypy src
```

## Finance Report Pipeline

The bot can generate finance reports from RSS podcast feeds. Configure sources in `config/finance_sources.toml`:

```toml
[[sources]]
id     = "youtinghao"
title  = "游庭皓的財經皓角"
author = "游庭皓"
rss_url = "https://..."
```

Pipeline per source: fetch RSS → resolve episode → download audio → Whisper transcription (concurrency 1) → LLM analysis (concurrency 4) → write `notes/finance/<id>/note_<date>.md` → post to `FINANCE_REPORT_CHANNEL_ID`.

Trigger via Discord:

```
@bot finance report
@bot finance report source=youtinghao
@bot finance report source=youtinghao 20260410
@bot finance sources
```

Or schedule it:

```
@bot finance schedule list
@bot finance schedule add name=morning cron="0 8 * * 1-5" source=youtinghao workers=1
@bot finance schedule update 3 cron="30 8 * * 1-5"
@bot finance schedule disable 3
@bot finance schedule enable 3
@bot finance schedule delete 3
```

If a note for a source+date already exists, the pipeline skips re-transcription and re-posts the cached note.

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_BOT_TOKEN` | ✓ | — | Bot authentication token |
| `ALLOWED_USER_ID` | ✓ | — | Discord user ID to authorise |
| `FINANCE_REPORT_CHANNEL_ID` | ✓ | — | Channel for finance report posts |
| `GEMINI_TOOL_MODEL` | — | — | Enables LLM routing (e.g. `gemini-2.5-flash-lite`) |
| `WEB_PORT` | — | `8765` | Web UI port |
| `BOT_LOG_DIR` | — | `.local/bot/logs` | Bot log directory |

## Generated artifacts (git-ignored)

| Path | Contents |
|------|----------|
| `db/workflow.sqlite3` | Skills + workflow graph |
| `db/bot_scheduler.sqlite3` | Scheduled job state |
| `.local/finance/` | Downloads, transcripts, codex output, logs |
| `.local/bot/logs/` | Bot runtime logs |
| `notes/finance/` | Final markdown notes per source |
