"""Play YouTube audio on a paired Bose speaker through a managed mpv process."""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = REPO_ROOT / ".local" / "bose-player"
UNIT = "personal-agent-bose.service"
AUTOPLAY_UNIT = "personal-agent-bose-autoplay.service"
JIAJIA_CHANNEL_ID = "UCgoiUg4LrO28_S0tBafdpcg"
SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "personal-agent-bose.sock"


class BoseUnavailable(RuntimeError):
    """An actionable speaker/adapter failure, distinct from a video failure."""


def connection_message(detail: str) -> str:
    if any(word in detail for word in ("AuthenticationFailed", "AuthenticationRejected", "AuthenticationCanceled")):
        return "Bose 配對驗證失敗。請讓喇叭進入配對模式，重新完成配對後再試。"
    if "InProgress" in detail:
        return "Bose 正在處理另一個連線。請等幾秒再試一次。"
    if "NotReady" in detail:
        return "電腦藍牙尚未就緒。請先開啟藍牙，再重新送出播放指令。"
    return "目前連不到 Bose。請確認喇叭已開機、在電腦附近，並切到藍牙模式；若仍連不上，先中斷它和手機的連線再試。"


def bluetooth_ready() -> None:
    try:
        command(["systemctl", "is-active", "bluetooth.service"])
    except RuntimeError:
        raise BoseUnavailable("電腦的藍牙服務尚未啟動。請先啟用藍牙服務，再重新送出播放指令。") from None
    try:
        adapter = command(["bluetoothctl", "show"])
    except RuntimeError:
        raise BoseUnavailable("電腦目前找不到藍牙裝置。請確認藍牙硬體與驅動已就緒。") from None
    if "Controller " not in adapter:
        raise BoseUnavailable("電腦目前找不到藍牙裝置。請確認藍牙硬體與驅動已就緒。")
    if "Powered: yes" not in adapter:
        raise BoseUnavailable("電腦藍牙目前關閉或被停用。請先開啟藍牙，再重新送出播放指令。")


def speaker_connected(address: str) -> bool:
    try:
        return "Connected: yes" in command(["bluetoothctl", "info", address])
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return False


def command(argv: list[str], timeout: int = 15) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[-800:] or f"{argv[0]} failed")
    return result.stdout


def youtube_target(value: str) -> str:
    """Accept a single YouTube video URL or a search phrase, never local paths."""
    value = value.strip().strip("<>")
    if not value:
        raise ValueError("請提供 YouTube 影片連結或影片名稱。")
    if re.search(r"https?://|://", value):
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"https", "http"}:
            raise ValueError("請提供 YouTube 影片連結。")
        if host in {"youtu.be", "www.youtu.be"}:
            video_id = parsed.path.lstrip("/")
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            parts = parsed.path.strip("/").split("/")
            video_id = parse_qs(parsed.query).get("v", [""])[0]
            if len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
                video_id = parts[1]
        else:
            raise ValueError("目前只支援 YouTube 影片連結。")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError("請提供單支 YouTube 影片連結，不支援播放清單。")
        return f"https://www.youtube.com/watch?v={video_id}"
    if len(value) > 300 or value.startswith(("/", "file:", "--")):
        raise ValueError("請提供 YouTube 影片名稱或連結。")
    return f"ytsearch1:{value}"


def extractor_command() -> list[str]:
    bundled = REPO_ROOT / ".local" / "bose-tools" / "bin" / "yt-dlp"
    ytdlp = str(bundled) if bundled.is_file() else "yt-dlp"
    deno_bundle = REPO_ROOT / ".local" / "bose-deno" / "bin" / "deno"
    deno = str(deno_bundle) if deno_bundle.is_file() else shutil.which("deno")
    if not deno:
        raise RuntimeError("缺少 YouTube 驗證元件，請執行 just bose-player-setup。")
    return [
        ytdlp, "--ignore-config", "--no-playlist", "--no-warnings",
        "--js-runtimes", f"deno:{deno}",
        "--socket-timeout", "15", "--retries", "1",
    ]


