# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**personal_agent** is a minimal private Discord bot for Jason Kuan.

## Architecture

The active runtime surface is small:

1. **Runtime bot (`src/bot/`)**
   - `bot.py` only responds to the Discord user ID configured via `ALLOWED_USER_ID`
   - It only replies when the bot is tagged
   - It echoes the tagged message content after removing the bot mention
   - If the message contains only the mention, it does nothing

2. **Legacy files**
   - Some older pipeline files may still exist in `src/pipeline/`, but they are not part of the current product behavior
   - Prefer simplifying or deleting unused code rather than extending it

## Development Environment

```bash
nix develop
just bot
just watch
```

Python is provided through the Nix dev shell. Prefer `just` targets where available.
