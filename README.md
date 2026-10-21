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

## Bose YouTube playback

In the `bose-control` channel (`1562300132249313290`), the allowed user can send
messages without mentioning the bot. A bare YouTube link or video title means
play; `volume`, `volume 30`, `stop`, `pause`, `resume`, and `status` control the
player. Chinese equivalents work too. Other channels still require a mention.
Override the dedicated channel with `BOSE_CONTROL_CHANNEL_ID` in `.env`.

The dedicated channel uses `bose-router` with `gpt-6-luna` and `max` reasoning.
The entry router delegates to it without an LLM call; other channels retain their
existing model settings. The Bose router declares reasoning effort in its infer
metadata, which the engine passes explicitly to Codex and records in LLM logs.
Its sole outgoing edge is `bose-player`, with a dedicated Bose-only prompt.
Finance, echo, scheduling, and other domains remain separate branches of the
entry router and are never delegated through the Bose branch.

Videos from 佳佳老師說故事 (`UCgoiUg4LrO28_S0tBafdpcg`) automatically continue with
other episodes from that same channel after each finishes. The supervisor checks
up to 200 recent episodes and skips ones already selected in the current session.
Say `只播這一集` to play once. `stop` or playing another video cancels the previous
session; pause/resume and volume changes persist across episodes. `status` shows
whether autoplay is active. Other channels' videos play once.

Mention the bot in Discord to play YouTube audio on this computer's paired Bose:

- `@bot 用 Bose 播放 https://www.youtube.com/watch?v=...`
- `@bot 播放 YT 周杰倫 稻香` (searches and plays the first result)
- `@bot 暫停播放` / `@bot 繼續播放` / `@bot 停止播放`
- `@bot Bose 現在播什麼` / `@bot Bose 音量 30`
- `@bot 目前 Bose 音量？` (reports speaker and player volumes without changing them)
- `@bot 快轉到中間` / `@bot 跳到 2 分 30 秒` / `@bot 跳到 1:02:30`
- `@bot 快轉 30 秒` / `@bot 倒轉 10 秒`
- `@bot 有哪些片段` / `@bot 跳到第 3 段` / `@bot 跳到故事開始那段`

Time controls seek within the current video and preserve pause and volume. The
middle requires a known video duration. Live or unseekable streams report that
seeking is unavailable; positions at or beyond the end are rejected. Rewinding
past the beginning returns to 0:00. These commands also work without a mention
in the Bose control channel.
Chapter queries list the current video's source-provided chapter titles and start
times (10 per page; ask `列出片段第 2 頁` to continue); chapter jumps accept a
1-based number or an unambiguous title. Missing
chapters are reported explicitly. Chapter metadata refreshes with each new video,
including autoplay; older active sessions fetch it on their first chapter query.

Playback uses a `bose-player` tool node reachable from the routers. Only the
existing allowed Discord user can trigger it. One video plays at a time; a new
play request replaces the previous video. Audio is targeted to the Bose output.
Turn the speaker on first; the node reconnects an already paired Bose as needed.
If multiple Bose devices are paired, set `BOSE_DEVICE_MAC` in `.env` and restart.
Search, Bluetooth, and stream failures are reported in the Discord reply.
If YouTube returns no search results for a 佳佳老師 episode name, the player
compares it against the original channel's recent episode titles and resolves
the matching video directly. Ambiguous titles require clarification.

The player uses mpv, Deno, and an isolated yt-dlp installation with its matching
YouTube challenge solver. Install these for the bot service from the repository root:

```bash
just bose-player-setup
```

The setup pins a tested yt-dlp nightly in `nodes/bose-player/requirements.txt`;
it does not replace yt-dlp used by other nodes. Extracted HTTP request headers are
passed to mpv. Formats are checked before playback, so inaccessible direct audio
can fall back to a working video stream (up to 360p preferred, audio playback only).
Playback startup errors report the stream or audio error and mark
the workflow as failed, including scheduled runs.

The player runs independently as the user service `personal-agent-bose.service`,
so the bot can reply while audio continues. Stop it with Discord or
`systemctl --user stop personal-agent-bose.service`. Runtime logs are stored in
`.local/bose-player/player.log`. Restarting the bot does not stop playback.
The continuation supervisor is `personal-agent-bose-autoplay.service`; inspect it
with `journalctl --user -u personal-agent-bose-autoplay.service`. Session state is
stored in `.local/bose-player/session.json`. Three failed candidate lookups stop
continuation and expose the reason through `status`.

All playback sessions run a connection supervisor, including single videos.
If the Bose disconnects, it stops playback and continuation and records an
actionable reason. `status` reports that reason; it does not send unsolicited
Discord notifications. New playback attempts distinguish Bluetooth service/
adapter/power issues, missing or ambiguous pairing, connection timeout or pairing
failure, missing audio output, and video/network errors. An unreachable speaker
response suggests checking power, proximity, Bluetooth mode, and a phone connection
without claiming to know which one caused the failure. Failed commands remain
errors in the workflow log. `stop` works even when the speaker is disconnected.
`status` checks Bluetooth availability and the speaker connection even without a
running player. It never reconnects or wakes the speaker, and distinguishes an
unconnected speaker from a connected speaker with no playback. A disconnected
Bluetooth link alone cannot establish whether the speaker is powered off.
For a connected speaker, `status` also reads link quality (0–255), the raw RSSI
return value, and current transmit power via `hcitool`. You can ask `Bose 連線品質`
or `訊號強度` in the Bose control channel. Link quality is not a transmission
success percentage, and the classic Bluetooth RSSI return value is not an absolute
signal strength in dBm. Missing tools, permissions, unsupported metrics, or query
timeouts appear as `無法取得` without preventing playback status from being shown.

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
Weekly reminders use Monday as the first day of the week, so completing a weekly
cycle schedules the next active cycle for Monday at the configured time.

After each unanswered notification, the displayed `Next Trigger` advances to the
next day at the same fixed time. The original cycle anchor is stored separately
so replying `done` can still close the correct day, week, or month.

Plain `@username` text in a reminder is resolved to `<@user_id>` before sending
when it uniquely matches a member of the target Discord server. Only a direct
reply to the reminder whose complete content is `done` or 👌 can close it; other
reply text is explicitly left incomplete.

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
- Each node has its own `model_name`. The default is `gpt-6.1-sol`, and the web UI colors nodes by model rather than pass index.
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
