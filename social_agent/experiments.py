"""Tests: the usual way vs ONE new idea, one test at a time. All data in experiments.json.

  Queue     Ideas wait here. From the owner, the agent (web trends, other accounts) or the Analyst (formats and
            hours not tried yet, added when the queue is empty). Best first: priority, then oldest.
            Content ideas from the agent or the Scout first need the owner's yes (Telegram buttons, or the chat).
            No answer in IDEA_ASK_DAYS = skipped. No = not asked again for 8 weeks.
  Planner   Puts the running test in about TEST_SHARE (30%) of the week's posts. The other posts use the usual way.
  Decision  The bot, after the Analyst's counter. When the TEST_POSTS (2) test posts have their TEST_CHECKPOINT
            (3 days) numbers, it compares their growth score with the usual posts (median, same platform, same age):
              20% better or more, and most test posts better  -> won: a learning the Planner follows
                                                                 ("Best format: carousel"), the new usual way.
              20% worse or more, and most test posts worse    -> lost: a learning; the idea is parked for 8 weeks.
              else (small gap, or the posts do not agree)     -> 1 more test post. Still unclear -> no difference.
            The owner gets the result. Then the next idea in the queue starts.

A test changes one thing: "format" (post, carousel, reel, video), "hour" (6-22, local time) or "idea" (anything
else: a topic, a hook style, a caption style; the agent writes the post that way).
"""
import uuid
from datetime import timedelta
from statistics import median

from . import config
from .business_space import BusinessSpace, now_utc

CHANGES = ("format", "hour", "idea")
AUTO_FORMATS = ("reel", "carousel", "video", "post")  # Tried in this order when the queue is empty.
AUTO_HOURS = (9, 12, 18, 20)
PRIORITY = {"owner": 3, "agent": 2, "scout": 2, "analyst": 1}
DONE = ("won", "lost", "no_difference")
BASELINE_POSTS = 20  # The usual posts to compare with: the newest 20.


# =============== the queue ===============
def add(space: BusinessSpace, change: str, value, idea: str = "", evidence: str = "", source: str = "agent",
        first: bool = False) -> dict:
    """Put an idea in the queue. first=True: it runs next (the owner asked for it)."""
    change = str(change or "").strip().lower()
    if change not in CHANGES:
        return {"error": f"change must be one of {', '.join(CHANGES)}"}
    value = _clean(change, value if change != "idea" else (value or idea))
    if value is None:
        return {"error": "value: format = post, carousel, reel or video; hour = 6 to 22; idea = a short text"}
    tests = space.get_experiments()
    same = [t for t in tests if t["change"] == change and str(t["value"]).lower() == str(value).lower()]
    waiting = next((t for t in same if t["status"] in ("queued", "running", "needs_ok")), None)
    if waiting:
        return {"status": "already_in_queue", "test": waiting}
    today = now_utc().date().isoformat()
    parked = next((t for t in same if t["status"] in ("lost", "declined") and t.get("retry_after", "") > today), None)
    if parked:
        why = "the owner said no" if parked["status"] == "declined" else f"tested and lost ({parked['result']['lift']})"
        return {"status": "parked", "message": f"Not now: {why}. It can be added again after {parked['retry_after']}."}
    test = _new(change, value, idea, evidence, source, 9 if first else PRIORITY.get(source, 2))
    tests.append(test)
    if needs_owner_ok(test):
        test["status"] = "needs_ok"
        space.save_experiments(tests)
        sent = _ask_owner(space, test)
        return {"status": "waiting_for_owner", "test": test, "asked_on": sent,
                "ask_owner": question(test) + ' Then run answer_test with {"test_id", "answer": "yes" or "no"}.'}
    space.save_experiments(tests)
    return {"status": "queued", "test": test, "queue_position": _queue(tests).index(test) + 1}


# =============== the owner's OK for content ideas ===============
def needs_owner_ok(test: dict) -> bool:
    """Content ideas from the agent or the Scout (trends, other accounts) can be off-brand: the owner decides.
    Formats and hours (safe), and the owner's own ideas, go straight to the queue."""
    return test["change"] == "idea" and test["source"] in ("agent", "scout")


def question(test: dict) -> str:
    why = f"\nWhy: {test['evidence']}" if test.get("evidence") else ""
    return (f"🧪 New test idea: {test['idea']}{why}\nTry it on {config.TEST_POSTS} posts? "
            f"(No answer in {config.IDEA_ASK_DAYS} days = skipped.)")


