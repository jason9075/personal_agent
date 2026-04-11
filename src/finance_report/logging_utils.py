"""Logging helpers for the finance report pipeline."""
from __future__ import annotations

import logging
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

_CURRENT_LOGGER_NAME: ContextVar[str] = ContextVar("finance_report_logger_name", default="finance_report")


def setup_logging(log_dir: Path, logger_name: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"finance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logger = logging.getLogger(logger_name)
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


def set_current_logger(logger_name: str) -> None:
    _CURRENT_LOGGER_NAME.set(logger_name)


def get_logger() -> logging.Logger:
    return logging.getLogger(_CURRENT_LOGGER_NAME.get())
