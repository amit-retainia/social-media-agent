"""4. Owner approval - the human checks each post.

Steps (from the plan):
  1. The owner sees a preview of the post in the Owner app.
  2. Approve -> the post goes to the Publisher (status "approved" = in the Publisher's queue).
  3. Ask for changes -> the post goes back to the Creator with a short note (status "changes_requested").
  4. The Creator fixes the post and sends it again (a new preview, status "pending_approval").
Posts wait in "pending_approval" until the owner acts.

The Owner app:
  - Grok Bot chat (the default on Grok Bot): each draft goes to Discord (DISCORD_WEBHOOK_URL) as a preview.
    The owner answers in the Grok chat; the bot runs approve_draft or request_changes (main.py --tool).
  - Phone (Telegram bot), when TELEGRAM_BOT_TOKEN and the owner's chat ID are in .env.
    The owner gets the image or video, the text and two buttons: Approve / Ask for changes.
    `python main.py --bot` keeps listening, so the owner can answer at any time.
  - Terminal, when Telegram is off.

Slow approvals (plan "Risks"): a reminder before the post time. If the time passes with no
approval, the post moves to the next free slot and the owner is told.
"""
import json
import secrets
import time
from datetime import timedelta
from pathlib import Path

from .. import config
from ..business_space import BusinessSpace, now_utc, parse_time
from ..tools import discord, telegram
from ..tools.http import ServiceError
from . import creator, planner

MAX_ROUNDS = 5  # Terminal: change rounds per post in one review session.
YES = {"y", "yes", "ok", "okay", "approve", "approved", "👍", "✅", "go", "post it"}
LATER = {"", "later", "skip"}
NO = {"n", "no", "change", "changes"}


# ================= shared core (terminal and phone) =================
def approve(space: BusinessSpace, post_id: str, via: str) -> dict:
    """Step 2: the post goes to the Publisher."""
    post = space.update_post(post_id, status="approved", approved_at=now_utc().isoformat(), approved_via=via)
    planner.write_plan_file(space)
    return post


def request_changes(space: BusinessSpace, post_id: str, note: str, via: str, draft: dict | None = None) -> dict:
    """Steps 3-4: back to the Creator with a short note. The Creator fixes it and sends it again.
    The new text comes from the agent (`draft`). Without it, the post waits in "changes_requested" and the
    answer is "needs_text" (MOCK remakes it with sample text)."""
    post = space.get_post(post_id)
    notes = post.get("change_notes", []) + [note.strip()]
    space.update_post(post_id, status="changes_requested", change_notes=notes, change_requested_via=via)
    if draft is None and not config.MOCK:
        planner.write_plan_file(space)
        return creator.needs_text(space, [space.get_post(post_id)])
    result = creator.make_draft(space, space.get_post(post_id), draft)  # -> status "pending_approval" again
    planner.write_plan_file(space)
    return result


def check_overdue(space: BusinessSpace, app=None) -> list[dict]:
    """Slow approvals: remind before the post time; past the time, move the post to the next free slot."""
    tz = planner.local_tz(space.get_brand_brief())
    now, done = now_utc(), []
    for post in space.posts_with_status("pending_approval", "checking"):
        at = parse_time(post["scheduled_at"])
        if at <= now:
            new = planner.next_free_slot(space, post["platforms"], now, fmt=post["format"], exclude_id=post["id"])
            space.update_post(post["id"], scheduled_at=new.isoformat(), approval_reminded_for=None)
            msg = (f"Post {post['id']} ({post['topic']}) was not approved in time. "
                   f"New time: {new.astimezone(tz):%a %d %b %H:%M}. Please approve it.")
            done.append({"id": post["id"], "result": "moved", "new_time": new.isoformat()})
        elif at - now <= timedelta(hours=config.APPROVAL_REMINDER_HOURS) \
                and post.get("approval_reminded_for") != post["scheduled_at"]:
            space.update_post(post["id"], approval_reminded_for=post["scheduled_at"])
            msg = (f"Reminder: post {post['id']} ({post['topic']}) goes out "
                   f"{at.astimezone(tz):%a %d %b %H:%M} but is not approved yet.")
            done.append({"id": post["id"], "result": "reminded"})
        else:
            continue
        if app is not None:
            app.alert(msg)
        space.add_alert(msg)
    if done:
        planner.write_plan_file(space)
    return done


def run(space: BusinessSpace, app, wait_seconds: float | None = None) -> dict:
    """The Manager's "owner_review" tool."""
    check_overdue(space, app)
    if not space.posts_with_status("pending_approval"):
        return {"status": "nothing_to_review", "message": "No drafts wait for approval."}
    if telegram.enabled(space.tenant_id):
        return _run_phone(space, app, config.APPROVAL_WAIT_SECONDS if wait_seconds is None else wait_seconds)
    return _run_terminal(space, app)


