"""2. Planner - picks topics and schedule. Writes a script per post.

Steps (from the plan):
  1. Reads the brand brief, trends and learnings (learnings come from the Analyst).
  2. Picks topics and formats (post, carousel, reel, video).
  3. Sets dates and times.
  4. Sends the schedule to the content calendar.
  5. Writes a script per post.
     Video or reel: hook, script, caption, hashtags.
       "script" is a list of shots in order: what to SHOW, what to SAY, seconds.
     Text or image post (post, carousel): copy idea, caption, hashtags.
Gets: tasks from the Manager; trends; learnings from the Analyst.
Gives: schedule to the calendar; script to the Creator.

Two kinds of task from the Manager:
  plan_week(...)  a weekly content calendar (posts_per_week from the brand brief).
  run(...)        news from the owner ("I baked a new chocolate cake") -> a post on the next suitable day.
Both save the plan to content_plan.json in the Business space, ready for the Creator.
"""
import json
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .. import config, llm
from ..business_space import BusinessSpace, now_utc, parse_time
from ..tools.trends import get_trends

FORMATS = ["post", "carousel", "reel", "video"]
VIDEO_FORMATS = ("reel", "video")
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# Good local times per format, used when there is no learning about time yet.
DEFAULT_TIMES = {"post": time(9, 0), "carousel": time(12, 30), "reel": time(18, 30), "video": time(19, 0)}
# Weekly patterns: (weekday 0=Mon, format, idea). posts_per_week picks one.
WEEK_TEMPLATES = {
    1: [(2, "reel", "behind_scenes")],
    2: [(0, "post", "product_photo"), (3, "reel", "behind_scenes")],
    3: [(0, "post", "product_photo"), (2, "reel", "behind_scenes"), (4, "carousel", "favourites")],
    4: [(0, "post", "product_photo"), (2, "reel", "behind_scenes"), (4, "carousel", "favourites"),
        (5, "reel", "customer")],
    5: [(0, "post", "product_photo"), (1, "carousel", "tips"), (2, "reel", "behind_scenes"),
        (4, "carousel", "favourites"), (5, "reel", "customer")],
    6: [(0, "post", "product_photo"), (1, "carousel", "tips"), (2, "reel", "behind_scenes"),
        (3, "post", "story"), (4, "carousel", "favourites"), (5, "reel", "customer")],
    7: [(0, "post", "product_photo"), (1, "carousel", "tips"), (2, "reel", "behind_scenes"),
        (3, "post", "story"), (4, "carousel", "favourites"), (5, "reel", "customer"), (6, "video", "day_in_life")],
}

SYSTEM = """You are the Planner in a social media team for one small business.
Pick topics, formats, dates and times, and write one script per post.
Rules:
- Follow the brand brief, its voice and its banned topics. Never invent prices or facts.
- Use the learnings from the Analyst: do more of what works (format, time, platform, topic).
- Use the trends when they fit the brand.
- Formats: post (one image), carousel (3-6 slides), reel (short vertical video), video (longer video).
- Pick scheduled_at from the free slots given (or a nearby time on the same day). One post per day.
- reel / video: fill hook, script, caption, hashtags.
    hook: the first words on screen and said in the first 2 seconds.
    script: the shots in order. Each shot: show (what the camera shows), say (the words said), seconds.
    Show the most eye-catching thing first. Reel: 3-6 shots, 15-30 s. Video: 5-10 shots, 30-90 s.
- post / carousel: fill copy_idea (what the image shows and the story), caption, hashtags.
  carousel: also fill slides (one line per slide).
- caption: 1-3 short sentences in the brand voice, with a call to action.
- hashtags: 5-10, starting with #, mix brand, local and topic tags.
- why: one line on why this topic, format and time."""

SHOT = {"type": "object", "properties": {
    "show": {"type": "string"}, "say": {"type": "string"}, "seconds": {"type": "number"}},
    "required": ["show", "say"]}
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "posts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "format": {"type": "string", "enum": FORMATS},
                    "platforms": {"type": "array", "items": {"type": "string"}},
                    "scheduled_at": {"type": "string", "description": "ISO 8601 date-time with offset"},
                    "why": {"type": "string"},
                    "script": {
                        "type": "object",
                        "properties": {
                            "hook": {"type": "string"},
                            "script": {"type": "array", "items": SHOT},
                            "copy_idea": {"type": "string"},
                            "slides": {"type": "array", "items": {"type": "string"}},
                            "caption": {"type": "string"},
                            "hashtags": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["caption", "hashtags"],
                    },
                },
                "required": ["topic", "format", "platforms", "scheduled_at", "script"],
            },
        }
    },
    "required": ["posts"],
}