def answer(space: BusinessSpace, test_id: str, yes: bool, via: str) -> dict:
    """The owner's answer: yes -> the queue. No -> not asked again for TEST_PARK_DAYS."""
    tests = space.get_experiments()
    if _expire(tests):
        space.save_experiments(tests)
    test = next((t for t in tests if t["id"] == test_id), None)
    if not test or test["status"] != "needs_ok":
        late = test and test["status"] == "skipped"
        return {"error": f"{test_id} " + ("was skipped: no answer in time. Add it again with add_test." if late
                                         else "does not wait for an answer. Use show_tests.")}
    test.update(answered_via=via, answered_at=now_utc().isoformat())
    if yes:
        test["status"] = "queued"
    else:
        test.update(status="declined", ended_at=now_utc().isoformat(),
                    retry_after=(now_utc() + timedelta(days=config.TEST_PARK_DAYS)).date().isoformat())
    space.save_experiments(tests)
    out = {"status": test["status"], "test": test}
    if yes:
        out["queue_position"] = _queue(tests).index(test) + 1
    return out


def _ask_owner(space: BusinessSpace, test: dict) -> str:
    """Telegram (Yes / No buttons) when it is set up, else Discord. Returns where it was asked ('' = nowhere:
    the agent asks in the chat)."""
    from .agents import owner_approval
    from .tools import discord, telegram
    from .tools.http import ServiceError
    try:
        if telegram.enabled(space.tenant_id):
            owner_approval.send_test_question(space, test)
            return "telegram"
        if discord.enabled():
            discord.send(f"**[{space.tenant_id}] {test['id']}**\n{question(test)}\n\n"
                         f"Answer in the Grok chat: \"yes {test['id']}\" or \"no {test['id']}\".")
            return "discord"
    except ServiceError:
        pass
    return ""


def drop(space: BusinessSpace, test_id: str) -> dict:
    tests = space.get_experiments()
    test = next((t for t in tests if t["id"] == test_id), None)
    if not test or test["status"] not in ("queued", "running", "needs_ok"):
        return {"error": f"{test_id} is not a queued or running test. Use show_tests."}
    test.update(status="dropped", ended_at=now_utc().isoformat())
    space.save_experiments(tests)
    return {"status": "dropped", "test": test}


def show(space: BusinessSpace) -> dict:
    tests = space.get_experiments()
    return {"running": next((t for t in tests if t["status"] == "running"), None),
            "status": status_line(space),
            "waiting_for_owner": [t for t in tests if t["status"] == "needs_ok"],
            "queue": _queue(tests),
            "results": [t for t in tests if t["status"] in DONE][-10:]}


def proven(space: BusinessSpace) -> list[str]:
    """Test results the Planner must always see, oldest first (the Planner follows the latest line):
    the latest won format and hour, the last 10 won ideas, and lost ideas while they are parked."""
    today = now_utc().date().isoformat()
    tests = sorted((t for t in space.get_experiments() if t["status"] in ("won", "lost")),
                   key=lambda t: t.get("ended_at", ""))
    won = [t for t in tests if t["status"] == "won"]
    keep = [t for t in won if t["change"] == "idea"][-10:]
    for change in ("format", "hour"):
        keep += [t for t in won if t["change"] == change][-1:]
    keep += [t for t in tests if t["status"] == "lost" and t.get("retry_after", "") > today]
    return [_learning(t) for t in sorted(keep, key=lambda t: t.get("ended_at", ""))]


def status_line(space: BusinessSpace) -> str:
    """One line for the owner: the running test and how far it is."""
    test = next((t for t in space.get_experiments() if t["status"] == "running"), None)
    if not test:
        return ""
    posts = {p["id"]: p for p in space.get_calendar()}
    measured = sum(1 for i in test["posts"] if config.TEST_CHECKPOINT in (posts.get(i, {}).get("metrics") or {}))
    return f"Testing: {test['idea']}. {measured} of {test['needed']} test posts measured."


def current(space: BusinessSpace) -> dict | None:
    """The running test. When none runs, the best idea in the queue starts (the Analyst adds ideas if it is empty)."""
    tests = space.get_experiments()
    changed = _expire(tests)
    running = next((t for t in tests if t["status"] == "running"), None)
    if running is None and config.TEST_SHARE > 0:
        if not _queue(tests):
            changed = _auto_ideas(space, tests) or changed
        queue = _queue(tests)
        if queue:
            running = queue[0]
            running.update(status="running", started_at=now_utc().isoformat(), platform=_platform(space),
                           usual=_usual(space, running["change"]))
            changed = True
    if changed:
        space.save_experiments(tests)
    return running


# =============== the Planner ===============
def plan_slots(space: BusinessSpace, n: int) -> tuple[dict | None, int]:
    """The running test and how many of this week's n posts are test posts (about TEST_SHARE, at least 1)."""
    if n < 2 or config.TEST_SHARE <= 0:
        return None, 0  # One post a week: it stays the usual way.
    test = current(space)
    if not test:
        return None, 0
    calendar_ids = {p["id"] for p in space.get_calendar()}
    still_open = test["needed"] - sum(1 for i in test["posts"] if i in calendar_ids)
    return test, max(0, min(still_open, max(1, round(n * config.TEST_SHARE))))