def resolve_video(value: str) -> dict[str, Any]:
    target = youtube_target(value)
    raw = command([
        *extractor_command(),
        "--skip-download", "--dump-single-json", "--check-formats",
        "--format", "bestaudio/best[height<=360]/best",
        "--", target,
    ], timeout=90)
    info = json.loads(raw)
    if isinstance(info, dict) and "entries" in info:
        info = next((entry for entry in info["entries"] if entry), None)
    if not isinstance(info, dict) or not info.get("url"):
        if target.startswith("ytsearch1:") and re.search(r"佳佳(?:老師|老师)", value):
            fallback = find_jiajia_episode(value)
            if fallback:
                resolved = resolve_video(fallback)
                if resolved.get("channel_id") != JIAJIA_CHANNEL_ID:
                    raise RuntimeError("找到的影片不屬於佳佳老師原頻道，已取消播放。")
                return resolved
        raise RuntimeError("找不到可播放的影片，請改貼 YouTube 連結。")
    if urlparse(str(info["url"])).scheme not in {"https", "http"}:
        raise RuntimeError("影片沒有可用的音訊串流。")
    return info


def bose_address() -> str:
    address = os.environ.get("BOSE_DEVICE_MAC", "").strip().upper()
    if not address:
        try:
            devices = command(["bluetoothctl", "devices", "Paired"])
        except RuntimeError:
            raise BoseUnavailable("無法讀取 Bose 配對資訊。請先確認電腦藍牙已啟用。") from None
        matches = [line.split()[1] for line in devices.splitlines() if "bose" in line.lower()]
        if not matches:
            raise BoseUnavailable("這台電腦尚未配對 Bose。請先讓喇叭進入配對模式並完成配對，再送出播放指令。")
        if len(matches) > 1:
            raise BoseUnavailable("目前配對了多台 Bose，無法確定要用哪一台。請先設定預設喇叭。")
        address = matches[0]
    if not re.fullmatch(r"(?:[A-F0-9]{2}:){5}[A-F0-9]{2}", address):
        raise BoseUnavailable("播放器的 Bose 裝置設定有誤，請修正預設喇叭設定後再試。")
    return address


def bose_sink() -> str:
    bluetooth_ready()
    address = bose_address()
    if not speaker_connected(address):
        for attempt in range(2):
            try:
                command(["bluetoothctl", "--timeout", "10", "connect", address], timeout=12)
                if not speaker_connected(address):
                    raise RuntimeError("speaker did not connect")
                break
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                if attempt:
                    print(f"Bose connect failed: {exc}", file=sys.stderr)
                    raise BoseUnavailable(connection_message(str(exc))) from None
    for _ in range(20):
        try:
            objects = json.loads(command(["pw-dump"]))
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            raise BoseUnavailable("Bose 已連線，但電腦的音訊服務沒有回應。請重新啟動音訊服務後再試。") from None
        for obj in objects:
            props = obj.get("info", {}).get("props", {})
            if (props.get("media.class") == "Audio/Sink"
                    and props.get("api.bluez5.address", "").upper() == address):
                return str(props["node.name"])
        time.sleep(0.25)
    raise BoseUnavailable("Bose 已連線，但尚未建立音訊輸出。請等幾秒重試；若持續失敗，請關閉再開啟喇叭。")


def ipc(args: list[Any]) -> Any:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(str(SOCKET_PATH))
        client.sendall((json.dumps({"command": args, "request_id": 1}) + "\n").encode())
        with client.makefile("r", encoding="utf-8") as stream:
            for line in stream:
                reply = json.loads(line)
                if reply.get("request_id") == 1:
                    if reply.get("error") != "success":
                        raise RuntimeError(str(reply.get("error")))
                    return reply.get("data")
    raise RuntimeError("播放器沒有回應。")


def stop_unit(unit: str) -> None:
    result = subprocess.run(
        ["systemctl", "--user", "stop", unit], capture_output=True, text=True, timeout=15,
    )
    if result.returncode and "not loaded" not in result.stderr:
        raise RuntimeError(result.stderr.strip() or "無法停止播放器。")


def stop() -> None:
    # Cancel the supervisor before stopping mpv, so EOF cannot restart playback.
    stop_unit(AUTOPLAY_UNIT)
    stop_unit(UNIT)
    SOCKET_PATH.unlink(missing_ok=True)
    (STATE_DIR / "session.json").unlink(missing_ok=True)


def player_failure() -> str:
    log = STATE_DIR / "player.log"
    text = log.read_text(encoding="utf-8", errors="replace")[-16000:] if log.exists() else ""
    match = re.search(r"HTTP error (\d{3})", text)
    if match:
        return f"YouTube 音訊串流回傳 HTTP {match.group(1)}，無法播放；請稍後重試或換一支影片。"
    if re.search(r"\[e\]\[ao[/\]]|Failed to initialize audio", text):
        return "無法開啟 Bose 音訊輸出，請確認喇叭已連線。"
    return "影片音訊未能開始播放。請稍後重試或換一支影片；這不一定是 Bose 連線問題。"