# =============== public entry points (the Manager calls these) ===============
def run(space: BusinessSpace, task: str, number_of_posts: int = 1, preferred_day: str = "") -> dict:
    """News or a one-off request: add post(s) on the next suitable day(s)."""
    ctx = _context(space)
    if "error" in ctx:
        return ctx["error"]
    n = max(1, min(int(number_of_posts or 1), 7))
    start = _preferred_start(ctx["tz"], preferred_day)
    fmt = _news_format(ctx, task)
    slots = free_slots(space, ctx["connected"], n, start=start, fmt=fmt, preferred_time=ctx["best_time"])
    if config.MOCK:
        posts = [_mock_news_post(ctx, task, i, slots[i], fmt if i == 0 else "post") for i in range(n)]
    else:
        posts = _claude_plan(ctx, f"News from the owner: {task}\nMake {n} post(s) on the next suitable day(s).",
                             n, slots)
    return _finish(space, ctx, posts, task, kind="news")


def plan_week(space: BusinessSpace, task: str = "", week: str = "next") -> dict:
    """A weekly content calendar: posts_per_week posts, spread over the week."""
    ctx = _context(space)
    if "error" in ctx:
        return ctx["error"]
    tz = ctx["tz"]
    today = datetime.now(tz).date()
    monday = today - timedelta(days=today.weekday())
    if week != "this":
        monday += timedelta(days=7)
    sunday = monday + timedelta(days=6)
    wanted = max(1, min(int(ctx["brief"].get("posts_per_week") or 3), 7))
    already = [p for p in space.get_calendar()
               if p.get("plan_kind") == "week" and monday <= parse_time(p["scheduled_at"]).astimezone(tz).date() <= sunday]
    n = wanted - len(already)
    if n <= 0:
        return {"status": "already_planned", "week_of": monday.isoformat(),
                "message": f"The week of {monday:%d %b} already has {len(already)} planned posts.",
                "plan_file": str(space.root / "content_plan.json")}

    template = _week_template(n, ctx["best_format"])
    earliest = max(now_utc() + timedelta(hours=config.PLANNER_MIN_LEAD_HOURS),
                   datetime.combine(monday, time(0), tz))
    slots = []
    for weekday, fmt, idea in template:
        day = monday + timedelta(days=weekday)
        want = datetime.combine(day, ctx["best_time"] or DEFAULT_TIMES[fmt], tz)
        slot = next_free_slot(space, ctx["connected"], max(want, earliest), fmt=fmt,
                              preferred_time=ctx["best_time"], taken=[s for s, _, _ in slots])
        if slot.astimezone(tz).date() <= sunday:
            slots.append((slot, fmt, idea))
    if not slots:
        return {"status": "no_free_days", "message": f"No free days left in the week of {monday:%d %b}."}

    if config.MOCK:
        posts = [_mock_week_post(ctx, task, idea, fmt, slot, i) for i, (slot, fmt, idea) in enumerate(slots)]
    else:
        request = (f"Plan the week of {monday:%A %d %B %Y}. {len(slots)} posts. Mix the formats. "
                   f"Suggested pattern: {[(DAY_NAMES[s.astimezone(tz).weekday()], f, i) for s, f, i in slots]}. "
                   f"Extra wishes from the owner: {task or 'none'}")
        posts = _claude_plan(ctx, request, len(slots), [s for s, _, _ in slots])
    result = _finish(space, ctx, posts, task or "weekly plan", kind="week")
    result["week_of"] = monday.isoformat()
    return result


def show_plan(space: BusinessSpace) -> dict:
    if not space.get_content_plan():
        return {"status": "no_plan", "message": "No plan yet. Ask the Planner for a weekly plan."}
    write_plan_file(space)  # Refresh: statuses change after the Creator, approval and the Publisher.
    plan = space.get_content_plan()
    return {"plan_file": str(space.root / "content_plan.json"), "calendar_view": plan["calendar_view"],
            "ready_for_creator": plan["ready_for_creator"]}


