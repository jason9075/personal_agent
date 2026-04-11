# personal_agent

`personal_agent` is currently a minimal private Discord bot for Jason Kuan.

## Setup

```bash
nix develop        # enter dev shell (Python 3.12 + all deps)
cp .env.example .env
# add DISCORD_BOT_TOKEN and ALLOWED_USER_ID
```

## Behavior

The Discord bot is locked to a single owner:

- Set `ALLOWED_USER_ID` in `.env`
- Only that user can trigger responses
- The bot only replies when it is tagged
- The reply is just the tagged message content echoed back
- If the message only contains the tag and no other content, the bot stays silent

Run the bot with:

```bash
just bot
```

Bot runtime logs are written to `.local/bot/logs/` and also streamed to stdout. Startup, received messages, routing decisions, skill execution, Pass 2 usage, and scheduler activity are all recorded there.

For auto-restart during development:

```bash
just watch
```

## Bot Architecture

The chatbot now follows a 2-pass skill pipeline:

1. Pass 1: when the owner tags the bot, the bot loads available skills from `skills/*/SKILL.md` and asks an LLM router to choose the best action.
2. Action step: the selected skill executes a local tool, typically `skills/<skill-name>/run.py`, and that tool writes structured or plain-text results to `stdout`.
3. Pass 2: the bot sends the tool output, together with the user request and recent context, to `codex exec` so the final reply is synthesized by an LLM before being returned to Discord.

This split keeps routing cheap, allows deterministic local tool execution, and makes final user-facing responses consistent even when tool outputs are noisy or verbose.

Recommended skill contract:

- `skills/<skill-name>/SKILL.md`: routing metadata, trigger examples, arguments, and execution notes
- `skills/<skill-name>/run.py`: side-effecting or data-fetching command entrypoint
- `stdout`: the canonical output boundary between the tool run and Pass 2 summarization
- `pass2_mode` in `SKILL.md` frontmatter controls whether the bot should always summarize with `codex exec`, never summarize, or decide case by case

Supported `pass2_mode` values:

- `always`: always run Pass 2
- `never`: return `run.py` output directly
- `optional`: bot decides based on the skill and action result

In this model, Discord commands only bypass Pass 2 when the skill output is already user-ready or explicitly diagnostic. The normal path remains `route -> run.py -> codex exec -> reply`.

Bot prompts are stored under `src/bot/prompt/` and loaded from disk on demand for each routing or Pass 2 execution. The bot does not keep compiled prompt text hardcoded in Python or cached across process restarts.

When mentioned in Discord, the bot can also trigger the finance report workflow. Example messages:

```text
@bot finance report
@bot finance report source=youtinghao
@bot finance report source=youtinghao 20260410
@bot finance sources
```

The bot also has its own SQLite-backed scheduler at `db/bot_scheduler.sqlite3`. It behaves like an app-managed crontab rather than the system crontab. Example schedule commands:

```text
@bot finance schedule list
@bot finance schedule add name=morning cron="0 8 * * 1-5" source=youtinghao workers=1
@bot finance schedule update 3 cron="30 8 * * 1-5"
@bot finance schedule disable 3
@bot finance schedule enable 3
@bot finance schedule delete 3
```

## Finance Report Pipeline

The repository also includes an RSS-backed finance report pipeline for private use. Manage feeds in `config/finance_sources.toml`, then run:

```bash
cp config/finance_sources.example.toml config/finance_sources.toml
just finance-sources
just finance-report
just finance-report source=youtinghao
just finance-report workers=4
just finance-report source=youtinghao target_date=20260410
just clean
```

The runner downloads each RSS feed, selects the requested episode, downloads the enclosure audio, runs Whisper locally, sends the transcript to `codex exec`, writes `note_<date>.md`, and posts the generated note to Discord.

Each source is managed with:

```toml
[[sources]]
id = "youtinghao"
title = "游庭皓的財經皓角"
author = "游庭皓"
rss_url = "https://..."
```

Generated downloads, transcripts, Codex output, logs, and feed debug files are stored under source-specific folders in `.local/finance/` and ignored by git. Notes are also split by source and written to `notes/finance/<source-id>/`. `just clean` only removes generated notes under `notes/finance/`.

The pipeline now requires `config/finance_sources.toml`; RSS URLs are no longer read from `.env`.

Default behavior:

1. If `source` is omitted, all configured sources are processed.
2. If `target_date` is omitted, each source uses its latest feed item.
3. Source-level worker pool defaults to `4`, and can be changed with `workers=<n>` or `--workers <n>`.
4. Whisper transcription is intentionally limited to concurrency `1` to avoid CPU/GPU overload on long audio.
5. `codex exec` analysis is limited to concurrency `4`.
6. If the note for that source and episode date already exists, the pipeline skips transcription and analysis, then directly re-sends the existing note to Discord.

Matching behavior:

1. First try `pubDate == target_date`
2. If that fails, fall back to title matching using date keywords such as `2026/4/10`
3. If `target_date` is omitted, the first feed item is treated as the latest episode
4. If multiple matches exist, the first feed item wins

Each run writes `.local/finance/debug/<source-id>/feed_<date>.xml` and `.json` so you can inspect the raw RSS payload, parsed episode list, enclosure URLs, and publish times when matching fails.

Discord messages are prefixed with `【來源名稱｜日期】` so multi-source runs remain readable in the channel. The generated report also includes the public source title and author metadata from the selected feed configuration.
