"""5. Publisher - posts on schedule.

Steps (from the plan):
  1. Puts approved posts in the queue.
  2. Posts at the set time, through each platform API.
  3. On fail: retries 3 times.
  4. Still fails: alerts the owner. The post goes back to the calendar for a new time.
  5. Replies to comments and messages in the brand voice. Hard or sensitive ones go to the owner.
     The agent (for example Grok Bot) writes the replies: new comments wait in "pending_comments"
     (pending_comments() -> send_replies()). MOCK (demo and tests) replies with a fixed thank-you.

`python main.py --once` (a routine) or `--publish` runs it.
"""
import time
from datetime import timedelta

from .. import config
from ..business_space import BusinessSpace, now_utc, parse_time
from ..tools import platforms
from . import planner

SENSITIVE_WORDS = ["refund", "complain", "complaint", "late", "cold", "sick", "allergy", "allergic",
                   "price", "cost", "lawyer", "legal", "sue", "police", "rude", "scam"]


def run(space: BusinessSpace, app, post_now: bool = False) -> dict:
    # Step 1: queue = approved posts whose time has come (or all approved, if post_now).
    now = now_utc()
    queue = [p for p in space.posts_with_status("approved")
             if post_now or parse_time(p["scheduled_at"]) <= now]
    waiting = len(space.posts_with_status("approved")) - len(queue)

    results = [_publish_one(space, app, post) for post in queue]
    replies = reply_to_comments(space, app)
    planner.write_plan_file(space)  # Keep content_plan.json up to date.
    return {"status": "done", "published": results, "approved_waiting_for_time": waiting,
            "replies": replies}


def _publish_one(space: BusinessSpace, app, post: dict) -> dict:
    done = dict(post.get("publish_results", {}))
    for platform in post["platforms"]:
        if platform in done:
            continue
        # Steps 2-3: post, retry up to 3 times with a wait between tries.
        for attempt in range(1, config.PUBLISH_RETRIES + 1):
            try:
                done[platform] = platforms.publish(platform, post, space) | {"posted_at": now_utc().isoformat()}
                break
            except platforms.PublishError as exc:
                error = exc
                if exc.permanent:  # Retrying will not help (for example a placeholder file).
                    break
                if attempt < config.PUBLISH_RETRIES:
                    time.sleep(config.PUBLISH_RETRY_WAIT)
        else:
            # Step 4: still fails -> alert owner, back to the calendar for a new time.
            # The Planner finds the next free slot (one day later at the earliest).
            after = max(parse_time(post["scheduled_at"]), now_utc()) + timedelta(days=1)
            new_time = planner.next_free_slot(space, [platform], after, exclude_id=post["id"]).isoformat()
            tries = 1 if error.permanent else config.PUBLISH_RETRIES
            msg = f"Post {post['id']} failed on {platform} after {tries} tries ({error}). "
            msg += f"New time: {new_time}."
            if error.login_expired:
                msg += f" Please connect your {platform} account again."
            app.alert(msg)
            space.add_alert(msg)
            space.update_post(post["id"], publish_results=done, scheduled_at=new_time)
            return {"id": post["id"], "result": "failed", "rescheduled_to": new_time, "posted_on": list(done)}

    space.update_post(post["id"], publish_results=done, status="posted",
                      posted_at=now_utc().isoformat(), replied_comment_ids=[])
    return {"id": post["id"], "result": "posted", "platforms": list(done)}


def reply_to_comments(space: BusinessSpace, app) -> list[dict]:
    """Step 5: answer simple comments in the brand voice. Send hard ones to the owner."""
    brief = space.get_brand_brief() or {}
    out = []
    for post in space.posts_with_status("posted"):
        seen = set(post.get("replied_comment_ids", []))
        pending = list(post.get("pending_comments", []))
        log = list(post.get("comment_log", []))  # Answered and sent-to-owner comments (saved to Dexere).
        for platform in post["platforms"]:
            for c in platforms.fetch_new_comments(platform, post, space):
                if c["id"] in seen:
                    continue
                seen.add(c["id"])
                if any(w in c["text"].lower() for w in SENSITIVE_WORDS):
                    msg = f"Hard comment on {platform} ({post['id']}) from {c['from']}: \"{c['text']}\""
                    app.alert(msg)
                    space.add_alert(msg)
                    log.append({"platform": platform, **c, "status": "sent_to_owner", "at": now_utc().isoformat()})
                    out.append({"comment": c["id"], "result": "sent to owner"})
                    continue
                if not config.MOCK:  # The agent writes the reply (show_comments -> reply_comments).
                    pending.append({"platform": platform, "id": c["id"], "from": c["from"], "text": c["text"],
                                    "at": now_utc().isoformat()})
                    out.append({"comment": c["id"], "result": "waits for a reply"})
                    continue
                text = f"Thank you so much! 💛 - {brief.get('business_name', 'the team')}"
                platforms.reply(platform, c["id"], text, space)
                log.append({"platform": platform, **c, "status": "replied", "reply": text, "at": now_utc().isoformat()})
                out.append({"comment": c["id"], "result": "replied", "reply": text})
        space.update_post(post["id"], replied_comment_ids=sorted(seen), pending_comments=pending, comment_log=log)
    return out


REPLY_RULES = ("Reply to each comment for the business. One or two short, friendly sentences in the brand voice "
               "from the brand brief. No prices, promises or facts you do not know.")


def pending_comments(space: BusinessSpace) -> dict:
    """Comments that wait for a reply, with the rules: the agent writes the replies."""
    items = [{"post_id": p["id"], "topic": p["topic"], "comment_id": c["id"], "from": c["from"], "text": c["text"]}
             for p in space.posts_with_status("posted") for c in p.get("pending_comments", [])]
    return {"status": "needs_text" if items else "nothing_to_do", "step": "reply_comments",
            "do": "Write a reply for each comment. Then run reply_comments with "
                  '{"replies": [{"post_id": ..., "comment_id": ..., "text": ...}]}.',
            "rules": REPLY_RULES, "data": {"brand_brief": space.get_brand_brief() or {}, "comments": items}}


def send_replies(space: BusinessSpace, replies: list[dict]) -> list[dict]:
    """Post the agent's replies. Each reply: post_id, comment_id, text."""
    out = []
    for r in replies:
        post = space.get_post(r["post_id"]) or {}
        pending = list(post.get("pending_comments", []))
        c = next((c for c in pending if c["id"] == r["comment_id"]), None)
        if c is None:
            out.append({"comment": r["comment_id"], "result": "not waiting for a reply"})
            continue
        platforms.reply(c["platform"], c["id"], r["text"], space)
        pending.remove(c)
        log = post.get("comment_log", []) + [dict(c, status="replied", reply=r["text"])]
        space.update_post(post["id"], pending_comments=pending, comment_log=log)
        out.append({"comment": c["id"], "result": "replied"})
    return out