def assign(space: BusinessSpace, test_id: str, post_ids: list[str]) -> None:
    tests = space.get_experiments()
    for t in tests:
        if t["id"] == test_id:
            t["posts"] = t["posts"] + [i for i in post_ids if i not in t["posts"]]
    space.save_experiments(tests)


# =============== the decision ===============
def review(space: BusinessSpace) -> list[str]:
    """Decide the running test when all its posts have their numbers. Returns what happened (the owner gets it too)."""
    from .agents import analyst

    tests = space.get_experiments()
    test = next((t for t in tests if t["status"] == "running"), None)
    if not test:
        return []
    posts = {p["id"]: p for p in space.get_calendar()}
    mine = [posts[i] for i in test["posts"] if i in posts]
    checkpoint = config.TEST_CHECKPOINT
    if len(mine) < test["needed"] or any(checkpoint not in (p.get("metrics") or {}) for p in mine):
        return []  # Test posts still to plan, post or measure.

    tested = {i for t in tests for i in t["posts"]}
    rows = [r for r in analyst._rows(space)
            if r["platform"] == test["platform"] and r["checkpoint"] == checkpoint and not r["late"]]
    test_rows = [r for r in rows if r["post_id"] in test["posts"]]
    usual_rows = sorted((r for r in rows if r["post_id"] not in tested),
                        key=lambda r: posts[r["post_id"]].get("posted_at", ""))[-BASELINE_POSTS:]
    big = [r for r in test_rows + usual_rows if r["reach"] >= config.ANALYST_MIN_REACH]
    fields = [f for f in config.GROWTH_WEIGHTS if big and all(f in r["has"] for r in big)]
    test_scores = [s for r in test_rows if (s := analyst.growth_score(r, fields)) is not None]
    usual_scores = [s for r in usual_rows if (s := analyst.growth_score(r, fields)) is not None]
    if len(usual_scores) < config.ANALYST_MIN_POSTS:
        return []  # Not enough usual posts to compare with yet.

    most = config.TEST_POSTS + config.TEST_EXTRA_POSTS + 2
    missing = test["needed"] - len(test_scores)  # Late numbers or tiny reach: those posts do not count.
    if missing > 0:
        if test["needed"] + missing > most:
            return [_end(space, tests, test, "no_difference", None, test_scores, usual_scores, fields,
                         note="not enough numbers")]
        test["needed"] += missing
        space.save_experiments(tests)
        return [f"Test \"{test['idea']}\": {missing} test post(s) had no usable numbers, {missing} more planned."]

    # Won: the middle test post is 20% better than the middle usual post AND most test posts (both, when there
    # are 2) are better. One lucky post cannot win alone. Lost: the same, worse.
    u = median(usual_scores)
    lift = (median(test_scores) / u - 1) if u else (1.0 if max(test_scores) > 0 else 0.0)
    most_better = sum(s > u for s in test_scores) * 2 > len(test_scores)
    most_worse = sum(s < u for s in test_scores) * 2 > len(test_scores)
    if lift >= config.TEST_LIFT and most_better:
        outcome = "won"
    elif lift <= -config.TEST_LIFT and most_worse:
        outcome = "lost"
    elif not test.get("extended"):
        extra = config.TEST_EXTRA_POSTS
        test.update(extended=True, needed=test["needed"] + extra)
        space.save_experiments(tests)
        return [f"Test \"{test['idea']}\": unclear so far ({lift:+.0%} vs usual). {extra} more test post(s)."]
    else:
        outcome = "no_difference"
    return [_end(space, tests, test, outcome, lift, test_scores, usual_scores, fields)]


def _end(space, tests, test, outcome, lift, test_scores, usual_scores, fields, note="") -> str:
    now = now_utc()
    test.update(status=outcome, ended_at=now.isoformat(), result={
        "lift": None if lift is None else f"{lift:+.0%}",
        "test_scores": test_scores, "usual_score": median(usual_scores),
        "score": ("growth: " + ", ".join(fields)) if fields else "engagement: likes + comments",
        "posts": len(test_scores), "checkpoint": config.TEST_CHECKPOINT, "platform": test["platform"], "note": note})
    if outcome == "lost":
        test["retry_after"] = (now + timedelta(days=config.TEST_PARK_DAYS)).date().isoformat()
    learning = _learning(test)
    space.save_experiments(tests)
    space.add_learnings([learning])
    space.add_alert(f"Test result: {learning}")
    return learning


