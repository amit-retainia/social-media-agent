"""6. Analyst - finds what works.

Three parts:
  collect(space)        The counter. The bot runs it every few minutes. No AI text, and cheap: it calls a platform
                        only when something is due. It saves each post's numbers when the post is 1 h, 24 h and
                        7 days old, and the follower count once a day.
  run(space)            The thinker. The agent runs it ("how did my posts do?"). It compares the saved numbers and
                        asks the agent to write the learnings for the Planner.
  weekly_report(space)  The bot sends the owner the week's numbers (REPORT_DAY from REPORT_HOUR, local time).
Tests (the usual way vs one new idea) are decided in experiments.review(), with the same growth score.

Steps (from the plan):
  1. Pulls metrics at 1 h, 24 h and 7 days after each post.
  2. Compares format, time and platform by the growth score.
  3. Finds what works best (only with ANALYST_MIN_POSTS posts per group).
  4. Saves learnings to the Business space.
  5. Sends learnings to the Planner (orange loop).
     The Planner reads them from the Business space on its next run.

Growth score = (follows x 3 + shares x 2 + saves) per 1,000 reach: the actions that bring new followers.
A platform that gives none of these: (likes + comments) per 1,000 reach.
Fair comparisons only: same platform, same checkpoint, no late checkpoints, reach of ANALYST_MIN_REACH or more.
"""
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from .. import config, experiments
from ..business_space import BusinessSpace, now_utc, parse_time
from ..tools import platforms
from . import planner

NEWEST_FIRST = tuple(reversed(config.METRIC_CHECKPOINTS))  # 7d, 24h, 1h
MIN_LIFT = 0.10  # A "best" must beat the next group by 10% or more.

SYSTEM = """You are the Analyst in a social media team for one business. The goal is more followers.
Look at the numbers and write 3-5 short, practical learnings for the Planner.
- The main number is the growth score: (follows x 3 + shares x 2 + saves) per 1,000 reach.
  If a platform gives none of these, the score is (likes + comments) per 1,000 reach.
- Compare only what "comparison" compares (same platform, same post age). Use "vs_usual" to name the strong and
  weak posts, and say what the strong ones have in common (topic, hook, format, time).
- "winners" lists what has enough posts to be sure. Write the lines "Best format: <format>" and "Best hour: <0-23>"
  ONLY for what is in "winners" (the Planner reads these lines). For the rest, say it is too early and how many more
  posts are needed.
- Say how the follower count changed ("followers").
- Tests are decided by the numbers, not by you: never write a "Best ..." line for a running test. You may say how
  the running test looks so far."""

LEARN_SCHEMA = {"type": "object", "properties": {
    "learnings": {"type": "array", "items": {"type": "string"}}}, "required": ["learnings"]}


# =============== the counter (the bot runs it) ===============
def collect(space: BusinessSpace, simulate_time_passed: bool = False) -> dict:
    """Save the numbers that are due now: post checkpoints, and the follower count once a day."""
    now = now_utc()
    saved = []
    for post in space.posts_with_status("posted"):
        metrics = post.get("metrics", {})
        posted_at = parse_time(post["posted_at"])
        due = [name for name, hours in config.METRIC_CHECKPOINTS.items()
               if name not in metrics and (simulate_time_passed or now >= posted_at + timedelta(hours=hours))]
        for name in due:
            # Late (the bot was off): the numbers are from later than the checkpoint, so they are not compared.
            late = not simulate_time_passed and now > posted_at + timedelta(hours=config.METRIC_LATE_HOURS[name])
            metrics[name] = {p: platforms.get_metrics(p, post, name, space) | {"taken_at": now.isoformat(), "late": late}
                             for p in post["platforms"]}
            saved.append(f"{post['id']} {name}" + (" (late)" if late else ""))
        if due:
            space.update_post(post["id"], metrics=metrics)

    today = now.astimezone(planner.local_tz(space.get_brand_brief())).date().isoformat()
    have = {(f["platform"], f["date"]) for f in space.get_followers()}
    counted = []
    for platform in space.connected_platforms():
        if (platform, today) not in have:
            got = platforms.get_followers(platform, space)
            space.add_followers(platform, today, got["followers"], got["mode"], got.get("gap", ""))
            counted.append(f"{platform}: {got['followers']}")
    return {"metrics_saved": saved, "followers_saved": counted}


