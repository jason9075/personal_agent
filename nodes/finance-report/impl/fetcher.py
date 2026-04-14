"""RSS-backed finance audio discovery and download."""
from __future__ import annotations

import json
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from .config import FinanceConfig, build_today_keywords
from .logging_utils import get_logger


class FinanceFetchError(RuntimeError):
    """Base fetcher error."""


class FeedDownloadError(FinanceFetchError):
    """Raised when the RSS feed or audio file cannot be downloaded."""


class EpisodeNotFoundError(FinanceFetchError):
    """Raised when no matching episode is found for the target date."""


@dataclass(frozen=True)
class FeedEpisode:
    title: str
    published_at: datetime | None
    enclosure_url: str
    enclosure_type: str
    link: str
    guid: str
    description: str

    @property
    def episode_date(self) -> date | None:
        return self.published_at.date() if self.published_at else _extract_date_from_text(self.title)


@dataclass(frozen=True)
class EpisodeSelection:
    episode: FeedEpisode
    feed_channel_title: str
    target_date: date
    is_latest: bool


@dataclass(frozen=True)
class DownloadResult:
    media_path: Path
    matched_episode_text: str
    target_date: date


def resolve_episode(config: FinanceConfig, target_date: date | None) -> EpisodeSelection:
    logger = get_logger()
    logger.info("Downloading RSS feed for source=%s url=%s", config.source.source_id, config.source.rss_url)
    xml_bytes = _download_bytes(
        config.source.rss_url,
        timeout_seconds=config.request_timeout_seconds,
        user_agent=config.request_user_agent,
    )

    logger.info("Parsing RSS feed")
    channel_title, episodes = _parse_feed(xml_bytes)
    selected = _select_episode(episodes, config, target_date)
    effective_date = selected.episode.episode_date or target_date or date.today()

    _save_feed_debug(config.debug_dir, effective_date, xml_bytes)
    _save_feed_summary_debug(config.debug_dir, effective_date, channel_title, episodes, selected.episode)
    logger.info(
        "Resolved episode: source=%s title=%r effective_date=%s latest=%s",
        config.source.source_id,
        selected.episode.title,
        effective_date.isoformat(),
        selected.is_latest,
    )
    return EpisodeSelection(
        episode=selected.episode,
        feed_channel_title=channel_title,
        target_date=effective_date,
        is_latest=selected.is_latest,
    )


def download_episode_media(config: FinanceConfig, selection: EpisodeSelection) -> DownloadResult:
    logger = get_logger()
    episode = selection.episode
    extension = _infer_extension(episode.enclosure_url, episode.enclosure_type)
    target_path = config.download_dir / f"finance_{selection.target_date.isoformat()}{extension}"
    logger.info("Downloading enclosure audio to %s", target_path)
    audio_bytes = _download_bytes(
        episode.enclosure_url,
        timeout_seconds=config.request_timeout_seconds,
        user_agent=config.request_user_agent,
    )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(audio_bytes)
    logger.info("Audio saved to %s (%s bytes)", target_path, len(audio_bytes))
    return DownloadResult(
        media_path=target_path,
        matched_episode_text=episode.title,
        target_date=selection.target_date,
    )