def load_video(info: dict[str, Any], *, update_title: bool = False) -> None:
    headers = [f"{key}: {value}" for key, value in info.get("http_headers", {}).items()]
    ipc(["set_property", "http-header-fields", headers])
    if update_title:
        ipc(["set_property", "force-media-title", str(info.get("title", "YouTube"))])
    ipc(["loadfile", str(info["url"])])


def start_player(info: dict[str, Any], sink: str, *, continuous: bool = False) -> None:
    mpv = shutil.which("mpv")
    if not mpv:
        bundled = REPO_ROOT / ".local" / "bose-mpv" / "bin" / "mpv"
        if not bundled.is_file():
            raise RuntimeError("尚未安裝 mpv，請執行 README 的 Bose 播放器安裝步驟。")
        mpv = str(bundled)
    # Replace only this bot's player; unrelated desktop playback is untouched.
    stop()
    (STATE_DIR / "player.log").unlink(missing_ok=True)
    command([
        "systemd-run", "--user", "--collect", f"--unit={UNIT}",
        "--property=Type=exec", "--property=UMask=0077",
        f"--setenv=XDG_RUNTIME_DIR={SOCKET_PATH.parent}",
        "--", mpv, "--no-config", "--no-video", "--no-terminal", "--ytdl=no",
        "--load-scripts=no", f"--idle={'yes' if continuous else 'once'}", "--volume=40", "--audio-fallback-to-null=no",
        f"--audio-device=pipewire/{sink}", f"--input-ipc-server={SOCKET_PATH}",
        f"--force-media-title={info.get('title', 'YouTube')}",
        f"--log-file={STATE_DIR / 'player.log'}",
    ])
    loaded = False
    # A live process alone doesn't establish that audio opened successfully.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if not loaded:
                load_video(info)
                loaded = True
            if ipc(["get_property", "time-pos"]) is not None and ipc(["get_property", "current-ao"]) == "pipewire":
                return
        except (OSError, RuntimeError):
            pass
        state = command(["systemctl", "--user", "show", UNIT, "--property=ActiveState", "--value"]).strip()
        if state in {"failed", "inactive"}:
            break
        time.sleep(0.25)
    reason = player_failure()
    stop()
    raise RuntimeError(reason)


