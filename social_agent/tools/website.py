"""Read the business website (used by the Interviewer)."""
import html
import re
import urllib.request

MAX_CHARS = 4000


def read_website(url: str) -> str:
    """Fetch a web page and return its plain text (first MAX_CHARS characters)."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (social-agent)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read(500_000).decode("utf-8", errors="ignore")
    except Exception as exc:  # noqa: BLE001 - any network error is reported to the agent
        return f"Could not read {url}: {exc}"
    raw = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_CHARS] or f"{url} has no readable text."
