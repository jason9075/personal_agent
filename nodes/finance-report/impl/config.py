"""Configuration for the RSS-based finance report pipeline."""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path


def _get_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} not set")
    return value


def _get_optional(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _to_bool(value: str, *, default: bool = False) -> bool:
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "default"


@dataclass(frozen=True)
class FinanceSource:
    source_id: str
    title: str
    author: str
    rss_url: str
    aliases: tuple[str, ...] = ()

    @property
    def slug(self) -> str:
        return _slugify(self.source_id)


@dataclass(frozen=True)
class FinanceConfig:
    source: FinanceSource
    channel_id: str
    download_dir: Path
    transcript_dir: Path
    notes_dir: Path
    codex_output_dir: Path
    log_dir: Path
    debug_dir: Path
    today_keyword_template: str
    today_keyword_overrides: tuple[str, ...]
    request_timeout_seconds: int
    request_user_agent: str
    whisper_model: str
    codex_model: str
    notify_on_no_episode: bool

    def ensure_directories(self) -> None:
        for path in (
            self.download_dir,
            self.transcript_dir,
            self.notes_dir,
            self.codex_output_dir,
            self.log_dir,
            self.debug_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def note_path_for(self, target_date: date) -> Path:
        return self.notes_dir / f"note_{target_date.isoformat()}.md"

    def transcript_path_for(self, target_date: date) -> Path:
        return self.transcript_dir / f"transcript_{target_date.isoformat()}.txt"

    def codex_output_path_for(self, target_date: date) -> Path:
        return self.codex_output_dir / f"codex_{target_date.isoformat()}.md"


def build_today_keywords(target_date: date, template: str, overrides: tuple[str, ...]) -> list[str]:
    if overrides:
        return [item.format(date=target_date) for item in overrides]

    values = {
        "date_iso": target_date.isoformat(),
        "date_slash": target_date.strftime("%Y/%m/%d"),
        "date_compact": target_date.strftime("%Y%m%d"),
        "md_slash": target_date.strftime("%-m/%-d"),
        "md_zh": target_date.strftime("%-m月%-d日"),
        "ymd_zh": target_date.strftime("%Y年%-m月%-d日"),
        "weekday_zh": ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][target_date.weekday()],
    }

    if template:
        return [template.format(**values)]

    return [
        values["date_iso"],
        values["date_slash"],
        values["date_compact"],
        values["md_slash"],
        values["md_zh"],
        values["ymd_zh"],
    ]


def load_config(selected_source_id: str = "") -> FinanceConfig:
    overrides = tuple(
        item.strip()
        for item in _get_optional("FINANCE_TODAY_KEYWORDS").split("||")
        if item.strip()
    )
    source = _load_selected_source(selected_source_id)
    base_download_dir = Path(_get_optional("FINANCE_DOWNLOAD_DIR", ".local/finance/downloads"))
    base_transcript_dir = Path(_get_optional("FINANCE_TRANSCRIPT_DIR", ".local/finance/transcripts"))
    base_notes_dir = Path(_get_optional("FINANCE_OUTPUT_DIR", "nodes/finance-report/notes"))
    base_codex_output_dir = Path(_get_optional("FINANCE_CODEX_OUTPUT_DIR", ".local/finance/codex"))
    base_log_dir = Path(_get_optional("FINANCE_LOG_DIR", ".local/finance/logs"))
    base_debug_dir = Path(_get_optional("FINANCE_DEBUG_DIR", ".local/finance/debug"))

    return FinanceConfig(
        source=source,
        channel_id=_get_required("FINANCE_REPORT_CHANNEL_ID"),
        download_dir=base_download_dir / source.slug,
        transcript_dir=base_transcript_dir / source.slug,
        notes_dir=base_notes_dir / source.slug,
        codex_output_dir=base_codex_output_dir / source.slug,
        log_dir=base_log_dir / source.slug,
        debug_dir=base_debug_dir / source.slug,
        today_keyword_template=_get_optional("FINANCE_TODAY_KEYWORD_TEMPLATE"),
        today_keyword_overrides=overrides,
        request_timeout_seconds=int(_get_optional("FINANCE_REQUEST_TIMEOUT_SECONDS", "60")),
        request_user_agent=_get_optional(
            "FINANCE_REQUEST_USER_AGENT",
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/133.0.0.0 Safari/537.36"
            ),
        ),
        whisper_model=_get_optional("FINANCE_WHISPER_MODEL", "base"),
        codex_model=_get_optional("FINANCE_CODEX_MODEL"),
        notify_on_no_episode=_to_bool(_get_optional("FINANCE_NOTIFY_ON_NO_EPISODE"), default=False),
    )