# =============== scheduling ===============
def local_tz(brief: dict | None) -> ZoneInfo:
    for name in ((brief or {}).get("timezone"), config.TIMEZONE, "UTC"):
        try:
            if name:
                return ZoneInfo(name)
        except Exception:  # noqa: BLE001 - unknown name: try the next one
            continue
    return ZoneInfo("UTC")


def next_free_slot(space: BusinessSpace, platforms: list[str], earliest: datetime, fmt: str = "post",
                   preferred_time: time | None = None, exclude_id: str | None = None,
                   taken: list[datetime] | None = None) -> datetime:
    """The first good time at or after `earliest` on a day with no other post on these platforms.

    Human-like schedule (plan "Risks"): at most one post per platform per day, at a good local time.
    Skips the brief's blackout_dates (YYYY-MM-DD) and closed_days (e.g. "Sunday").
    """
    brief = space.get_brand_brief() or {}
    tz = local_tz(brief)
    earliest = max(earliest, now_utc() + timedelta(hours=config.PLANNER_MIN_LEAD_HOURS)).astimezone(tz)
    blackout = set(brief.get("blackout_dates") or [])
    closed = {d.strip().lower() for d in (brief.get("closed_days") or [])}
    busy: set[date] = {t.astimezone(tz).date() for t in (taken or [])}
    for p in space.get_calendar():
        if p["id"] != exclude_id and p.get("status") != "posted" and set(p["platforms"]) & set(platforms):
            busy.add(parse_time(p["scheduled_at"]).astimezone(tz).date())
    at = preferred_time or DEFAULT_TIMES.get(fmt, time(12, 0))
    for i in range(60):
        day = earliest.date() + timedelta(days=i)
        if day in busy or day.isoformat() in blackout or DAY_NAMES[day.weekday()].lower() in closed:
            continue
        slot = datetime.combine(day, at, tz)
        if slot < earliest:  # Today's good time has passed: use a later time today only if it is still good.
            later = earliest.replace(minute=0, second=0) + timedelta(hours=1)
            if later.date() != day or later.hour > 20:
                continue
            slot = later
        return slot
    return earliest


def free_slots(space: BusinessSpace, platforms: list[str], n: int, start: datetime | None = None,
               fmt: str = "reel", preferred_time: time | None = None) -> list[datetime]:
    slots: list[datetime] = []
    when = start or now_utc()
    for _ in range(n):
        slot = next_free_slot(space, platforms, when, fmt=fmt, preferred_time=preferred_time, taken=slots)
        slots.append(slot)
        when = slot
    return slots


def _preferred_start(tz: ZoneInfo, preferred_day: str) -> datetime:
    """'' / 'today' / 'tomorrow' / a weekday name / YYYY-MM-DD -> the earliest start."""
    now = datetime.now(tz)
    text = (preferred_day or "").strip().lower()
    if not text or text in {"asap", "today", "next", "soon"}:
        return now
    if text == "tomorrow":
        return datetime.combine(now.date() + timedelta(days=1), time(0), tz)
    if text.capitalize() in DAY_NAMES:
        ahead = (DAY_NAMES.index(text.capitalize()) - now.weekday()) % 7
        return datetime.combine(now.date() + timedelta(days=ahead), time(0), tz)
    try:
        return datetime.combine(date.fromisoformat(text), time(0), tz)
    except ValueError:
        return now


# =============== shared steps ===============
def _context(space: BusinessSpace) -> dict:
    """Step 1: read the brand brief, trends and learnings."""
    brief = space.get_brand_brief()
    if not brief:
        return {"error": {"status": "blocked", "message": "No brand brief yet. Run the Interviewer first."}}
    connected = space.connected_platforms()
    if not connected:
        return {"error": {"status": "blocked",
                          "message": "No connected accounts. Run the Interviewer to connect them."}}
    tz = local_tz(brief)
    learnings = [item["learning"] for item in space.get_learnings()[-10:]]
    best_format, best_time = _learned_preferences(learnings)
    return {
        "brief": brief, "connected": connected, "tz": tz,
        "trends": get_trends(brief.get("business_type", ""), datetime.now(tz).date(), brief.get("location", "")),
        "learnings": learnings, "best_format": best_format, "best_time": best_time,
        "calendar": [{k: p[k] for k in ("topic", "format", "platforms", "scheduled_at", "status")}
                     for p in space.get_calendar() if p["status"] != "posted"],
    }


