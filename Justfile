# personal_agent task runner
# Usage: just <target>

set dotenv-load := true

# List available targets
default:
    @just --list

# Run the minimal private Discord bot
bot:
    python -m src.bot.bot

# Watch Python files and restart the bot on change
watch:
    find src -name '*.py' | grep -v __pycache__ | entr -r just bot
