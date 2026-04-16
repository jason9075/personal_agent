from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import io
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CACHE_ROOT = REPO_ROOT / ".local" / "podcast-digest"
STATE_PATH = CACHE_ROOT / "state.json"
MAX_FEED_BYTES = 8 * 1024 * 1024
MAX_AUDIO_BYTES = 512 * 1024 * 1024
USER_AGENT = "personal-agent-podcast-digest/1.0"
WHISPER_TIMEOUT_SECONDS = 6900
WHISPER_MODEL = "base"


def _reply(message: str, metadata: dict[str, Any] | None = None) -> None:
    envelope: dict[str, Any] = {"kind": "reply", "reply": message}
    if metadata:
        envelope["metadata"] = metadata
    print(json.dumps(envelope, ensure_ascii=False))


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _emit_status(
    status: str,
    *,
    reason: str,
    message: str = "",
    source: str = "",
    title: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "message": message,
        "source": source,
        "title": title,
    }
    if extra:
        payload.update(extra)
    _reply(json.dumps(payload, ensure_ascii=False, indent=2))


def _append_log(log_path: Path, event: str, **fields: Any) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": _now_iso(), "event": event, **fields}
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict[str, Any]) -> None:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_http_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _extract_channel_id(value: str) -> str:
    value = value.strip()
    mention = re.fullmatch(r"<#(\d+)>", value)
    if mention:
        return mention.group(1)
    if re.fullmatch(r"\d{10,}", value):
        return value
    return ""


