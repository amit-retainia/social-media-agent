"""`python main.py --bot`: keeps the team running when the chat is closed.

- Listens to the owner's phone (Telegram): Approve / Ask for changes / notes, at any time.
- Every few minutes, for every business: sends new previews, reminds about slow approvals,
  and runs the Publisher (posts what is due, with retries and alerts).
Stop it with Ctrl+C. Run it on an always-on computer or server (see README).
"""
import time

from . import config
from .agents import owner_approval, publisher
from .business_space import BusinessSpace
from .tools import telegram


class LogApp:
    """The Owner app for background runs: alerts go to the phone (via alerts); here we only log."""

    def say(self, who: str, text: str) -> None:
        print(f"{who}: {text}", flush=True)

    def alert(self, text: str) -> None:
        print(f"[ALERT] {text}", flush=True)

    def ask(self, who: str, question: str) -> str:
        return "later"  # Nobody types here. Decisions come from the phone.

    def preview(self, post: dict) -> None:
        pass


def businesses() -> list[BusinessSpace]:
    if not config.DATA_DIR.exists():
        return []
    return [BusinessSpace(d.name) for d in sorted(config.DATA_DIR.iterdir())
            if d.is_dir() and not d.name.startswith(("_", "."))]


def tick(app=None) -> None:
    """One background round for every business."""
    app = app or LogApp()
    for space in businesses():
        try:
            owner_approval.check_overdue(space, app)
            owner_approval.send_previews(space)
            out = publisher.run(space, app)
            for r in out["published"]:
                app.say("Publisher", f"{space.tenant_id}: {r}")
        except Exception as exc:  # noqa: BLE001 - keep going with the other businesses
            app.say("Bot", f"{space.tenant_id}: error: {exc}")


def serve(rounds: int | None = None) -> None:
    app = LogApp()
    if not config.TELEGRAM_BOT_TOKEN:
        print("Telegram is off (no TELEGRAM_BOT_TOKEN). Only the Publisher runs.", flush=True)
    else:
        print("Listening to the owner's phone (Telegram). Ctrl+C stops.", flush=True)
    every = max(30.0, config.BOT_PUBLISH_EVERY_MINUTES * 60)
    next_tick, n = 0.0, 0
    while rounds is None or n < rounds:
        n += 1
        if time.monotonic() >= next_tick:
            tick(app)
            next_tick = time.monotonic() + every
        if config.TELEGRAM_BOT_TOKEN:
            try:
                owner_approval.poll(wait_seconds=25)
            except telegram.ServiceError as exc:
                app.say("Bot", f"Telegram error: {exc}. Trying again in 10 s.")
                time.sleep(10)
        else:
            time.sleep(min(every, 60))


def setup() -> None:
    """Check the bot token and print the chat IDs of people who wrote to the bot."""
    if not config.TELEGRAM_BOT_TOKEN:
        print("Set TELEGRAM_BOT_TOKEN in .env first (from @BotFather in Telegram).")
        return
    try:
        me = telegram.call("getMe")
    except telegram.ServiceError as exc:
        print(f"The bot token does not work: {exc}\nCopy it again from @BotFather into TELEGRAM_BOT_TOKEN.")
        return
    print(f"Bot OK: @{me.get('username')}")
    telegram.call("deleteWebhook")  # getUpdates (used here) does not work while a webhook is set.
    chats = {}
    for u in telegram.get_updates(None, 0):
        msg = u.get("message") or (u.get("callback_query") or {}).get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            chats[str(chat["id"])] = chat.get("first_name") or chat.get("title") or chat.get("username") or ""
    if chats:
        print("Chats that wrote to the bot (put the owner's ID in TELEGRAM_CHAT_ID):")
        for cid, name in chats.items():
            print(f"  {cid}  {name}")
    else:
        print(f"No messages yet. Open https://t.me/{me.get('username')} , send 'hi', then run this again.")
    if config.TELEGRAM_CHAT_ID:
        telegram.send_text(config.TELEGRAM_CHAT_ID, "✅ Your social media team is connected. Drafts come here.")
        print(f"Test message sent to TELEGRAM_CHAT_ID={config.TELEGRAM_CHAT_ID}.")
