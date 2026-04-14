# personal_agent task runner
# Usage: just <target>

set dotenv-load := true

alias fin := finance-report

# List available targets
default:
    @just --list

# Run the minimal private Discord bot
bot:
    find src nodes -type f \( -name '*.py' -o -name '*.toml' -o -name '*.html' -o -name '*.css' -o -name '*.js' -o -name '*.md' \) | grep -v __pycache__ | entr -r env -u PYTHONPATH python -m src.bot.bot

# Run once without file watching
bot-once:
    env -u PYTHONPATH python -m src.bot.bot

# Watch Python files and restart the bot on change
watch:
    find src nodes -type f \( -name '*.py' -o -name '*.toml' -o -name '*.html' -o -name '*.css' -o -name '*.js' -o -name '*.md' \) | grep -v __pycache__ | entr -r env -u PYTHONPATH python -m src.bot.bot

# Remove generated finance notes only
clean:
    rm -rf nodes/finance-report/notes

# List configured finance RSS sources
finance-sources:
    env -u PYTHONPATH python nodes/finance/run.py --list-sources

# Run the RSS-backed finance report pipeline
finance-report source='' target_date='' workers='4':
    env -u PYTHONPATH python nodes/finance-report/run.py --workers {{workers}} {{ if source != '' { '--source ' + source } else { '' } }} {{target_date}}