def _read_url(url: str, max_bytes: int, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("download_too_large")
            chunks.append(chunk)
    return b"".join(chunks)


def _text_of(parent: ET.Element, tag: str) -> str:
    child = parent.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _find_audio_url(item: ET.Element) -> str:
    enclosure = item.find("enclosure")
    if enclosure is not None:
        url = str(enclosure.attrib.get("url", "")).strip()
        enclosure_type = str(enclosure.attrib.get("type", "")).lower()
        if url and (not enclosure_type or "audio" in enclosure_type or url.lower().split("?")[0].endswith((".mp3", ".m4a", ".aac", ".wav", ".ogg"))):
            return url
    for child in item:
        if child.tag.lower().endswith("link"):
            href = str(child.attrib.get("href", "")).strip()
            link_type = str(child.attrib.get("type", "")).lower()
            if href and "audio" in link_type:
                return href
    return ""


def _parse_feed(xml_bytes: bytes) -> list[dict[str, str]]:
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else root.findall(".//item")
    episodes: list[dict[str, str]] = []
    for item in items:
        title = _text_of(item, "title")
        guid = _text_of(item, "guid")
        link = _text_of(item, "link")
        published = _text_of(item, "pubDate") or _text_of(item, "published")
        description = _text_of(item, "description") or _text_of(item, "summary")
        audio_url = _find_audio_url(item)
        if not audio_url:
            continue
        stable_id = guid or audio_url or link or title
        episodes.append({
            "title": title,
            "guid": guid,
            "link": link,
            "published": published,
            "description": description,
            "audio_url": audio_url,
            "episode_key": _sha256(stable_id),
        })
    return episodes


def _select_episode(episodes: list[dict[str, str]], title: str) -> dict[str, str] | None:
    if not episodes:
        return None
    needle = title.strip().lower()
    if not needle:
        return episodes[0]
    for episode in episodes:
        if episode.get("title", "").strip().lower() == needle:
            return episode
    for episode in episodes:
        if needle in episode.get("title", "").strip().lower():
            return episode
    return None


def _download_audio(audio_url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = _read_url(audio_url, max_bytes=MAX_AUDIO_BYTES, timeout=120)
    dest.write_bytes(data)


def _safe_file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _get_audio_duration(audio_path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _transcribe(audio_path: Path, out_dir: Path) -> str:
    txt_path = out_dir / f"{audio_path.stem}.txt"
    log_path = out_dir / "transcribe.log"
    stderr_path = out_dir / "whisper.stderr.log"
    stdout_path = out_dir / "whisper.stdout.log"
    if txt_path.exists() and txt_path.read_text(encoding="utf-8").strip():
        _append_log(
            log_path,
            "transcribe_cache_hit",
            transcript_path=str(txt_path),
            transcript_chars=len(txt_path.read_text(encoding="utf-8").strip()),
        )
        return txt_path.read_text(encoding="utf-8").strip()
    cmd = [
        "whisper",
        str(audio_path),
        "--model",
        WHISPER_MODEL,
        "--output_format",
        "txt",
        "--output_dir",
        str(out_dir),
    ]
    audio_duration_seconds = _get_audio_duration(audio_path)
    audio_size_bytes = _safe_file_size(audio_path)
    started_at = time.monotonic()
    _append_log(
        log_path,
        "transcribe_started",
        audio_path=str(audio_path),
        audio_duration_seconds=round(audio_duration_seconds, 3),
        audio_size_bytes=audio_size_bytes,
        command=cmd,
        timeout_seconds=WHISPER_TIMEOUT_SECONDS,
        whisper_model=WHISPER_MODEL,
        transcript_path=str(txt_path),
    )
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=WHISPER_TIMEOUT_SECONDS)
    except FileNotFoundError:
        _append_log(log_path, "transcribe_missing_binary", command=cmd)
        raise RuntimeError("找不到 whisper 指令，請先在環境中安裝或啟用 Whisper。")
    except subprocess.TimeoutExpired as exc:
        stdout_path.write_text((exc.stdout or "")[-20000:], encoding="utf-8")
        stderr_path.write_text((exc.stderr or "")[-20000:], encoding="utf-8")
        _append_log(
            log_path,
            "transcribe_timeout",
            elapsed_seconds=round(time.monotonic() - started_at, 3),
            stdout_log=str(stdout_path),
            stderr_log=str(stderr_path),
            stderr_tail=(exc.stderr or "")[-500:],
        )
        raise RuntimeError(
            "轉錄逾時，請查看 whisper.stderr.log / transcribe.log，或改用較短的單集。"
        )
    except subprocess.CalledProcessError as exc:
        stdout_path.write_text(exc.stdout or "", encoding="utf-8")
        stderr_path.write_text(exc.stderr or "", encoding="utf-8")
        _append_log(
            log_path,
            "transcribe_failed",
            elapsed_seconds=round(time.monotonic() - started_at, 3),
            returncode=exc.returncode,
            stdout_log=str(stdout_path),
            stderr_log=str(stderr_path),
            stderr_tail=(exc.stderr or "")[-500:],
        )
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"Whisper 轉錄失敗：{detail[:500] or '未知錯誤'}")
    except KeyboardInterrupt:
        _append_log(
            log_path,
            "transcribe_cancelled",
            elapsed_seconds=round(time.monotonic() - started_at, 3),
        )
        raise RuntimeError("轉錄被手動中止，請查看 transcribe.log 確認已執行多久。")
    stdout_path.write_text(result.stdout or "", encoding="utf-8")
    stderr_path.write_text(result.stderr or "", encoding="utf-8")
    if not txt_path.exists():
        candidates = sorted(out_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            txt_path = candidates[0]
    if not txt_path.exists():
        _append_log(
            log_path,
            "transcribe_missing_output",
            elapsed_seconds=round(time.monotonic() - started_at, 3),
            stdout_log=str(stdout_path),
            stderr_log=str(stderr_path),
        )
        raise RuntimeError("Whisper 沒有產生逐字稿。")
    transcript = txt_path.read_text(encoding="utf-8").strip()
    if not transcript:
        _append_log(
            log_path,
            "transcribe_empty_output",
            elapsed_seconds=round(time.monotonic() - started_at, 3),
            transcript_path=str(txt_path),
        )
        raise RuntimeError("逐字稿是空的。")
    _append_log(
        log_path,
        "transcribe_completed",
        elapsed_seconds=round(time.monotonic() - started_at, 3),
        stderr_log=str(stderr_path),
        stdout_log=str(stdout_path),
        transcript_chars=len(transcript),
        transcript_path=str(txt_path),
    )
    return transcript


def main() -> int:
    if "--args-json" not in sys.argv:
        print("usage: run.py --args-json '{...}'", file=sys.stderr)
        return 1
    idx = sys.argv.index("--args-json")
    payload: dict[str, Any] = json.loads(sys.argv[idx + 1])
    args = payload.get("args", {})
    if not isinstance(args, dict):
        args = {}
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    source = str(
        args.get("source")
        or args.get("rss_url")
        or args.get("feed_url")
        or payload.get("source", "")
        or payload.get("rss_url", "")
        or payload.get("feed_url", "")
    ).strip()
    title = str(args.get("title") or payload.get("title", "")).strip()
    digest_instruction = str(
        args.get("digest_instruction")
        or args.get("digest")
        or args.get("digest_requirements")
        or payload.get("digest_instruction", "")
    ).strip()
    target_channel_id = _extract_channel_id(
        str(args.get("target_channel_id") or args.get("channel_id") or metadata.get("target_channel_id") or "")
    )
    force = _as_bool(args.get("force") or args.get("rerun"))
    skip_if_already_sent = _as_bool(args.get("skip_if_already_sent"))
    silent_on_error = _as_bool(args.get("silent_on_error"))
    silent_on_no_match = _as_bool(args.get("silent_on_no_match"))
    silent_on_no_new = _as_bool(args.get("silent_on_no_new") or args.get("silent_if_no_new_episode"))
    trigger = str(metadata.get("trigger", "")).strip().lower()
    silent_flags = {
        "silent_on_error": silent_on_error,
        "silent_on_no_match": silent_on_no_match,
        "silent_on_no_new": silent_on_no_new,
        "skip_if_already_sent": skip_if_already_sent,
    }

    if not source:
        _emit_status(
            "error",
            reason="missing_source",
            message="請提供 podcast RSS URL，例如 args.source、args.rss_url 或 args.feed_url。",
            extra={"silent": silent_flags},
        )
        return 0
    if not _is_http_url(source):
        _emit_status(
            "error",
            reason="invalid_source",
            message="source 必須是有效的 http/https RSS URL。",
            source=source,
            extra={"silent": silent_flags},
        )
        return 0

    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    source_key = _sha256(source)[:16]
    state = _load_state()
    source_state = state.setdefault(source_key, {})
    processed = source_state.setdefault("processed_episode_keys", [])
    if not isinstance(processed, list):
        processed = []
        source_state["processed_episode_keys"] = processed

    try:
        feed_xml = _read_url(source, max_bytes=MAX_FEED_BYTES, timeout=60)
        episodes = _parse_feed(feed_xml)
    except urllib.error.URLError as exc:
        _emit_status(
            "error",
            reason="rss_read_failed",
            message=f"讀取 RSS 失敗：{exc}",
            source=source,
            extra={"silent": silent_flags},
        )
        return 0
    except ET.ParseError:
        _emit_status(
            "error",
            reason="rss_parse_failed",
            message="RSS XML 解析失敗，請確認 source 是有效 RSS feed。",
            source=source,
            extra={"silent": silent_flags},
        )
        return 0
    except ValueError as exc:
        if str(exc) == "download_too_large":
            _emit_status(
                "error",
                reason="rss_too_large",
                message="RSS feed 太大，已停止處理。",
                source=source,
                extra={"silent": silent_flags},
            )
            return 0
        _emit_status(
            "error",
            reason="rss_read_failed",
            message=f"讀取 RSS 失敗：{exc}",
            source=source,
            extra={"silent": silent_flags},
        )
        return 0

    episode = _select_episode(episodes, title)
    if episode is None:
        if title:
            _emit_status(
                "skipped",
                reason="episode_not_found",
                message=f"找不到標題符合「{title}」的 podcast 單集。",
                source=source,
                title=title,
                extra={"silent": silent_flags},
            )
        else:
            _emit_status(
                "skipped",
                reason="no_audio_episode",
                message="RSS 裡找不到含 audio enclosure 的 podcast 單集。",
                source=source,
                extra={"silent": silent_flags},
            )
        return 0

    episode_key = episode["episode_key"]
    already_processed = episode_key in processed
    if already_processed and (trigger == "cron" or skip_if_already_sent) and not force:
        _emit_status(
            "skipped",
            reason="episode_already_processed",
            message="這一集已經處理過，略過本次執行。",
            source=source,
            title=episode.get("title", ""),
            extra={
                "episode_key": episode_key,
                "silent": silent_flags,
            },
        )
        return 0

    episode_dir = CACHE_ROOT / source_key / episode_key
    audio_suffix = Path(urllib.parse.urlparse(episode["audio_url"]).path).suffix or ".mp3"
    audio_path = episode_dir / f"audio{audio_suffix}"
    transcript_path = episode_dir / "transcript.txt"
    transcribe_log_path = episode_dir / "transcribe.log"
    whisper_stdout_log_path = episode_dir / "whisper.stdout.log"
    whisper_stderr_log_path = episode_dir / "whisper.stderr.log"
    transcript_cached = transcript_path.exists() and bool(transcript_path.read_text(encoding="utf-8").strip())

    try:
        if not audio_path.exists():
            _download_audio(episode["audio_url"], audio_path)
        if transcript_cached:
            transcript = transcript_path.read_text(encoding="utf-8").strip()
        else:
            transcript = _transcribe(audio_path, episode_dir)
            transcript_path.write_text(transcript, encoding="utf-8")
    except ValueError as exc:
        if str(exc) == "download_too_large":
            _emit_status(
                "error",
                reason="audio_too_large",
                message="音訊檔太大，已停止下載。",
                source=source,
                title=episode.get("title", ""),
                extra={"silent": silent_flags},
            )
            return 0
        _emit_status(
            "error",
            reason="audio_download_failed",
            message=f"下載音訊失敗：{exc}",
            source=source,
            title=episode.get("title", ""),
            extra={"silent": silent_flags},
        )
        return 0
    except urllib.error.URLError as exc:
        _emit_status(
            "error",
            reason="audio_download_failed",
            message=f"下載音訊失敗：{exc}",
            source=source,
            title=episode.get("title", ""),
            extra={"silent": silent_flags},
        )
        return 0
    except RuntimeError as exc:
        _emit_status(
            "error",
            reason="transcribe_failed",
            message=str(exc),
            source=source,
            title=episode.get("title", ""),
            extra={"silent": silent_flags},
        )
        return 0

    now = int(time.time())
    if episode_key not in processed:
        processed.append(episode_key)
    source_state.update({
        "source": source,
        "last_seen_episode_key": episode_key,
        "last_seen_title": episode.get("title", ""),
        "last_seen_guid": episode.get("guid", ""),
        "last_seen_audio_url": episode.get("audio_url", ""),
        "last_success_at": now,
    })
    _save_state(state)

    output = {
        "status": "ready",
        "source": source,
        "requested_title": title,
        "digest_instruction": digest_instruction or "請整理成清楚的繁體中文重點摘要。",
        "target_channel_id": target_channel_id,
        "episode": {
            "title": episode.get("title", ""),
            "guid": episode.get("guid", ""),
            "link": episode.get("link", ""),
            "published": episode.get("published", ""),
            "description": episode.get("description", ""),
            "audio_url": episode.get("audio_url", ""),
            "episode_key": episode_key,
        },
        "dedupe": {
            "already_processed_before_this_run": already_processed,
            "state_path": str(STATE_PATH),
            "cache_dir": str(episode_dir),
        },
        "diagnostics": {
            "audio_duration_seconds": round(_get_audio_duration(audio_path), 3),
            "audio_size_bytes": _safe_file_size(audio_path),
            "transcribe_log_path": str(transcribe_log_path),
            "whisper_stdout_log_path": str(whisper_stdout_log_path),
            "whisper_stderr_log_path": str(whisper_stderr_log_path),
            "transcript_cached": transcript_cached,
            "whisper_model": WHISPER_MODEL,
            "whisper_timeout_seconds": WHISPER_TIMEOUT_SECONDS,
        },
        "silent": silent_flags,
        "schedule_args_template": {
            "source": source,
            "title": title,
            "digest_instruction": digest_instruction or "請整理成清楚的繁體中文重點摘要。",
            "target_channel_id": target_channel_id,
            "force": False,
        },
        "transcript": transcript,
    }
    _reply(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
