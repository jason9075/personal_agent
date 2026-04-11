# Repository Guidelines

## Project Structure & Module Organization

Core runtime code lives in `src/bot/`. `src/bot/bot.py` is the Discord entrypoint, and `src/bot/config.py` centralizes required environment loading. Prompt text lives under `src/bot/prompt/`. Keep new runtime modules inside `src/bot/` unless they are clearly reusable across features.

Repository-level support files:

- `Justfile`: local development commands
- `flake.nix`: Nix dev shell and Python toolchain
- `.env.example`: required runtime configuration
- `db/` and `skills/`: auxiliary project data; avoid coupling bot runtime logic to them unless needed

## Build, Test, and Development Commands

Use the Nix shell first so Python and tooling are consistent:

```bash
nix develop
just bot      # run the Discord bot
just watch    # restart on Python file changes
ruff check src
mypy src
```

`just --list` shows the maintained task surface. Prefer `just` targets over ad hoc shell commands when a target exists.

## Coding Style & Naming Conventions

Follow existing Python style: 4-space indentation, type hints on public functions, and small modules with direct control flow. Use `snake_case` for functions and variables, `UPPER_SNAKE_CASE` for env-backed constants, and concise module docstrings where behavior is not obvious.

Keep Discord event handlers narrow and push reusable logic into helper functions or new modules under `src/bot/`. Use `ruff` for linting and `mypy` for static checks before opening a PR.

## Testing Guidelines

There is no committed automated test suite yet. For now, treat `ruff check src` and `mypy src` as the minimum validation gate, then do a focused manual run with `just bot`.

When adding tests, place them in `tests/` and name files `test_*.py`. Prefer small unit tests around parsing, config loading, and message-filtering behavior.

## Commit & Pull Request Guidelines

Git history currently uses short Conventional Commit style messages such as `feat: basic echo function`. Continue with prefixes like `feat:`, `fix:`, and `chore:` followed by a brief imperative summary.

PRs should explain the behavior change, list validation steps, and note any `.env` or Discord permission changes. Include screenshots only when user-visible chat behavior changes in a way that is easier to review visually.

## Security & Configuration Tips

Never commit `.env` or real bot tokens. Keep `DISCORD_BOT_TOKEN` and `ALLOWED_USER_ID` local, and update `.env.example` whenever required configuration changes.
