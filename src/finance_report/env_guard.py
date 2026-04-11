"""Runtime checks for common Python environment mismatches."""
from __future__ import annotations

import os
import sys


def assert_clean_pythonpath() -> None:
    pythonpath = os.getenv("PYTHONPATH", "").strip()
    if not pythonpath:
        return

    version_token = f"python{sys.version_info.major}.{sys.version_info.minor}"
    bad_entries = [
        entry for entry in pythonpath.split(":")
        if entry and "site-packages" in entry and version_token not in entry
    ]
    if bad_entries:
        joined = "\n".join(bad_entries[:5])
        raise RuntimeError(
            "Detected incompatible PYTHONPATH entries for this interpreter.\n"
            f"Current Python: {sys.version.split()[0]}\n"
            "Unset PYTHONPATH before running, for example:\n"
            "  env -u PYTHONPATH python -m src.finance_report.runner\n"
            "First mismatched entries:\n"
            f"{joined}"
        )