def _download_bytes(url: str, *, timeout_seconds: int, user_agent: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise FeedDownloadError(f"HTTP {exc.code} while downloading {url}: {body[:200]}") from exc
    except urllib.error.URLError as exc:
        raise FeedDownloadError(f"failed to download {url}: {exc.reason}") from exc


def _parse_feed(xml_bytes: bytes) -> tuple[str, list[FeedEpisode]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise FeedDownloadError(f"invalid RSS XML: {exc}") from exc

    channel = _first_child(root, "channel") or root
    channel_title = _child_text(channel, "title")
    items = _children(channel, "item")
    episodes = [_parse_episode(item) for item in items]
    return channel_title, episodes


def _parse_episode(item: ET.Element) -> FeedEpisode:
    title = _child_text(item, "title")
    link = _child_text(item, "link")
    guid = _child_text(item, "guid")
    description = _child_text(item, "description")
    published_at = _parse_datetime(_child_text(item, "pubDate"))

    enclosure = _first_child(item, "enclosure")
    enclosure_url = (enclosure.attrib.get("url", "").strip() if enclosure is not None else "")
    enclosure_type = (enclosure.attrib.get("type", "").strip() if enclosure is not None else "")
    if not enclosure_url:
        raise FeedDownloadError(f"episode {title!r} is missing enclosure url")

    return FeedEpisode(
        title=title,
        published_at=published_at,
        enclosure_url=enclosure_url,
        enclosure_type=enclosure_type,
        link=link,
        guid=guid,
        description=description,
    )


def _select_episode(
    episodes: list[FeedEpisode],
    config: FinanceConfig,
    target_date: date | None,
) -> EpisodeSelection:
    if not episodes:
        raise EpisodeNotFoundError("feed contains no episodes")

    logger = get_logger()
    if target_date is None:
        logger.info("No target date provided; selecting latest feed item")
        return EpisodeSelection(
            episode=episodes[0],
            feed_channel_title="",
            target_date=episodes[0].episode_date or date.today(),
            is_latest=True,
        )

    title_keywords = [item.lower() for item in build_today_keywords(
        target_date,
        config.today_keyword_template,
        config.today_keyword_overrides,
    )]
    logger.info("Episode title keywords for fallback matching: %s", title_keywords)

    dated_matches = [episode for episode in episodes if episode.episode_date == target_date]
    if dated_matches:
        logger.info("Found %s date-based match(es)", len(dated_matches))
        return EpisodeSelection(
            episode=dated_matches[0],
            feed_channel_title="",
            target_date=target_date,
            is_latest=False,
        )

    titled_matches = [
        episode for episode in episodes
        if any(keyword in episode.title.lower() for keyword in title_keywords)
    ]
    if titled_matches:
        logger.info("Found %s title-based match(es)", len(titled_matches))
        resolved_date = titled_matches[0].episode_date or target_date
        return EpisodeSelection(
            episode=titled_matches[0],
            feed_channel_title="",
            target_date=resolved_date,
            is_latest=False,
        )

    samples = [
        {
            "title": episode.title,
            "published_at": episode.published_at.isoformat() if episode.published_at else "",
        }
        for episode in episodes[:10]
    ]
    logger.info("No episode matched; first items: %s", samples)
    raise EpisodeNotFoundError(f"no RSS episode matched {target_date.isoformat()}")


def _save_feed_debug(debug_dir: Path, target_date: date, xml_bytes: bytes) -> None:
    path = debug_dir / f"feed_{target_date.isoformat()}.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(xml_bytes)


def _save_feed_summary_debug(
    debug_dir: Path,
    target_date: date,
    channel_title: str,
    episodes: list[FeedEpisode],
    selected_episode: FeedEpisode,
) -> None:
    path = debug_dir / f"feed_{target_date.isoformat()}.json"
    payload = {
        "channel_title": channel_title,
        "episode_count": len(episodes),
        "selected_episode_title": selected_episode.title,
        "episodes": [
            {
                "title": episode.title,
                "published_at": episode.published_at.isoformat() if episode.published_at else "",
                "episode_date": episode.episode_date.isoformat() if episode.episode_date else "",
                "enclosure_type": episode.enclosure_type,
                "enclosure_url": episode.enclosure_url,
                "link": episode.link,
                "guid": episode.guid,
            }
            for episode in episodes[:20]
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _infer_extension(url: str, content_type: str) -> str:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix
    if suffix:
        return suffix

    normalized = content_type.split(";", 1)[0].strip().lower()
    guessed = mimetypes.guess_extension(normalized)
    if guessed:
        return guessed
    if "mpeg" in normalized or "mp3" in normalized:
        return ".mp3"
    if "mp4" in normalized or "m4a" in normalized or "aac" in normalized:
        return ".m4a"
    return ".bin"


def _parse_datetime(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        pass

    iso_candidate = raw.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_candidate)
    except ValueError:
        pass

    matched_date = _extract_date_from_text(raw)
    if matched_date:
        return datetime(matched_date.year, matched_date.month, matched_date.day)
    return None


def _extract_date_from_text(raw: str) -> date | None:
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", raw)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(element) if _local_name(child.tag) == name]


def _first_child(element: ET.Element, name: str) -> ET.Element | None:
    for child in list(element):
        if _local_name(child.tag) == name:
            return child
    return None


def _child_text(element: ET.Element, name: str) -> str:
    child = _first_child(element, name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()