# ================= terminal =================
def _run_terminal(space: BusinessSpace, app) -> dict:
    results = []
    for post in space.posts_with_status("pending_approval"):
        for _ in range(MAX_ROUNDS):
            post = space.get_post(post["id"])
            app.preview(post)  # Step 1
            answer = app.ask("Owner approval",
                             "Type 'yes' to approve, 'later' to skip, or type a short note on what to change:")
            if answer.lower() in YES:  # Step 2
                approve(space, post["id"], via="terminal")
                results.append({"id": post["id"], "result": "approved"})
                break
            if answer.lower() in LATER:
                results.append({"id": post["id"], "result": "still pending"})
                break
            if answer.lower() in NO:
                answer = app.ask("Owner approval", "What should change? (a short note)")
                if answer.lower() in LATER | NO:
                    results.append({"id": post["id"], "result": "still pending"})
                    break
            app.say("Owner approval", "OK. The Creator is fixing it...")
            request_changes(space, post["id"], answer, via="terminal")  # Steps 3-4
        else:
            results.append({"id": post["id"], "result": "still pending (too many rounds)"})
    return {"status": "reviewed", "results": results,
            "next_step": "Publisher posts approved posts at their set time."}


# ================= phone (Telegram) =================
def _run_phone(space: BusinessSpace, app, wait_seconds: float) -> dict:
    sent = send_previews(space)
    ids = [p["id"] for p in space.posts_with_status("pending_approval", "changes_requested")]
    if sent:
        app.say("Owner approval", f"I sent {len(sent)} preview(s) to your phone (Telegram). "
                                  "Tap Approve or Ask for changes.")
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while ids and time.monotonic() < deadline:
        poll(min(25, max(1, int(deadline - time.monotonic()))))
        if not space.posts_with_status("pending_approval", "changes_requested"):
            break
    results = []
    for pid in ids:
        status = space.get_post(pid)["status"]
        results.append({"id": pid, "result": "approved" if status == "approved" else
                        "still pending (answer on your phone at any time; `python main.py --bot` must run)"})
    return {"status": "reviewed", "owner_app": "telegram", "results": results,
            "next_step": "Publisher posts approved posts at their set time."}


def _state_path() -> Path:
    return config.DATA_DIR / "_telegram_state.json"


def _load_state() -> dict:
    path = _state_path()
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    state.setdefault("offset", None)
    state.setdefault("buttons", {})        # token -> {tenant, post, version, chat, message_id}
    state.setdefault("awaiting_note", {})  # chat id -> token
    return state


def _save_state(state: dict) -> None:
    # Keep the file small: drop buttons older than the newest 500.
    if len(state["buttons"]) > 500:
        state["buttons"] = dict(list(state["buttons"].items())[-500:])
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def _preview_text(space: BusinessSpace, post: dict) -> str:
    draft = post.get("draft", {})
    tz = planner.local_tz(space.get_brand_brief())
    when = parse_time(post["scheduled_at"]).astimezone(tz).strftime("%a %d %b %H:%M")
    lines = [f"📝 Draft for approval (version {draft.get('version', 1)})",
             f"{post['format'].title()} · {', '.join(post['platforms'])} · {when}",
             f"Topic: {post['topic']}", "",
             draft.get("final_copy", ""), "",
             draft.get("caption", ""),
             " ".join(draft.get("hashtags", []))]
    if post.get("change_notes"):
        lines += ["", "Your change notes: " + " | ".join(post["change_notes"])]
    if draft.get("simulated"):
        lines += ["", "⚠️ Some media is SIMULATED (a key is missing). It cannot be posted until you add the key."]
    lines += [f"⚠️ {w}" for w in draft.get("warnings", [])]
    lines += ["", "Tap a button. Or reply to this message: 'yes', or a short note on what to change."]
    return "\n".join(lines)


def _preview_files(space: BusinessSpace, post: dict) -> list[Path]:
    """The final files in the shape of the first platform (one shape is enough for a preview)."""
    draft = post.get("draft", {})
    files = [space.root / f for f in draft.get("files", [])]
    shape = (draft.get("sizes") or {}).get(post["platforms"][0], "") if post["platforms"] else ""
    tag = f"_{shape.replace(':', 'x')}_" if shape else ""
    same_shape = [f for f in files if tag and tag in f.name]
    return [f for f in (same_shape or files) if f.exists()]