def list_available_sources() -> list[FinanceSource]:
    sources_file = resolve_sources_file()
    if not sources_file.exists():
        return []
    return _load_sources_file(sources_file)


def load_configs(selected_source_id: str = "") -> list[FinanceConfig]:
    sources = list_available_sources()
    if not sources:
        sources_file = resolve_sources_file()
        raise RuntimeError(
            f"finance sources file not found: {sources_file}. "
            "Create it from nodes/finance/sources.example.toml."
        )

    if selected_source_id:
        source_ids = {source.source_id for source in sources}
        if selected_source_id not in source_ids:
            known = ", ".join(sorted(source_ids))
            raise RuntimeError(f"unknown finance source id {selected_source_id!r}; available: {known}")
        return [load_config(selected_source_id)]

    return [load_config(source.source_id) for source in sources]


def _load_selected_source(selected_source_id: str) -> FinanceSource:
    explicit_source_id = selected_source_id or _get_optional("FINANCE_SOURCE_ID")
    sources_file = resolve_sources_file()

    if not sources_file.exists():
        raise RuntimeError(
            f"finance sources file not found: {sources_file}. "
            "Create it from nodes/finance/sources.example.toml."
        )

    sources = _load_sources_file(sources_file)
    if not sources:
        raise RuntimeError(f"no finance sources defined in {sources_file}")
    if explicit_source_id:
        for source in sources:
            if source.source_id == explicit_source_id:
                return source
        known = ", ".join(source.source_id for source in sources)
        raise RuntimeError(f"unknown finance source id {explicit_source_id!r}; available: {known}")
    if len(sources) == 1:
        return sources[0]
    known = ", ".join(source.source_id for source in sources)
    raise RuntimeError(
        "multiple finance sources configured; select one with "
        f"`--source <id>` or FINANCE_SOURCE_ID. Available: {known}"
    )


def _load_sources_file(path: Path) -> list[FinanceSource]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list):
        raise RuntimeError(f"{path} must define [[sources]] entries")

    sources: list[FinanceSource] = []
    for index, raw_source in enumerate(raw_sources, start=1):
        if not isinstance(raw_source, dict):
            raise RuntimeError(f"{path} sources entry #{index} must be a table")
        source_id = str(raw_source.get("id", "")).strip()
        title = str(raw_source.get("title", "")).strip()
        author = str(raw_source.get("author", "")).strip()
        rss_url = str(raw_source.get("rss_url", "")).strip()
        raw_aliases = raw_source.get("aliases", [])
        if isinstance(raw_aliases, str):
            raw_aliases = [raw_aliases]
        aliases = tuple(
            str(item).strip()
            for item in raw_aliases
            if str(item).strip()
        )
        if not source_id or not title or not rss_url:
            raise RuntimeError(
                f"{path} sources entry #{index} must include id, title, and rss_url"
            )
        sources.append(
            FinanceSource(
                source_id=source_id,
                title=title,
                author=author,
                rss_url=rss_url,
                aliases=aliases,
            )
        )
    return sources


def resolve_sources_file() -> Path:
    explicit = _get_optional("FINANCE_SOURCES_FILE")
    if explicit:
        return Path(explicit)

    preferred = Path("nodes/finance/sources.toml")
    if preferred.exists():
        return preferred

    legacy = Path("config/finance_sources.toml")
    if legacy.exists():
        return legacy

    return preferred


def match_source_from_text(text: str, sources: list[FinanceSource]) -> FinanceSource | None:
    normalized_text = _normalize_match_text(text)
    if not normalized_text:
        return None

    for source in sources:
        if _normalize_match_text(source.source_id) == normalized_text:
            return source

    best: tuple[int, FinanceSource] | None = None
    for source in sources:
        for term in _source_match_terms(source):
            if term and term in normalized_text:
                score = len(term)
                if best is None or score > best[0]:
                    best = (score, source)
    return best[1] if best else None


def _source_match_terms(source: FinanceSource) -> tuple[str, ...]:
    values = [source.source_id, source.title, source.author, *source.aliases]
    terms = []
    for value in values:
        normalized = _normalize_match_text(value)
        if normalized:
            terms.append(normalized)
    return tuple(dict.fromkeys(terms))


def _normalize_match_text(value: str) -> str:
    lowered = value.casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", lowered)
