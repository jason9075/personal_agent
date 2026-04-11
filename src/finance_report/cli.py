"""CLI helpers for finance report commands."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class FinanceCliArgs:
    source_id: str
    target_date: date | None
    list_sources: bool
    workers: int


def parse_cli_args(argv: list[str]) -> FinanceCliArgs:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", dest="source_id", default="")
    parser.add_argument("--list-sources", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("target_date", nargs="?", default="")
    args = parser.parse_args(argv)

    if args.workers <= 0:
        raise SystemExit("workers must be a positive integer")

    if not args.target_date:
        target_date = None
    else:
        value = args.target_date.strip()
        if len(value) == 8 and value.isdigit():
            target_date = date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
        elif len(value) == 10 and value[4] == "-" and value[7] == "-":
            target_date = date.fromisoformat(value)
        else:
            raise SystemExit("target_date must be YYYYMMDD or YYYY-MM-DD")

    return FinanceCliArgs(
        source_id=args.source_id.strip(),
        target_date=target_date,
        list_sources=args.list_sources,
        workers=args.workers,
    )
