"""Discord channel webhook: alerts and draft previews for the owner (send only; owners answer in the Grok chat).

Key: DISCORD_WEBHOOK_URL (Discord channel -> Edit -> Integrations -> Webhooks -> New webhook -> Copy URL).
"""
import json
from pathlib import Path

from .. import config
from .http import request

MAX_TEXT = 2000  # Discord message limit
MAX_FILE_BYTES = 10_000_000  # Bigger files are not attached (the text still goes)


def enabled() -> bool:
    return bool(config.DISCORD_WEBHOOK_URL)


def send(text: str, files: list[Path] | None = None) -> None:
    """One message with up to 10 files. Raises ServiceError on failure."""
    files = [f for f in (files or []) if f.exists() and f.stat().st_size <= MAX_FILE_BYTES][:10]
    payload = {"content": text[:MAX_TEXT], "allowed_mentions": {"parse": []}}
    if not files:
        request("POST", config.DISCORD_WEBHOOK_URL, service="Discord", timeout=30, json=payload)
        return
    request("POST", config.DISCORD_WEBHOOK_URL, service="Discord", timeout=120,
            data={"payload_json": json.dumps(payload)},
            files={f"files[{i}]": (f.name, f.read_bytes()) for i, f in enumerate(files)})