def send_preview(space: BusinessSpace, post: dict) -> dict:
    """Step 1 on the phone: media, then the text with Approve / Ask for changes buttons."""
    chat = config.telegram_chat_id(space.tenant_id)
    version = post.get("draft", {}).get("version", 1)
    files = _preview_files(space, post)
    shown = False
    try:
        if post["format"] == "carousel":
            shown = bool(telegram.send_album(chat, files))
        if not shown:
            for f in files:
                if telegram.send_file(chat, f, caption=f"{post['topic']} (v{version})"):
                    shown = True
                    break
    except ServiceError as exc:  # Media failed: still send the text, so the owner can decide.
        telegram.send_text(chat, f"⚠️ Could not show the media: {exc}")
    text = _preview_text(space, post)
    if files and not shown:
        text = "(No media to show: simulated or too big for Telegram. Files are in the media library.)\n\n" + text

    state = _load_state()
    token = secrets.token_hex(4)
    msg = telegram.send_text(chat, text, buttons=[[
        {"text": "✅ Approve", "callback_data": f"ok:{token}"},
        {"text": "✏️ Ask for changes", "callback_data": f"ch:{token}"}]])
    state["buttons"][token] = {"tenant": space.tenant_id, "post": post["id"], "version": version,
                               "chat": str(chat), "message_id": msg["message_id"]}
    _save_state(state)
    space.update_post(post["id"], approval_request={"channel": "telegram", "version": version,
                                                    "message_id": msg["message_id"],
                                                    "sent_at": now_utc().isoformat()})
    return {"id": post["id"], "version": version, "message_id": msg["message_id"]}


def send_previews(space: BusinessSpace) -> list[dict]:
    """Send a preview for every pending draft that the owner has not seen yet (this version).
    Telegram when it is set up, else Discord."""
    channel = "telegram" if telegram.enabled(space.tenant_id) else "discord" if discord.enabled() else ""
    if not channel:
        return []
    sent = []
    for post in space.posts_with_status("pending_approval"):
        req = post.get("approval_request") or {}
        if req.get("channel") == channel and req.get("version") == post.get("draft", {}).get("version"):
            continue
        sent.append(send_preview(space, post) if channel == "telegram" else send_discord_preview(space, post))
    return sent


def send_discord_preview(space: BusinessSpace, post: dict) -> dict:
    """The draft's media and text in the Discord channel. The owner answers in the Grok chat."""
    version = post.get("draft", {}).get("version", 1)
    text = _preview_text(space, post).rsplit("\n\n", 1)[0]  # Drop the Telegram button line.
    text = (f"**[{space.tenant_id}] {post['id']}**\n{text}\n\n"
            f"Answer in the Grok chat: \"approve {post['id']}\" or \"change {post['id']}: <what to change>\".")
    try:
        discord.send(text, _preview_files(space, post)[:10])
    except ServiceError:
        discord.send(text + "\n(The media could not be attached. Files are in the media library.)")
    space.update_post(post["id"], approval_request={"channel": "discord", "version": version,
                                                    "sent_at": now_utc().isoformat()})
    return {"id": post["id"], "version": version, "channel": "discord"}


def send_test_question(space: BusinessSpace, test: dict) -> dict:
    """A content idea for a test: the owner taps Yes (it goes to the test queue) or No."""
    from .. import experiments
    chat = config.telegram_chat_id(space.tenant_id)
    state = _load_state()
    token = secrets.token_hex(4)
    msg = telegram.send_text(chat, experiments.question(test) + "\n\nTap a button, or reply 'yes' or 'no'.", buttons=[[
        {"text": "✅ Try it", "callback_data": f"ty:{token}"},
        {"text": "❌ No", "callback_data": f"tn:{token}"}]])
    state["buttons"][token] = {"tenant": space.tenant_id, "test": test["id"], "chat": str(chat),
                               "message_id": msg["message_id"]}
    _save_state(state)
    return {"id": test["id"], "message_id": msg["message_id"]}


def _answer_test_on_phone(entry: dict, yes: bool) -> str:
    from .. import experiments
    try:
        space = BusinessSpace(entry["tenant"])
    except ValueError:
        return "This business is gone."
    out = experiments.answer(space, entry["test"], yes, via="telegram")
    telegram.remove_buttons(entry["chat"], entry["message_id"])
    if "error" in out:
        return "This idea was already decided, or no answer came in time."
    if yes:
        return f"✅ Added to the tests (number {out['queue_position']} in line). I will tell you the result."
    return "❌ OK, not this idea."


def poll(wait_seconds: int = 0) -> int:
    """Read new answers from the phone and act on them. Returns how many updates were handled."""
    updates = telegram.get_updates(_load_state()["offset"], wait_seconds)
    for update in updates:
        state = _load_state()  # Fresh: handling an update also changes the state.
        state["offset"] = update["update_id"] + 1
        _save_state(state)  # Never handle the same update twice, even if one fails.
        try:
            handle_update(update)
        except Exception as exc:  # noqa: BLE001 - one bad update must not stop the bot
            chat = _chat_of(update)
            if chat:
                _try_send(chat, f"⚠️ Something went wrong: {exc}")
    return len(updates)


def _chat_of(update: dict) -> str:
    msg = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
    return str((msg.get("chat") or {}).get("id", ""))


