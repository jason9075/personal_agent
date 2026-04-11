"""Logging helpers for the Discord bot."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


LOGGER_NAME = "personal_agent.bot"


def setup_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return log_path


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