def _learned_preferences(learnings: list[str]) -> tuple[str | None, time | None]:
    """Read the Analyst's 'Best format: reel' and 'Best hour: 18' lines (latest wins)."""
    best_format = best_time = None
    for text in learnings:
        m = re.search(r"best format\W+(post|carousel|reel|video)\b", text, re.I)
        if m:
            best_format = m.group(1).lower()
        m = re.search(r"best hour\W+(\d{1,2})\b", text, re.I)
        if m and 6 <= int(m.group(1)) <= 22:
            best_time = time(int(m.group(1)), 0)
    return best_format, best_time


def _week_template(n: int, best_format: str | None) -> list[tuple[int, str, str]]:
    template = list(WEEK_TEMPLATES[min(n, 7)])
    if best_format and n >= 2 and sum(f == best_format for _, f, _ in template) < 2:
        # Learning loop: do more of what works. Swap the last other-format slot.
        for i in range(len(template) - 1, -1, -1):
            if template[i][1] != best_format:
                template[i] = (template[i][0], best_format, template[i][2])
                break
    return template


def _claude_plan(ctx: dict, request: str, n: int, slots: list[datetime]) -> list[dict]:
    """Steps 2, 3, 5 with Claude."""
    tz = ctx["tz"]
    slot_text = [f"{DAY_NAMES[s.astimezone(tz).weekday()]} {s.astimezone(tz).isoformat()}" for s in slots]
    prompt = (
        f"Now: {datetime.now(tz).isoformat()} (time zone {tz.key})\n"
        f"Task from the Manager: {request}\nNumber of posts: {n}\n"
        f"Free slots (pick from these): {slot_text}\n\n"
        f"Brand brief:\n{json.dumps(ctx['brief'], indent=2, ensure_ascii=False)}\n\n"
        f"Connected platforms: {ctx['connected']}\nTrends: {ctx['trends']}\n"
        f"Learnings from the Analyst: {ctx['learnings'] or 'none yet'}\n"
        f"Already on the calendar: {json.dumps(ctx['calendar'], ensure_ascii=False)}"
    )
    posts = llm.structured(SYSTEM, prompt, "content_plan", PLAN_SCHEMA)["posts"][:n]
    # Fix any time that is invalid, in the past or on a busy day: use the free slot instead.
    for post, slot in zip(posts, slots):
        try:
            t = parse_time(post["scheduled_at"])
        except (KeyError, ValueError):
            t = None
        ok_days = {s.astimezone(tz).date() for s in slots}
        if t is None or t < now_utc() or t.astimezone(tz).date() not in ok_days:
            post["scheduled_at"] = slot.isoformat()
    return posts


def _finish(space: BusinessSpace, ctx: dict, posts: list[dict], task: str, kind: str) -> dict:
    """Step 4: send the schedule to the content calendar. Save the plan file and scripts."""
    for post in posts:
        _normalise(post, ctx)
        post["task"] = task
        post["plan_kind"] = kind
    posts = space.add_posts(posts)
    for post in posts:  # One folder per post in the media library (plan "Media library").
        (space.media_folder(post["id"]) / "script.json").write_text(
            json.dumps(post["script"], indent=2, ensure_ascii=False), encoding="utf-8")
    plan_path = write_plan_file(space, ctx)
    tz = ctx["tz"]
    return {
        "status": "planned",
        "posts": [{"id": p["id"], "topic": p["topic"], "format": p["format"], "platforms": p["platforms"],
                   "when": _label(parse_time(p["scheduled_at"]), tz), "scheduled_at": p["scheduled_at"],
                   "hook": p["script"].get("hook") or p["script"].get("copy_idea", "")} for p in posts],
        "plan_file": str(plan_path),
        "next_step": "Creator makes the drafts (create_drafts).",
    }


