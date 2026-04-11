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

For auto-restart during development:

```bash
just watch
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
