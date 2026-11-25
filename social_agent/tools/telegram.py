"""Telegram Bot API: the Owner app on the owner's phone (preview, approve, ask for changes, alerts).

It switches on when TELEGRAM_BOT_TOKEN and the owner's chat ID are in .env.
Docs: https://core.telegram.org/bots/api  (no extra package: plain HTTPS calls)

Setup (once):
  1. In Telegram, talk to @BotFather -> /newbot -> copy the token to TELEGRAM_BOT_TOKEN.
  2. Open your new bot and send it any message (for example "hi").
  3. Run `python main.py --telegram-setup`. It prints your chat ID. Put it in TELEGRAM_CHAT_ID.
"""
import json
from pathlib import Path

from .. import config
from .http import ServiceError, request

API = "https://api.telegram.org"
TEXT_LIMIT = 4096      # sendMessage
CAPTION_LIMIT = 1024   # sendPhoto / sendVideo caption
PHOTO_LIMIT = 10 * 1024 * 1024   # Bot API upload limit for photos
FILE_LIMIT = 50 * 1024 * 1024    # Bot API upload limit for other files
PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_TYPES = {".mp4", ".mov"}


def enabled(tenant_id: str | None = None) -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.telegram_chat_id(tenant_id))


def call(method: str, *, files: dict | None = None, http_timeout: float = 30, **params) -> object:
    """Call one Bot API method. Returns "result". Raises ServiceError."""
    url = f"{config.TELEGRAM_API_URL or API}/bot{config.TELEGRAM_BOT_TOKEN}/{method}"
    if files:  # multipart: JSON-type fields must be sent as strings
        data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items()}
        resp = request("POST", url, service="Telegram", timeout=http_timeout, data=data, files=files)
    else:
        resp = request("POST", url, service="Telegram", timeout=http_timeout, json=params)
    body = resp.json()
    if not body.get("ok", False):
        raise ServiceError(f"Telegram {method}: {body.get('description', body)}")
    return body.get("result")


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def send_text(chat_id: str, text: str, buttons: list | None = None, force_reply: bool = False,
              reply_to: int | None = None) -> dict:
    params: dict = {"chat_id": chat_id, "text": _cut(text, TEXT_LIMIT), "disable_web_page_preview": True}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": buttons}
    elif force_reply:
        params["reply_markup"] = {"force_reply": True, "input_field_placeholder": "What should change?"}
    if reply_to:
        params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    return call("sendMessage", **params)


def send_file(chat_id: str, path: Path, caption: str = "") -> dict | None:
    """Send one photo or video. Returns None if Telegram cannot take it (type or size)."""
    suffix, size = path.suffix.lower(), path.stat().st_size
    if suffix in PHOTO_TYPES and size <= PHOTO_LIMIT:
        method, field = "sendPhoto", "photo"
    elif suffix in VIDEO_TYPES and size <= FILE_LIMIT:
        method, field = "sendVideo", "video"
    else:
        return None
    params = {"chat_id": chat_id}
    if caption:
        params["caption"] = _cut(caption, CAPTION_LIMIT)
    if field == "video":
        params["supports_streaming"] = True
    with path.open("rb") as fh:
        return call(method, files={field: (path.name, fh)}, http_timeout=300, **params)


def send_album(chat_id: str, paths: list[Path]) -> list | None:
    """2-10 photos as one album (a carousel preview)."""
    photos = [p for p in paths if p.suffix.lower() in PHOTO_TYPES and p.stat().st_size <= PHOTO_LIMIT][:10]
    if len(photos) < 2:
        return None
    media = [{"type": "photo", "media": f"attach://f{i}"} for i in range(len(photos))]
    handles = [p.open("rb") for p in photos]
    try:
        files = {f"f{i}": (p.name, h) for i, (p, h) in enumerate(zip(photos, handles))}
        return call("sendMediaGroup", files=files, http_timeout=300, chat_id=chat_id, media=media)
    finally:
        for h in handles:
            h.close()


def get_updates(offset: int | None, wait_seconds: int = 0) -> list[dict]:
    params: dict = {"timeout": wait_seconds, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        params["offset"] = offset
    return call("getUpdates", http_timeout=wait_seconds + 15, **params) or []


def answer_button(callback_id: str, text: str = "") -> None:
    try:
        call("answerCallbackQuery", callback_query_id=callback_id, text=text[:200])
    except ServiceError:
        pass  # The button press is old; nothing to do.


def remove_buttons(chat_id: str, message_id: int) -> None:
    try:
        call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
             reply_markup={"inline_keyboard": []})
    except ServiceError:
        pass  # Already removed, or the message is too old to edit.
