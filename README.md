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
