"""Finance decision node executor."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    payload = _parse_payload()
    via_args_json = "--args-json" in sys.argv
    node_dir = Path(__file__).resolve().parent
    report_node_dir = node_dir.parent / "finance-report"
    if str(report_node_dir) not in sys.path:
        sys.path.insert(0, str(report_node_dir))

    from impl.config import list_available_sources, match_source_from_text

    message = str(payload.get("message", "")).strip()
    explicit_source = str(payload.get("source", "")).strip()
    explicit_target_date = str(payload.get("target_date", "")).strip()
    explicit_list_sources = bool(payload.get("list_sources", False))
    workers = int(payload.get("workers", 4) or 4)

    sources = list_available_sources()
    note_index = _build_note_index(node_dir.parent / "finance-report" / "notes", sources)

    if explicit_list_sources:
        if via_args_json:
            print(json.dumps({"decision": "reply", "reply": _format_source_list(sources)}, ensure_ascii=False))
        else:
            print(_format_source_list(sources))
        return 0

    if workers <= 0:
        raise SystemExit("workers must be a positive integer")

    selected_source = None
    if explicit_source:
        selected_source = next((source for source in sources if source.source_id == explicit_source), None)
        if selected_source is None:
            known = ", ".join(source.source_id for source in sources) if sources else "(none)"
            raise SystemExit(f"unknown finance source id {explicit_source!r}; available: {known}")
    else:
        selected_source = match_source_from_text(message, sources)

    default_args: dict[str, object] = {}
    if selected_source:
        default_args["source"] = selected_source.source_id
    if explicit_target_date:
        default_args["target_date"] = explicit_target_date
    if workers:
        default_args["workers"] = workers

    run_output = json.dumps(
        {
            "explicit_hints": {
                "source": explicit_source,
                "target_date": explicit_target_date,
                "matched_source": selected_source.source_id if selected_source else "",
                "workers": workers,
            },
            "available_rss_sources": [
                {
                    "id": source.source_id,
                    "title": source.title,
                    "author": source.author,
                    "aliases": list(source.aliases),
                }
                for source in sources
            ],
            "existing_note_inventory": note_index,
        },
        ensure_ascii=False,
        indent=2,
    )
    fallback_reply = _format_selector_summary(note_index, selected_source)
    print(
        json.dumps(
            {
                "kind": "llm_request",
                "response_mode": "decision",
                "run_output": run_output,
                "default_args": default_args,
                "metadata": {
                    "node_kind": "finance",
                    "fallback_reply": fallback_reply,
                    "selected_source": selected_source.source_id if selected_source else "",
                    "target_date": explicit_target_date,
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


def _parse_payload() -> dict:
    if "--args-json" in sys.argv:
        idx = sys.argv.index("--args-json")
        return json.loads(sys.argv[idx + 1])

    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="")
    parser.add_argument("--target-date", default="")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--list-sources", action="store_true")
    parser.add_argument("message", nargs="?", default="")
    args = parser.parse_args()
    return {
        "message": args.message,
        "source": args.source,
        "target_date": args.target_date,
        "workers": args.workers,
        "list_sources": args.list_sources,
    }


def _format_source_list(sources: list) -> str:
    if not sources:
        return "目前沒有可用的財經 RSS 來源。"

    lines = ["可用財經來源："]
    for source in sources:
        author = f"｜作者：{source.author}" if source.author else ""
        lines.append(f"- {source.source_id}｜{source.title}{author}")
    return "\n".join(lines)


def _build_note_index(notes_dir: Path, sources: list) -> list[dict]:
    rows: list[dict] = []
    for source in sources:
        source_dir = notes_dir / source.source_id
        note_files = sorted(source_dir.glob("note_*.md"), reverse=True)
        latest_note = note_files[0] if note_files else None
        rows.append(
            {
                "source_id": source.source_id,
                "title": source.title,
                "author": source.author,
                "count": len(note_files),
                "latest_note": latest_note.name if latest_note else "",
                "latest_date": _extract_note_date(latest_note.name) if latest_note else "",
            }
        )
    return rows


def _extract_note_date(filename: str) -> str:
    import re

    match = re.search(r"note_(\d{4}-\d{2}-\d{2})\.md$", filename)
    return match.group(1) if match else ""


def _format_selector_summary(note_index: list[dict], selected_source) -> str:
    if selected_source:
        row = next((item for item in note_index if item["source_id"] == selected_source.source_id), None)
        if row and row["count"] > 0:
            return (
                f"目前 `{row['title']}` 已有 {row['count']} 篇筆記，最新是 {row['latest_date']}。"
                "如果你要我跑新的一篇，請直接指定日期或要求生成最新報告。"
            )
        return (
            f"目前已鎖定來源 `{selected_source.title}`，但還沒有現成筆記。"
            "如果要我開始抓最新一篇並分析，請直接要求生成最新報告。"
        )

    available = [row for row in note_index if row["count"] > 0]
    if not available:
        return "目前還沒有現成的 finance 筆記。如果要我開始分析，請指定來源或直接要求生成最新報告。"

    latest_row = max(available, key=lambda row: (_parse_date_or_min(row["latest_date"]), row["source_id"]))
    return (
        f"目前可直接查看的最新筆記來自 `{latest_row['title']}`，日期是 {latest_row['latest_date']}，"
        f"累積共 {latest_row['count']} 篇。若你要我重新跑新的集數，請指定來源或日期。"
    )


def _parse_date_or_min(value: str):
    from datetime import date

    try:
        return date.fromisoformat(value)
    except ValueError:
        return date.min


if __name__ == "__main__":
    sys.exit(main())