# =============== the thinker (the agent runs it) ===============
def run(space: BusinessSpace, simulate_time_passed: bool = False) -> dict:
    if not space.posts_with_status("posted"):
        return {"status": "nothing_to_measure", "message": "No posted posts yet."}
    collect(space, simulate_time_passed)  # The bot may be off: save anything that is due now.
    test_results = experiments.review(space)

    rows = _rows(space)
    if not rows:
        return {"status": "waiting", "message": "No metrics yet. The first pull is 1 hour after posting."}
    comparison = _compare(rows)
    winners, lines = _findings(comparison)
    followers = follower_change(space)
    late = sorted({r["post_id"] for r in rows if r["late"]})

    if not config.MOCK:
        return {"status": "needs_text", "step": "save_learnings",
                "do": "Write the learnings by these rules. Then run save_learnings with {\"learnings\": [...]}.",
                "rules": SYSTEM, "shape": LEARN_SCHEMA,
                "data": {"comparison": comparison, "winners": winners, "findings": lines,
                         "followers_last_7_days": followers, "posts_with_late_numbers": late,
                         "test_results_now": test_results, "tests": experiments.show(space)}}
    learnings = lines or ["Not enough posts yet to compare."]
    space.add_learnings(learnings)
    return {"status": "done", "posts_measured": len({r["post_id"] for r in rows}),
            "comparison": comparison, "followers_last_7_days": followers, "learnings": learnings,
            "test_results_now": test_results, "next_step": "Planner uses these learnings for the next posts."}


def growth_score(m: dict, fields: list[str]) -> float | None:
    """Points per 1,000 reach from the growth `fields`, else from likes + comments. None: reach too small."""
    reach = m.get("reach") or 0
    if reach < config.ANALYST_MIN_REACH:
        return None
    if fields:
        points = sum(m.get(f, 0) * config.GROWTH_WEIGHTS[f] for f in fields)
    else:
        points = m.get("likes", 0) + m.get("comments", 0)
    return round(points / reach * 1000, 1)


def _rows(space: BusinessSpace) -> list[dict]:
    """One row per post, platform and checkpoint."""
    tz = planner.local_tz(space.get_brand_brief())
    rows = []
    for post in space.posts_with_status("posted"):
        hour = parse_time(post["scheduled_at"]).astimezone(tz).hour
        for checkpoint, by_platform in (post.get("metrics") or {}).items():
            for platform, m in by_platform.items():
                gaps = m.get("gaps", [])
                rows.append({"post_id": post["id"], "topic": post["topic"], "format": post["format"],
                             "platform": platform, "hour": hour, "checkpoint": checkpoint, "late": bool(m.get("late")),
                             **{f: m.get(f, 0) for f in platforms.METRIC_FIELDS},
                             "has": [f for f in config.GROWTH_WEIGHTS if f in m and f not in gaps]})
    return rows


def _compare(rows: list[dict]) -> dict:
    """Per platform: one checkpoint (7d if it has enough posts, else 24h, ...), a score per post, groups."""
    out = {}
    for platform in sorted({r["platform"] for r in rows}):
        on_time = [r for r in rows if r["platform"] == platform and not r["late"]]
        by_checkpoint = {c: [r for r in on_time if r["checkpoint"] == c] for c in NEWEST_FIRST}
        checkpoint = next((c for c in NEWEST_FIRST if len(by_checkpoint[c]) >= config.ANALYST_MIN_POSTS),
                          max(NEWEST_FIRST, key=lambda c: len(by_checkpoint[c])))
        picked = by_checkpoint[checkpoint]
        if not picked:
            continue
        # Score every post with the same fields (a reel has no "follows" on Instagram: then no post uses it).
        big = [r for r in picked if r["reach"] >= config.ANALYST_MIN_REACH]
        fields = [f for f in config.GROWTH_WEIGHTS if big and all(f in r["has"] for r in big)]
        scored = []
        for r in picked:
            r["score"] = growth_score(r, fields)
            if r["score"] is not None:
                scored.append(r)
        usual = median(r["score"] for r in scored) if scored else None
        for r in scored:
            r["vs_usual"] = f"{r['score'] / usual - 1:+.0%}" if usual else None
        out[platform] = {
            "checkpoint": checkpoint,
            "score": ("growth: " + ", ".join(fields)) if fields else "engagement: likes + comments",
            "usual_score": usual,
            "posts_scored": len(scored),
            "too_small_reach": [r["post_id"] for r in picked if r["score"] is None],
            "by_format": _groups(scored, "format"),
            "by_hour": _groups(scored, "hour"),
            "posts": [{k: r.get(k) for k in ("post_id", "topic", "format", "hour", "reach", "likes", "comments",
                                              *fields, "score", "vs_usual")} for r in scored],
        }
    return out


def _groups(rows: list[dict], key: str) -> dict:
    groups = defaultdict(list)
    for r in rows:
        groups[str(r[key])].append(r["score"])
    return {k: {"posts": len(v), "avg_score": round(sum(v) / len(v), 1)} for k, v in sorted(groups.items())}