def _normalise(post: dict, ctx: dict) -> None:
    """Make every post safe for the Creator: valid format, platforms, time and script fields."""
    connected = ctx["connected"]
    post["format"] = post.get("format") if post.get("format") in FORMATS else "post"
    post["platforms"] = [p for p in post.get("platforms", []) if p in connected] or connected
    post["scheduled_at"] = parse_time(post["scheduled_at"]).astimezone(ctx["tz"]).isoformat()
    s = post.setdefault("script", {})
    s["caption"] = (s.get("caption") or post["topic"]).strip()
    tags = []
    for tag in s.get("hashtags") or []:
        tag = "#" + re.sub(r"[^\w]", "", str(tag).lstrip("#"))
        if len(tag) > 1 and tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
    s["hashtags"] = tags[:10]
    if post["format"] in VIDEO_FORMATS:
        shots = s.get("script") or []
        if isinstance(shots, str):
            shots = [{"show": shots, "say": "", "seconds": 5}]
        s["script"] = [{"shot": i + 1, "show": sh.get("show", ""), "say": sh.get("say", ""),
                        "seconds": sh.get("seconds") or 4} for i, sh in enumerate(shots)]
        s["hook"] = (s.get("hook") or (s["script"][0]["say"] if s["script"] else s["caption"])).strip()
        s["duration_seconds"] = sum(sh["seconds"] for sh in s["script"])
        for key in ("copy_idea", "slides"):
            s.pop(key, None)
    else:
        s["copy_idea"] = (s.get("copy_idea") or s["caption"]).strip()
        if post["format"] != "carousel":
            s.pop("slides", None)
        for key in ("hook", "script"):
            s.pop(key, None)


def write_plan_file(space: BusinessSpace, ctx: dict | None = None) -> object:
    """Save content_plan.json: every upcoming post with its script, by week and day.

    Other agents call this after they change a post, so the file always matches the calendar.
    """
    if ctx is None:  # Keep the inputs of the last Planner run.
        ctx = (space.get_content_plan() or {}).get("inputs", {})
        ctx = {"trends": ctx.get("trends", []), "learnings": ctx.get("learnings_used", [])}
    brief = space.get_brand_brief() or {}
    tz = local_tz(brief)
    upcoming = [p for p in space.get_calendar() if p["status"] != "posted"]
    weeks: dict[str, dict[str, list]] = {}
    for p in upcoming:
        t = parse_time(p["scheduled_at"]).astimezone(tz)
        monday = (t.date() - timedelta(days=t.weekday())).isoformat()
        weeks.setdefault(monday, {}).setdefault(t.date().isoformat(), []).append({
            "id": p["id"], "time": t.strftime("%H:%M"), "format": p["format"], "platforms": p["platforms"],
            "topic": p["topic"], "status": p["status"], "why": p.get("why", ""), "script": p["script"]})
    plan = {
        "business": brief.get("business_name") or space.tenant_id,
        "timezone": tz.key,
        "updated_at": now_utc().isoformat(),
        "inputs": {"trends": (ctx or {}).get("trends", []), "learnings_used": (ctx or {}).get("learnings", [])},
        "calendar_view": [f"{_label(parse_time(p['scheduled_at']), tz)} | {p['format']} | "
                          f"{', '.join(p['platforms'])} | {p['topic']} [{p['status']}]" for p in upcoming],
        "weeks": [{"week_of": w, "days": [{"date": d, "day": DAY_NAMES[date.fromisoformat(d).weekday()],
                                           "posts": posts} for d, posts in sorted(days.items())]}
                  for w, days in sorted(weeks.items())],
        "ready_for_creator": [p["id"] for p in upcoming if p["status"] in ("planned", "changes_requested")],
        "next_step": "Creator: make drafts for the posts in ready_for_creator.",
    }
    return space.save_content_plan(plan)


def _label(t: datetime, tz: ZoneInfo) -> str:
    return t.astimezone(tz).strftime("%a %d %b %H:%M")


# =============== mock mode (no Claude): simple fixed scripts ===============
def _product(task: str, brief: dict) -> str:
    m = re.search(r"\bnew ([a-z][\w\s'-]{2,40}?)(?=,|\.|!| and | i | we | to |$)", task, re.I)
    if m:
        return m.group(1).strip()
    offers = brief.get("offers") or []
    return offers[0] if offers else "our latest work"


def _tags(brief: dict, *topic_words: str) -> list[str]:
    name = "#" + re.sub(r"\W", "", brief.get("business_name", "shop")).lower()
    city = "#" + re.sub(r"\W", "", brief.get("location", "") or "local").lower()
    kind = "#" + re.sub(r"\W", "", brief.get("business_type", "") or "smallbusiness").lower()
    words = ["#" + re.sub(r"\W", "", w).lower() for w in topic_words if w]
    return [name, city, kind, *words, "#shoplocal", "#freshlymade"]


