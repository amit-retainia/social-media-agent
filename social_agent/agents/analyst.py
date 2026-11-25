"""6. Analyst - finds what works.

Steps (from the plan):
  1. Pulls metrics at 1 h, 24 h and 7 days after each post.
  2. Compares topic, format, time and platform.
  3. Finds what works best.
  4. Saves learnings to the Business space.
  5. Sends learnings to the Planner (orange loop).
     The Planner reads them from the Business space on its next run.
"""
from collections import defaultdict
from datetime import timedelta

from .. import config
from ..business_space import BusinessSpace, now_utc, parse_time
from ..tools import platforms

SYSTEM = """You are the Analyst in a social media team for one business.
Look at the post metrics and write 3-5 short, practical learnings for the Planner.
Compare topic, format, posting time and platform. Use engagement rate
((likes + comments) / reach). Say when data is too small to be sure.
When the data shows it, include the lines "Best format: <post|carousel|reel|video>" and
"Best hour: <0-23>": the Planner reads them."""

LEARN_SCHEMA = {"type": "object", "properties": {
    "learnings": {"type": "array", "items": {"type": "string"}}}, "required": ["learnings"]}


def run(space: BusinessSpace, simulate_time_passed: bool = False) -> dict:
    posted = space.posts_with_status("posted")
    if not posted:
        return {"status": "nothing_to_measure", "message": "No posted posts yet."}

    # Step 1: pull metrics at each checkpoint that has passed.
    now = now_utc()
    for post in posted:
        metrics = post.get("metrics", {})
        posted_at = parse_time(post["posted_at"])
        for name, hours in config.METRIC_CHECKPOINTS.items():
            due = simulate_time_passed or now >= posted_at + timedelta(hours=hours)
            if due and name not in metrics:
                metrics[name] = {p: platforms.get_metrics(p, post, name, space) for p in post["platforms"]}
        space.update_post(post["id"], metrics=metrics)

    # Step 2: compare topic, format, time and platform (use the latest checkpoint per post).
    rows = []
    for post in space.posts_with_status("posted"):
        metrics = post.get("metrics", {})
        latest = next((metrics[c] for c in reversed(list(config.METRIC_CHECKPOINTS)) if c in metrics), None)
        if not latest:
            continue
        for platform, m in latest.items():
            m = {k: m.get(k, 0) for k in ("reach", "views", "likes", "comments")}
            rows.append({"topic": post["topic"], "format": post["format"], "platform": platform,
                         "hour": parse_time(post["scheduled_at"]).hour, **m,
                         "engagement": round((m["likes"] + m["comments"]) / max(m["reach"], 1), 4)})
    if not rows:
        return {"status": "waiting", "message": "No metrics yet. The first pull is 1 hour after posting."}
    comparison = {key: _average_by(rows, key) for key in ("format", "platform", "hour", "topic")}

    # Step 3: find what works best. The agent writes the learnings (MOCK: from the averages).
    if not config.MOCK:
        return {"status": "needs_text", "step": "save_learnings",
                "do": "Write the learnings by these rules. Then run save_learnings with {\"learnings\": [...]}.",
                "rules": SYSTEM, "shape": LEARN_SCHEMA,
                "data": {"per_post_metrics": rows, "averages": comparison}}
    learnings = [f"Best {key}: {max(groups, key=lambda g: groups[g])} "
                 f"(engagement {max(groups.values()):.1%})"
                 for key, groups in comparison.items() if len(groups) > 1]
    learnings = learnings or ["Not enough different posts yet to compare."]

    # Steps 4-5: save learnings; the Planner reads them next time.
    space.add_learnings(learnings)
    return {"status": "done", "posts_measured": len({r['topic'] for r in rows}),
            "comparison": comparison, "learnings": learnings,
            "next_step": "Planner uses these learnings for the next posts."}


def _average_by(rows: list[dict], key: str) -> dict:
    groups = defaultdict(list)
    for r in rows:
        groups[str(r[key])].append(r["engagement"])
    return {k: round(sum(v) / len(v), 4) for k, v in groups.items()}
