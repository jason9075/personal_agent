"""Runtime configuration for the minimal private Discord bot."""
import os


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} not set")
    return value


ALLOWED_USER_ID = _require_env("ALLOWED_USER_ID")