def _findings(comparison: dict) -> tuple[dict, list[str]]:
    """What is proven (winners) and plain lines about it. "Best format/hour:" only for the main platform."""
    if not comparison:
        return {}, []
    main = max(comparison, key=lambda p: comparison[p]["posts_scored"])
    winners: dict = {}
    lines = []
    for platform, c in comparison.items():
        for key, label in (("by_format", "format"), ("by_hour", "hour")):
            groups = c[key]
            ready = {k: g for k, g in groups.items() if g["posts"] >= config.ANALYST_MIN_POSTS}
            if len(ready) < 2:
                now = ", ".join(f"{k} {g['posts']}" for k, g in groups.items()) or "none"
                lines.append(f"Too early to pick a best {label} on {platform}: need {config.ANALYST_MIN_POSTS} posts "
                             f"for at least 2 {label}s (now: {now}).")
                continue
            best, second = sorted(ready, key=lambda k: ready[k]["avg_score"], reverse=True)[:2]
            a, b = ready[best]["avg_score"], ready[second]["avg_score"]
            proof = (f"{platform}, {c['checkpoint']}: score {a} vs {b} for {second}, "
                     f"{ready[best]['posts']} vs {ready[second]['posts']} posts")
            if b and a < b * (1 + MIN_LIFT):
                lines.append(f"No clear best {label} on {platform} yet ({best} and {second} are close: {a} vs {b}).")
                continue
            winners.setdefault(platform, {})[label] = best
            if platform == main:
                lines.append(f"Best {label}: {best} ({proof})")
            else:
                lines.append(f"On {platform}, {best} did best ({proof})")
    return winners, lines


# =============== followers and the weekly report ===============
def follower_change(space: BusinessSpace, days: int = 7) -> dict:
    """Per platform: the follower count `days` ago (or the first count) and now."""
    by_platform = defaultdict(list)
    for f in space.get_followers():
        if f["followers"] is not None:
            by_platform[f["platform"]].append(f)
    out = {}
    for platform, items in by_platform.items():
        items.sort(key=lambda f: f["date"])
        last = items[-1]
        start = (date.fromisoformat(last["date"]) - timedelta(days=days)).isoformat()
        first = next(f for f in items if f["date"] >= start)
        out[platform] = {"from": first["followers"], "to": last["followers"],
                         "change": last["followers"] - first["followers"], "since": first["date"]}
    return out


def weekly_report(space: BusinessSpace, now=None) -> str | None:
    """Once a week (REPORT_DAY from REPORT_HOUR, local time): the week's numbers to the owner. No AI text."""
    brief = space.get_brand_brief() or {}
    local = (now or now_utc()).astimezone(planner.local_tz(brief))
    day = config.REPORT_DAY.strip().capitalize()
    report_weekday = planner.DAY_NAMES.index(day) if day in planner.DAY_NAMES else 0
    report_day = local.date() - timedelta(days=(local.weekday() - report_weekday) % 7)
    if report_day == local.date() and local.hour < config.REPORT_HOUR:
        return None
    if any(r["week_of"] == report_day.isoformat() for r in space.get_reports()):
        return None

    week_ago = local - timedelta(days=7)
    posted = [p for p in space.posts_with_status("posted") if parse_time(p["posted_at"]) >= week_ago]
    followers = follower_change(space)
    if not posted and not followers:
        return None  # Not live yet.
    lines = [f"Weekly report for {brief.get('business_name') or space.tenant_id}:"]
    for platform, c in followers.items():
        lines.append(f"{platform.title()} followers: {c['from']:,} -> {c['to']:,} ({c['change']:+,})")
    lines.append(f"Posts this week: {len(posted)}.")
    top = _top_post(posted)
    if top:
        post, platform, m, score = top
        lines.append(f'Top post: "{post["topic"]}" ({post["format"]}, {platform}): growth score {score}, '
                     f'{m.get("shares", 0)} shares, {m.get("saves", 0)} saves, {m.get("follows", 0)} follows.')
    if experiments.status_line(space):
        lines.append(experiments.status_line(space))
    text = "\n".join(lines)
    space.add_alert(text)
    space.add_report(report_day.isoformat(), text)
    return text


def _top_post(posts: list[dict]):
    """The post with the best growth score at 24 h (same age for every post)."""
    best = None
    for post in posts:
        for platform, m in ((post.get("metrics") or {}).get("24h") or {}).items():
            if m.get("late"):
                continue
            gaps = m.get("gaps", [])
            score = growth_score(m, [f for f in config.GROWTH_WEIGHTS if f in m and f not in gaps])
            if score is not None and (best is None or score > best[3]):
                best = (post, platform, m, score)
    return best
