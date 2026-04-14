"""Run Codex CLI over the transcript to produce a markdown note."""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from .logging_utils import get_logger


PROMPT_TEMPLATE_PATH = Path(__file__).with_name("prompt").joinpath("finance_report_analysis.md")


def analyze_transcript(
    transcript_path: Path,
    note_path: Path,
    codex_output_path: Path,
    target_date: date,
    codex_model: str,
    repo_root: Path,
    source_title: str,
    source_author: str,
) -> str:
    logger = get_logger()
    codex_output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    prompt = prompt_template.format(
        transcript_path=transcript_path,
        note_date=target_date.isoformat(),
        note_path=note_path,
        source_title=source_title,
        source_author=source_author or "未提供",
    )

    cmd = [
        "codex",
        "-m",
        codex_model or "gpt-5.4",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--output-last-message",
        str(codex_output_path),
        "-C",
        str(repo_root),
    ]
    logger.info("Running codex exec for analysis")
    completed = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        capture_output=True,
        cwd=repo_root,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip() or "codex exec failed"
        raise RuntimeError(stderr)

    markdown = codex_output_path.read_text(encoding="utf-8").strip()
    if not markdown:
        raise RuntimeError("codex exec returned an empty note")

    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(markdown + "\n", encoding="utf-8")
    logger.info("Codex analysis output saved to %s and note saved to %s", codex_output_path, note_path)
    return markdown