def _learning(test: dict) -> str:
    """The line the Planner reads. A won format or hour uses the "Best format:" / "Best hour:" lines it follows."""
    r = test["result"]
    proof = f"{r['lift']} growth score vs usual, {r['posts']} test posts on {r['platform']}, {r['checkpoint']}"
    if test["status"] == "won":
        if test["change"] in ("format", "hour"):
            return f"Best {test['change']}: {test['value']} (test won: {proof})"
        return f"Test won: {test['idea']} ({proof}). Do more of this."
    if test["status"] == "lost":
        return f"Test lost: {test['idea']} ({proof}). Do not use it before {test['retry_after']}."
    if r["note"]:
        return f"Test stopped: {test['idea']} ({r['note']} on {r['platform']}). Keep the usual way."
    return f"Test: {test['idea']} made no clear difference ({proof}). Keep the usual way."


# =============== helpers ===============
def _new(change, value, idea, evidence, source, priority) -> dict:
    return {"id": "test-" + uuid.uuid4().hex[:8], "change": change, "value": value,
            "idea": (idea or _describe(change, value)).strip(), "evidence": evidence, "source": source,
            "priority": priority, "status": "queued", "added_at": now_utc().isoformat(), "posts": [],
            "needed": config.TEST_POSTS}


def _describe(change: str, value) -> str:
    if change == "format":
        return f"{value} instead of the usual format"
    if change == "hour":
        return f"posting at {value}:00 instead of the usual time"
    return str(value)


def _clean(change: str, value):
    from .agents.planner import FORMATS
    if change == "format":
        value = str(value or "").strip().lower()
        return value if value in FORMATS else None
    if change == "hour":
        try:
            hour = int(str(value).split(":")[0])
        except ValueError:
            return None
        return hour if 6 <= hour <= 22 else None  # The Planner only follows hours 6-22.
    value = str(value or "").strip()
    return value[:200] or None


def _queue(tests: list[dict]) -> list[dict]:
    return sorted((t for t in tests if t["status"] == "queued"), key=lambda t: (-t["priority"], t["added_at"]))


def _expire(tests: list[dict]) -> bool:
    """Ideas from trends and other accounts get old: drop them after IDEA_EXPIRE_DAYS.
    No answer from the owner in IDEA_ASK_DAYS: skipped (silence is not a yes)."""
    now = now_utc()
    limit = (now - timedelta(days=config.IDEA_EXPIRE_DAYS)).isoformat()
    ask_limit = (now - timedelta(days=config.IDEA_ASK_DAYS)).isoformat()
    old = [t for t in tests if t["status"] == "queued" and t["source"] in ("agent", "scout") and t["added_at"] < limit]
    for t in old:
        t.update(status="dropped", ended_at=now.isoformat(), note="waited too long")
    unanswered = [t for t in tests if t["status"] == "needs_ok" and t["added_at"] < ask_limit]
    for t in unanswered:
        t.update(status="skipped", ended_at=now.isoformat(), note="no answer from the owner")
    return bool(old or unanswered)


def _auto_ideas(space: BusinessSpace, tests: list[dict]) -> bool:
    """Empty queue: the Analyst adds the formats and hours that are not the usual way and not tested lately."""
    from .agents.planner import _learned_preferences, learnings_for
    best_format, best_time = _learned_preferences(learnings_for(space))
    recent = (now_utc() - timedelta(days=config.TEST_PARK_DAYS)).isoformat()

    def tried(change, value):
        return any(t["change"] == change and t["value"] == value and
                   (t["status"] in ("queued", "running") or (t["status"] in DONE and t.get("ended_at", "") > recent))
                   for t in tests)
    ideas = [("format", f) for f in AUTO_FORMATS if f != best_format] + \
            [("hour", h) for h in AUTO_HOURS if not best_time or h != best_time.hour]
    new = [_new(c, v, "", "not tried lately", "analyst", PRIORITY["analyst"]) for c, v in ideas if not tried(c, v)]
    tests.extend(new)
    return bool(new)


def _usual(space: BusinessSpace, change: str) -> str:
    """What the test is compared with (the usual way when it started)."""
    from .agents.planner import _learned_preferences, learnings_for
    best_format, best_time = _learned_preferences(learnings_for(space))
    if change == "format" and best_format:
        return f"best format so far: {best_format}"
    if change == "hour" and best_time:
        return f"best hour so far: {best_time.hour}"
    return "the usual posts"


def _platform(space: BusinessSpace) -> str:
    """Tests are judged on one platform: Instagram if it is connected, else the first one."""
    connected = space.connected_platforms()
    return "instagram" if "instagram" in connected else (connected[0] if connected else "instagram")
