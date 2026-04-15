# personal_agent task runner
# Usage: just <target>

set dotenv-load := true

# List available targets
default:
    @just --list

# Run the bot + web server without file watching
bot:
    env -u PYTHONPATH python -m src.bot.bot

# Run the bot + web server with auto-restart on file changes
watch:
    find src nodes -type f \( -name '*.py' -o -name '*.toml' -o -name '*.html' -o -name '*.css' -o -name '*.js' -o -name '*.md' \) | grep -v __pycache__ | entr -r env -u PYTHONPATH python -m src.bot.bot

# Remove generated finance notes
clean:
    rm -rf nodes/finance-report/notes