def _try_send(chat: str, text: str) -> None:
    try:
        telegram.send_text(chat, text)
    except ServiceError:
        pass


def _owner_chats() -> set[str]:
    """Only owners (chat IDs from .env) may approve."""
    import os
    chats = {config.TELEGRAM_CHAT_ID} | {v.strip() for k, v in os.environ.items()
                                         if k.startswith("TELEGRAM_CHAT_ID_") and v.strip()}
    return {c for c in chats if c}


def handle_update(update: dict) -> None:
    chat = _chat_of(update)
    if chat not in _owner_chats():
        if update.get("message", {}).get("text", "").startswith("/start"):
            _try_send(chat, f"Hi! Your chat ID is {chat}. Put it in TELEGRAM_CHAT_ID in .env.")
        return  # Not the owner: ignore.
    state = _load_state()

    if "callback_query" in update:
        cq = update["callback_query"]
        action, _, token = (cq.get("data") or "").partition(":")
        entry = state["buttons"].get(token)
        if not entry or entry["chat"] != chat:
            telegram.answer_button(cq["id"], "This preview is old.")
            return
        if entry.get("test"):  # A test idea: Try it / No.
            telegram.answer_button(cq["id"])
            telegram.send_text(chat, _answer_test_on_phone(entry, action == "ty"))
            return
        space, post = _current(entry)
        if post is None:
            telegram.answer_button(cq["id"], "This draft was changed or already decided.")
            telegram.remove_buttons(chat, entry["message_id"])
            return
        if action == "ok":
            telegram.answer_button(cq["id"], "Approved ✅")
            _approve_on_phone(space, post, entry)
        elif action == "ch":
            state["awaiting_note"][chat] = token
            _save_state(state)
            telegram.answer_button(cq["id"])
            telegram.send_text(chat, f"What should change in \"{post['topic']}\"? Send a short note.",
                               force_reply=True, reply_to=entry["message_id"])
        return

    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    if not text:
        return
    if text.startswith("/start") or text.startswith("/help"):
        _try_send(chat, "I send each draft post here. Tap ✅ Approve, or ✏️ Ask for changes and send a short note.")
        return
    # Which preview is this about? A reply to a preview, or the note we asked for.
    replied = (msg.get("reply_to_message") or {}).get("message_id")
    token = next((t for t, e in state["buttons"].items()
                  if replied and e["message_id"] == replied and e["chat"] == chat), None)
    if token is None:
        token = state["awaiting_note"].get(chat)
    if token is None:
        _try_send(chat, "Please reply to a preview, or tap its button.")
        return
    state["awaiting_note"].pop(chat, None)
    _save_state(state)
    entry = state["buttons"].get(token)
    if entry and entry.get("test"):  # A reply to a test idea: yes or no.
        if text.lower() in YES | NO:
            _try_send(chat, _answer_test_on_phone(entry, text.lower() in YES))
        else:
            _try_send(chat, "Please answer 'yes' or 'no' to this test idea.")
        return
    space, post = _current(entry) if entry else (None, None)
    if post is None:
        _try_send(chat, "That draft was changed or already decided. Use the newest preview.")
        return
    if text.lower() in YES:
        _approve_on_phone(space, post, entry)
        return
    if text.lower() in NO:
        state["awaiting_note"][chat] = token
        _save_state(state)
        telegram.send_text(chat, "What should change? Send a short note.", force_reply=True,
                           reply_to=entry["message_id"])
        return
    telegram.remove_buttons(chat, entry["message_id"])
    telegram.send_text(chat, "✏️ Got it. The Creator is fixing it. A new preview comes soon.")
    request_changes(space, post["id"], text, via="telegram")
    if space.get_post(post["id"])["status"] == "pending_approval":  # Else the agent writes it (next create_drafts).
        send_preview(space, space.get_post(post["id"]))


def _current(entry: dict):
    """The post for a preview, only if that preview is still the newest draft waiting for approval."""
    try:
        space = BusinessSpace(entry["tenant"])
    except ValueError:
        return None, None
    post = space.get_post(entry["post"])
    if not post or post.get("status") != "pending_approval" \
            or post.get("draft", {}).get("version", 1) != entry["version"]:
        return space, None
    return space, post


def _approve_on_phone(space: BusinessSpace, post: dict, entry: dict) -> None:
    approve(space, post["id"], via="telegram")
    telegram.remove_buttons(entry["chat"], entry["message_id"])
    tz = planner.local_tz(space.get_brand_brief())
    when = parse_time(post["scheduled_at"]).astimezone(tz).strftime("%a %d %b %H:%M")
    telegram.send_text(entry["chat"], f"✅ Approved: \"{post['topic']}\". "
                                      f"The Publisher posts it {when} on {', '.join(post['platforms'])}.")