def read_session() -> dict[str, Any]:
    try:
        return json.loads((STATE_DIR / "session.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_session(state: dict[str, Any]) -> None:
    temporary = STATE_DIR / "session.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(STATE_DIR / "session.json")


def start_autoplay(info: dict[str, Any], *, continuous: bool = True) -> None:
    token = uuid.uuid4().hex
    write_session({
        "token": token, "enabled": continuous, "monitor": True, "channel_id": info.get("channel_id", ""),
        "speaker_address": bose_address(),
        "current_id": info["id"], "seen": [info["id"]], "error": "",
    })
    try:
        command([
            "systemd-run", "--user", "--collect", f"--unit={AUTOPLAY_UNIT}",
            "--description=Bose playback supervisor", "--property=Type=exec", "--property=UMask=0077",
            f"--setenv=XDG_RUNTIME_DIR={SOCKET_PATH.parent}", "--", sys.executable,
            str(Path(__file__).resolve()), "--autoplay-worker", token,
        ])
    except Exception:
        stop()
        raise


def episode_entries(channel_id: str) -> list[dict[str, Any]]:
    if channel_id != JIAJIA_CHANNEL_ID:
        raise ValueError("自動接播目前只支援佳佳老師說故事。")
    raw = command([
        *extractor_command(), "--flat-playlist", "--playlist-end", "200", "--dump-single-json",
        "--", f"https://www.youtube.com/channel/{channel_id}/videos",
    ], timeout=60)
    entries = json.loads(raw).get("entries", [])
    return [
        entry for entry in entries if isinstance(entry, dict)
        and re.fullmatch(r"[A-Za-z0-9_-]{11}", str(entry.get("id", "")))
        and re.search(r"EP\s*\d+", str(entry.get("title", "")), re.IGNORECASE)
        and entry.get("live_status") not in {"is_live", "is_upcoming"}
    ]


def episode_ids(channel_id: str) -> list[str]:
    return list(dict.fromkeys(str(entry["id"]) for entry in episode_entries(channel_id)))


def normalize_title(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", value).casefold() if char.isalnum())


def find_jiajia_episode(query: str) -> str | None:
    """Recover an empty search using titles from the original channel only."""
    needle = normalize_title(query)
    for phrase in ("佳佳老師說故事", "佳佳老师说故事", "佳佳老師", "佳佳老师", "說故事", "说故事"):
        needle = needle.replace(normalize_title(phrase), "")
    entries = episode_entries(JIAJIA_CHANNEL_ID)
    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for entry in entries:
        title = str(entry.get("title", ""))
        story = re.search(r"《(.*?)》", title)
        if story and normalize_title(story.group(1)) == needle:
            exact.append(entry)
        elif needle and needle in normalize_title(title):
            partial.append(entry)
    if exact:
        match = exact[0]
    elif not needle and entries:
        match = entries[0]
    elif len(partial) == 1:
        match = partial[0]
    elif len(partial) > 1:
        titles = "、".join(str(entry.get("title", "")) for entry in partial[:3])
        raise RuntimeError(f"找到多個相近集名，請提供完整名稱或影片連結：{titles}")
    else:
        return None
    return f"https://www.youtube.com/watch?v={match['id']}"


def advance_autoplay(token: str, candidates: list[str]) -> bool:
    """Resolve outside the control lock, then recheck ownership before loading."""
    state = read_session()
    if state.get("token") != token or not state.get("enabled"):
        return False
    seen = set(state.get("seen", []))
    errors: list[str] = []
    for video_id in candidates:
        if video_id in seen:
            continue
        seen.add(video_id)
        try:
            info = resolve_video(f"https://www.youtube.com/watch?v={video_id}")
            if info.get("channel_id") != JIAJIA_CHANNEL_ID:
                raise RuntimeError("接播影片不屬於佳佳老師頻道。")
            with (STATE_DIR / "control.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                current = read_session()
                if current.get("token") != token or not current.get("enabled"):
                    return False
                if current.get("speaker_address") and not speaker_connected(str(current["speaker_address"])):
                    raise BoseUnavailable("Bose 連線已中斷，已停止接播。請確認喇叭已開機並在附近，再重新送出播放指令。")
                if not ipc(["get_property", "idle-active"]):
                    return True
                load_video(info, update_title=True)
                current.update(current_id=video_id, seen=list(seen), error="")
                write_session(current)
                print(f"Autoplay loaded video_id={video_id} title={info.get('title', '')}", flush=True)
                return True
        except BoseUnavailable:
            raise
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))
            print(f"Autoplay skipped video_id={video_id}: {exc}", flush=True)
            if len(errors) >= 3:
                break
    with (STATE_DIR / "control.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = read_session()
        if current.get("token") != token:
            return False
        current.update(enabled=False, monitor=False, seen=list(seen), error=errors[-1] if errors else "")
        write_session(current)
        ipc(["quit"])
    return False


def autoplay_worker(token: str) -> int:
    try:
        state = read_session()
        if state.get("token") != token or not state.get("monitor"):
            return 0
        candidates = episode_ids(str(state["channel_id"])) if state.get("enabled") else []
        print(f"Autoplay ready episodes={len(candidates)}", flush=True)
        while True:
            state = read_session()
            if state.get("token") != token or not state.get("monitor"):
                return 0
            if not speaker_connected(str(state["speaker_address"])):
                raise BoseUnavailable("Bose 連線已中斷，已停止播放與接播。請確認喇叭已開機並在附近，再重新送出播放指令。")
            try:
                idle = ipc(["get_property", "idle-active"])
            except OSError:
                if state.get("enabled"):
                    raise RuntimeError("播放器意外結束，已停止接播。請重新送出播放指令。") from None
                finish_monitor(token)
                return 0  # Single-video mpv exits normally at EOF.
            if idle:
                if not state.get("enabled"):
                    finish_monitor(token)
                    return 0
                if not advance_autoplay(token, candidates):
                    return 1 if read_session().get("error") else 0
            time.sleep(1)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        with (STATE_DIR / "control.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = read_session()
            if state.get("token") == token:
                state.update(enabled=False, monitor=False, error=str(exc))
                write_session(state)
                # Finish the current episode if discovering the next ones failed.
                try:
                    if isinstance(exc, BoseUnavailable):
                        ipc(["quit"])
                    else:
                        ipc(["set_property", "idle", "once"])
                except (OSError, RuntimeError):
                    pass
        print(f"Autoplay failed: {exc}", file=sys.stderr, flush=True)
        return 1


def finish_monitor(token: str) -> None:
    with (STATE_DIR / "control.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_session()
        if state.get("token") == token:
            state.update(monitor=False)
            write_session(state)


def get_volume() -> str:
    """Read speaker and player volumes without reconnecting or changing either."""
    address = bose_address()
    speaker = "Bose 目前未連線，無法讀取喇叭音量；請確認喇叭已開機並在附近"
    for obj in json.loads(command(["pw-dump"])):
        props = obj.get("info", {}).get("props", {})
        if (props.get("media.class") == "Audio/Sink"
                and props.get("api.bluez5.address", "").upper() == address):
            raw = command(["wpctl", "get-volume", str(obj["id"])])
            match = re.search(r"Volume:\s*([\d.]+)", raw)
            if not match:
                raise RuntimeError("無法讀取 Bose 喇叭音量。")
            muted = "（已靜音）" if "MUTED" in raw else ""
            speaker = f"Bose 喇叭音量 {round(float(match.group(1)) * 100)}%{muted}"
            break
    try:
        volume = float(ipc(["get_property", "volume"]))
        muted = "（已靜音）" if ipc(["get_property", "mute"]) else ""
        playback = f"播放器音量 {round(volume)}%{muted}"
    except (OSError, RuntimeError):
        playback = "目前沒有正在播放的音訊"
    return f"{speaker}；{playback}。"


def execute(args: dict[str, Any]) -> str:
    action = str(args.get("action", "play")).lower()
    if action not in {"play", "stop", "pause", "resume", "status", "volume", "get_volume"}:
        raise ValueError("支援播放、停止、暫停、繼續、查詢狀態及調整音量。")
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE_DIR / "control.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == "get_volume" or (action == "volume" and args.get("volume") is None):
            return get_volume()
        if action == "stop":
            stop()
            return "已停止 Bose 播放。"
        if action == "play":
            value = str(args.get("url") or args.get("query") or "")
            # Validate before attempting a Bluetooth connection.
            youtube_target(value)
            sink = bose_sink()
            info = resolve_video(value)
            continuous = info.get("channel_id") == JIAJIA_CHANNEL_ID and args.get("autoplay", True) is not False
            start_player(info, sink, continuous=continuous)
            start_autoplay(info, continuous=continuous)
            suffix = "\n已開啟佳佳老師自動接播；輸入 stop 可停止。" if continuous else ""
            return f"Bose 正在播放：{info.get('title', 'YouTube')}\nhttps://www.youtube.com/watch?v={info['id']}{suffix}"
        try:
            title = ipc(["get_property", "media-title"])
            state = read_session()
            address = str(state.get("speaker_address") or bose_address())
            if not speaker_connected(address):
                stop()
                raise BoseUnavailable("Bose 連線已中斷，無法操作播放。請確認喇叭已開機並在附近，再重新送出播放指令。")
            if action in {"pause", "resume"}:
                ipc(["set_property", "pause", action == "pause"])
                return "已暫停 Bose 播放。" if action == "pause" else "已繼續 Bose 播放。"
            if action == "volume":
                volume = int(args.get("volume", -1))
                if not 0 <= volume <= 100:
                    raise ValueError("音量請指定 0 到 100。")
                ipc(["set_property", "volume", volume])
                return f"Bose 播放音量已設為 {volume}%。"
            paused = ipc(["get_property", "pause"])
            position = int(ipc(["get_property", "time-pos"]) or 0)
            volume = round(float(ipc(["get_property", "volume"])))
            state = read_session()
            autoplay = "；佳佳老師自動接播已開啟" if state.get("enabled") else ""
            if state.get("error"):
                autoplay = f"；自動接播已停止：{state['error']}"
            return f"Bose {'已暫停' if paused else '正在播放'}：{title}（{position // 60}:{position % 60:02d}）；播放器音量 {volume}%{autoplay}。"
        except BoseUnavailable:
            raise
        except (OSError, RuntimeError):
            state = read_session()
            if action == "status" and state.get("error"):
                return f"目前沒有正在播放的 Bose 音訊；{state['error']}"
            return "目前沒有正在播放的 Bose 音訊。請先貼 YouTube 連結或輸入影片名稱。"


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--autoplay-worker":
        return autoplay_worker(sys.argv[2])
    returncode = 0
    try:
        index = sys.argv.index("--args-json")
        payload = json.loads(sys.argv[index + 1])
        args = payload.get("args", {})
        if not isinstance(args, dict):
            raise ValueError("播放器參數格式錯誤。")
        reply = execute(args)
    except subprocess.TimeoutExpired:
        returncode = 1
        reply = "取得影片逾時。請確認網路可用，再重試或換一支影片。"
    except BoseUnavailable as exc:
        returncode = 1
        reply = str(exc)
    except (ValueError, RuntimeError, OSError) as exc:
        returncode = 1
        reply = f"Bose 播放失敗：{exc}"
    print(json.dumps({"kind": "reply", "reply": reply}, ensure_ascii=False))
    return returncode


if __name__ == "__main__":
    sys.exit(main())
