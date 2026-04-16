"""Webfetch node — uses Playwright to fetch a URL and extract main text content."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_URL_RE = re.compile(r"https?://[^\s<>\"']+")

# Persistent browser profile — stores cookies/localStorage across runs so sites
# treat the bot as a returning user rather than a fresh automation session.
# Excluded from git via .gitignore.
_PROFILE_DIR = Path(__file__).resolve().parent / "profile"

_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-gpu",
    "--window-size=1280,800",
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Injected into every page before scripts run — removes the automation flag
# that sites like Google check to detect headless browsers.
_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-TW', 'zh', 'en-US', 'en']});
window.chrome = {runtime: {}};
"""

# Semantic content selectors tried in priority order before falling back to <body>
_CONTENT_SELECTORS = [
    "article",
    "main",
    "[role='main']",
    ".post-content",
    ".article-content",
    ".entry-content",
    "#content",
    ".content",
]


def main() -> int:
    if "--args-json" not in sys.argv:
        print("usage: run.py --args-json '{...}'", file=sys.stderr)
        return 1

    idx = sys.argv.index("--args-json")
    payload: dict = json.loads(sys.argv[idx + 1])
    args = payload.get("args", {})
    if not isinstance(args, dict):
        args = {}
    message = str(payload.get("message", "")).strip()
    prev_output = str(payload.get("prev_output", "")).strip()
    explicit_url = str(args.get("url") or payload.get("url", "")).strip()
    args_message = str(args.get("message", "")).strip()

    # Extract URL — user message takes priority over prev_output
    url = _extract_url(explicit_url) or _extract_url(args_message) or _extract_url(message) or _extract_url(prev_output)
    if not url:
        print(json.dumps({"kind": "reply", "reply": "請提供要抓取的網址（https://...）。"}, ensure_ascii=False))
        return 0

    try:
        title, content = _fetch_page(url)
    except Exception as exc:
        print(json.dumps({"kind": "reply", "reply": f"抓取失敗：{exc}"}, ensure_ascii=False))
        return 0

    # Output as reply; if this node has a successor edge the engine will pass
    # this text as prev_output to the next node automatically.
    reply = f"**{title}**\n來源：{url}\n\n{content}"
    print(json.dumps({"kind": "reply", "reply": reply}, ensure_ascii=False))
    return 0


def _extract_url(text: str) -> str | None:
    if not text:
        return None
    m = _URL_RE.search(text)
    # Strip common trailing punctuation that may be part of surrounding sentence
    return m.group(0).rstrip(".,;:)\"'") if m else None


def _fetch_page(url: str) -> tuple[str, str]:
    from playwright.sync_api import sync_playwright

    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # Persistent context keeps cookies/session state between runs and is
        # harder to fingerprint than a fresh ephemeral browser instance.
        context = p.chromium.launch_persistent_context(
            str(_PROFILE_DIR),
            headless=True,
            args=_LAUNCH_ARGS,
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="zh-TW",
            timezone_id="Asia/Taipei",
        )
        try:
            page = context.new_page()
            page.add_init_script(_STEALTH_SCRIPT)
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            title = page.title() or url
            content = _extract_content(page)
        finally:
            context.close()

    return title, content


def _extract_content(page) -> str:
    """Try semantic selectors first; fall back to full <body> text."""
    for selector in _CONTENT_SELECTORS:
        try:
            el = page.query_selector(selector)
            if el:
                text = el.inner_text().strip()
                if len(text) > 200:
                    return text[:8000]
        except Exception:
            continue
    try:
        return page.inner_text("body")[:8000]
    except Exception:
        return "(無法擷取內容)"


if __name__ == "__main__":
    sys.exit(main())
