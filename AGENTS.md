# Repository Guidelines

## Project Structure & Module Organization

Core runtime code lives in `src/bot/`. `src/bot/bot.py` is the Discord entrypoint, and `src/bot/config.py` centralizes required environment loading. Prompt text lives under `src/bot/prompt/`. Keep new runtime modules inside `src/bot/` unless they are clearly reusable across features.

Repository-level support files:

- `Justfile`: local development commands
- `flake.nix`: Nix dev shell and Python toolchain
- `.env.example`: required runtime configuration
- `db/`: SQLite runtime state such as scheduler data
- `skills/`: skill definitions and executable tool entrypoints

Bot architecture uses a 2-pass skill flow:

1. Pass 1 routes a tagged Discord message to a skill using `skills/*/SKILL.md`.
2. The selected skill executes a local action, preferably `skills/<skill-name>/run.py`, and emits its result to `stdout`.
3. Pass 2 sends the tool result to `codex exec` to generate the final user-facing reply.

When adding a skill, keep the skill contract explicit:

- `SKILL.md` should describe routing intent, examples, arguments, and expected output shape.
- `SKILL.md` frontmatter should declare `pass2_mode: always | optional | never`.
- `run.py` should do the deterministic work and write machine- or human-readable output to `stdout`.
- Bot code in `src/bot/` should orchestrate routing and final response generation, not embed large feature-specific workflows inline.
- Bot system prompts must live under `src/bot/prompt/` and be loaded from disk at runtime. Do not hardcode prompt bodies in Python modules.

## Build, Test, and Development Commands

Use the Nix shell first so Python and tooling are consistent:

```bash
nix develop
just bot      # run the Discord bot
just watch    # restart on Python file changes
just finance-sources
just fin      # run all finance sources with default workers
ruff check src
mypy src
```

`just --list` shows the maintained task surface. Prefer `just` targets over ad hoc shell commands when a target exists.

## Coding Style & Naming Conventions

Follow existing Python style: 4-space indentation, type hints on public functions, and small modules with direct control flow. Use `snake_case` for functions and variables, `UPPER_SNAKE_CASE` for env-backed constants, and concise module docstrings where behavior is not obvious.

Keep Discord event handlers narrow and push reusable logic into helper functions or new modules under `src/bot/`. Use `ruff` for linting and `mypy` for static checks before opening a PR.

For skills, prefer stable argument parsing and predictable `stdout` output over implicit side effects. If a workflow needs LLM narration, treat raw tool output as Pass 2 input to `codex exec` rather than the final Discord message.
Use `pass2_mode: never` only when `run.py` already emits user-ready text. Use `optional` when some actions are diagnostic and others benefit from synthesis.

## Testing Guidelines

There is no committed automated test suite yet. For now, treat `ruff check src` and `mypy src` as the minimum validation gate, then do a focused manual run with `just bot`.

When adding tests, place them in `tests/` and name files `test_*.py`. Prefer small unit tests around routing, schedule parsing, config loading, and skill argument extraction.

## Commit & Pull Request Guidelines

Git history currently uses short Conventional Commit style messages such as `feat: basic echo function`. Continue with prefixes like `feat:`, `fix:`, and `chore:` followed by a brief imperative summary.

PRs should explain the behavior change, list validation steps, and note any `.env` or Discord permission changes. Include screenshots only when user-visible chat behavior changes in a way that is easier to review visually.

## Security & Configuration Tips

Never commit `.env` or real bot tokens. Keep `DISCORD_BOT_TOKEN` and `ALLOWED_USER_ID` local, and update `.env.example` whenever required configuration changes.

Treat `db/` as runtime state, not source control data. Commit skill definitions and code, but ignore generated SQLite files, transcripts, notes, and other private outputs.
