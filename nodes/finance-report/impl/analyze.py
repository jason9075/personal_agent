"""Run Codex CLI over the transcript to produce a markdown note."""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from .llm_log_db import log_llm_call
from .logging_utils import get_logger


PROMPT_TEMPLATE_PATH = Path(__file__).with_name("prompt").joinpath("finance_report_analysis.md")
_LLM_LOG_DB_PATH = Path(__file__).resolve().parents[3] / "db" / "finance_llm_log.sqlite3"


def analyze_transcript(
    transcript_path: Path,
    note_path: Path,
    codex_output_path: Path,
    target_date: date,
    codex_model: str,
    repo_root: Path,
    source_title: str,
    source_author: str,
    source_id: str = "",
    node_prompt_path: str = "",
) -> str:
    logger = get_logger()
    codex_output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    task_prompt = prompt_template.format(
        transcript_path=transcript_path,
        note_date=target_date.isoformat(),
        note_path=note_path,
        source_title=source_title,
        source_author=source_author or "未提供",
    )
    engine_prompt = (repo_root / "src/bot/engine_system_prompt.md").read_text(encoding="utf-8")
    node_prompt = ""
    if node_prompt_path:
        node_prompt = (repo_root / node_prompt_path).read_text(encoding="utf-8")
    run_output = "\n".join([
        "transcription_completed=true",
        f"transcript_path={transcript_path}",
        f"note_path={note_path}",
        f"source_title={source_title}",
        f"source_author={source_author or '未提供'}",
        f"target_date={target_date.isoformat()}",
    ])
    prompt = _compose_prompt(engine_prompt, node_prompt, f"RUN_OUTPUT:\n{run_output}", task_prompt)

    model = codex_model or "gpt-5.4"
    cmd = [
        "codex",
        "-m",
        model,
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
        log_llm_call(
            db_path=_LLM_LOG_DB_PATH,
            source_id=source_id,
            target_date=target_date.isoformat(),
            model=model,
            prompt=prompt,
            response=None,
            success=False,
            error_message=stderr,
        )
        raise RuntimeError(stderr)

    markdown = codex_output_path.read_text(encoding="utf-8").strip()
    if not markdown:
        log_llm_call(
            db_path=_LLM_LOG_DB_PATH,
            source_id=source_id,
            target_date=target_date.isoformat(),
            model=model,
            prompt=prompt,
            response=None,
            success=False,
            error_message="codex exec returned an empty note",
        )
        raise RuntimeError("codex exec returned an empty note")

    log_llm_call(
        db_path=_LLM_LOG_DB_PATH,
        source_id=source_id,
        target_date=target_date.isoformat(),
        model=model,
        prompt=prompt,
        response=markdown,
        success=True,
    )
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(markdown + "\n", encoding="utf-8")
    logger.info("Codex analysis output saved to %s and note saved to %s", codex_output_path, note_path)
    return markdown


def _compose_prompt(*sections: str) -> str:
    return "\n\n".join(section.strip() for section in sections if section and section.strip())
