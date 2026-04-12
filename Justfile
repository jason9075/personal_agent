# personal_agent task runner
# Usage: just <target>

set dotenv-load := true

alias fin := finance-report

# List available targets
default:
    @just --list

# Run the minimal private Discord bot
bot:
    env -u PYTHONPATH python -m src.bot.bot

# Watch Python files and restart the bot on change
watch:
    find src skills config -type f \( -name '*.py' -o -name '*.toml' \) | grep -v __pycache__ | entr -r env -u PYTHONPATH python -m src.bot.bot

# Remove generated finance notes only
clean:
    rm -rf notes/finance

# List configured finance RSS sources
finance-sources:
    env -u PYTHONPATH python -m src.finance_report.runner --list-sources

# Run the RSS-backed finance report pipeline
finance-report source='' target_date='' workers='4':
    env -u PYTHONPATH python -m src.finance_report.runner --workers {{workers}} {{ if source != '' { '--source ' + source } else { '' } }} {{target_date}}