def _video_script(brief: dict, product: str, kind: str) -> dict:
    name = brief.get("business_name", "our shop")
    where = brief.get("location", "")
    if kind == "news":
        hook = "Something new just came out of our oven..."
        shots = [
            ("Close-up of the new " + product + " on the counter. Slow push-in.", hook, 3),
            ("Hands cut the first slice. Show the inside.", f"Meet our new {product}!", 4),
            ("Owner smiles and holds the plate to the camera.", f"Baked fresh today at {name}.", 4),
            (f"Shop front or sign{', ' + where if where else ''}.", "Come try it this week. Tell us what you think!", 4),
        ]
        caption = f"Our new {product} is here! Come taste it this week at {name}."
    else:
        hook = f"How we make our {product}, from start to finish"
        shots = [
            (f"The finished {product}, close-up, steam or shine.", hook, 3),
            ("Hands mix and pour. Quick cuts.", "It all starts early in the morning.", 5),
            ("Into the oven, then out. Time-lapse.", "Every batch is made by hand.", 5),
            ("Packing a box for a customer.", f"Fresh, every day, at {name}.", 4),
            ("Shop sign.", "Which one should we bake next? Comment below!", 3),
        ]
        caption = f"From our kitchen to your table: {product}, made by hand at {name}."
    return {"hook": hook, "script": [{"show": s, "say": w, "seconds": sec} for s, w, sec in shots],
            "caption": caption, "hashtags": _tags(brief, *product.split()[:2])}


def _news_format(ctx: dict, task: str) -> str:
    """Owner's wish first, then the Analyst's best format, else a reel (best reach for news)."""
    for word, fmt in (("carousel", "carousel"), ("reel", "reel"), ("video", "video"),
                      ("photo", "post"), ("picture", "post"), ("image", "post")):
        if re.search(rf"\b{word}", task, re.I):
            return fmt
    return ctx["best_format"] or "reel"


def _mock_news_post(ctx: dict, task: str, i: int, slot: datetime, fmt: str) -> dict:
    brief = ctx["brief"]
    product = _product(task, brief)
    if fmt in VIDEO_FORMATS:
        script = _video_script(brief, product, "news")
    else:
        script = {"copy_idea": f"Bright photo of the new {product} on a wooden table, one slice cut. "
                               f"Short story: why we made it.",
                  "caption": f"New at {brief.get('business_name', 'our shop')}: {product}! Come try it.",
                  "hashtags": _tags(brief, *product.split()[:2])}
        if fmt == "carousel":
            script["slides"] = [f"The new {product}", "The inside", "Made by hand", "Visit us"]
    return {"topic": f"New {product}", "format": fmt, "platforms": ctx["connected"],
            "scheduled_at": slot.isoformat(),
            "why": f"News: first free day. {fmt} works best for launches"
                   + (" (learning from the Analyst)." if ctx["best_format"] else "."),
            "script": script}


def _mock_week_post(ctx: dict, task: str, idea: str, fmt: str, slot: datetime, i: int) -> dict:
    brief = ctx["brief"]
    offers = brief.get("offers") or ["our products"]
    product = offers[i % len(offers)]
    name = brief.get("business_name", "our shop")
    topics = {
        "product_photo": f"{product.capitalize()} photo",
        "behind_scenes": f"Behind the scenes: baking {product}",
        "favourites": "Customer favourites this week",
        "customer": "A happy customer story",
        "tips": f"3 tips to enjoy {product}",
        "story": f"Our story: {brief.get('brand', name)[:60]}",
        "day_in_life": f"A day in the life at {name}",
    }
    topic = topics.get(idea, product)
    if fmt in VIDEO_FORMATS:
        script = _video_script(brief, product, "week")
    else:
        script = {"copy_idea": f"{topic}: bright, natural light, on a wooden table. Short story in the brand voice.",
                  "caption": f"{topic} at {name}. Which one is your favourite?",
                  "hashtags": _tags(brief, *product.split()[:2])}
        if fmt == "carousel":
            script["slides"] = [f"{o.capitalize()}" for o in offers[:4]] + ["Visit us this week"]
    return {"topic": topic, "format": fmt, "platforms": ctx["connected"], "scheduled_at": slot.isoformat(),
            "why": f"Weekly mix: {fmt} on {DAY_NAMES[slot.astimezone(ctx['tz']).weekday()]}"
                   + (f"; trend: {ctx['trends'][1]}" if fmt in VIDEO_FORMATS and len(ctx['trends']) > 1 else ""),
            "script": script}
