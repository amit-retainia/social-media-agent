"""Rows for the Dexere space "Social Media Agent" (the agent writes them; this app cannot reach Dexere).

Dexere is the source of truth for text; the JSON files on disk are only a working copy:
  export()   the rows of a business for the 8 tables (businesses, posts, drafts, comments, metrics, learnings,
             followers, tests),
             in the tables' field keys. The agent saves them after each step that changed something.
  restore()  a fresh disk: the agent reads the business's rows from Dexere and passes them back (same shape);
             this rebuilds the JSON files, and the kept media comes back from R2 (tools/media.py).
"""
import json
import re

from .business_space import BusinessSpace, now_utc, parse_time
from .tools import media, platforms, storage

TABLES = ("businesses", "posts", "drafts", "comments", "metrics", "learnings", "followers", "tests")
FINAL = re.compile(r"v\d+_\d+x\d+_")


def _date(iso: str | None) -> str | None:
    return iso[:10] if iso else None


def export(space: BusinessSpace, post_ids: list[str] | None = None) -> dict:
    """Rows for every table (only the given posts, if any). The primary key of each table is its first field."""
    from .agents import planner

    biz = space.tenant_id
    brief = space.get_brand_brief() or {}
    tz = planner.local_tz(brief)
    posts = [p for p in space.get_calendar() if not post_ids or p["id"] in post_ids]
    rows: dict = {t: [] for t in TABLES}
    rows["businesses"].append({
        "business": biz, "name": brief.get("business_name"), "business_type": brief.get("business_type"),
        "location": brief.get("location"), "timezone": brief.get("timezone"),
        "platforms": ", ".join(brief.get("platforms") or []), "posts_per_week": brief.get("posts_per_week"),
        "status": "active", "brand_brief": json.dumps(brief, ensure_ascii=False),
        "accounts": json.dumps(space.get_connected_accounts(), ensure_ascii=False),
        "added_at": _date(brief.get("updated_at"))})
    for p in posts:
        d = p.get("draft") or {}
        rows["posts"].append({
            "post_id": p["id"], "business": biz, "topic": p["topic"], "format": p["format"],
            "platforms": ", ".join(p["platforms"]),
            "post_date": parse_time(p["scheduled_at"]).astimezone(tz).date().isoformat(),
            "scheduled_at": p["scheduled_at"], "status": p["status"], "plan_kind": p.get("plan_kind"),
            "why": p.get("why"), "script": json.dumps(p.get("script", {}), ensure_ascii=False),
            "draft_version": d.get("version"), "change_notes": "\n".join(p.get("change_notes", [])),
            "approved_at": p.get("approved_at"), "posted_at": p.get("posted_at"),
            "links": json.dumps({k: v.get("url") for k, v in (p.get("publish_results") or {}).items()})})
        if d:
            rows["drafts"].append({
                "draft_id": f"{p['id']}-v{d.get('version', 1)}", "post": p["id"], "business": biz,
                "version": d.get("version", 1), "final_copy": d.get("final_copy"), "caption": d.get("caption"),
                "hashtags": " ".join(d.get("hashtags", [])),
                "prompts": json.dumps(d.get("scenes") or d.get("image_prompts") or [], ensure_ascii=False),
                "checks": json.dumps(d.get("checks", []), ensure_ascii=False),
                "media": "\n".join(d.get("r2", [])), "warnings": "\n".join(d.get("warnings", [])),
                "simulated": bool(d.get("simulated")), "made_at": d.get("made_at") or now_utc().date().isoformat()})
        for c in p.get("comment_log", []) + [dict(c, status="waiting") for c in p.get("pending_comments", [])]:
            rows["comments"].append({
                "comment_id": c["id"], "post": p["id"], "business": biz, "platform": c["platform"],
                "from": c.get("from"), "text": c.get("text"), "status": c["status"], "reply": c.get("reply"),
                "seen_at": _date(c.get("at"))})
        for checkpoint, by_platform in (p.get("metrics") or {}).items():
            for platform, m in by_platform.items():
                rows["metrics"].append({
                    "key": f"{p['id']}@{platform}@{checkpoint}", "post": p["id"], "business": biz,
                    "platform": platform, "checkpoint": checkpoint,
                    **{k: m.get(k) for k in platforms.METRIC_FIELDS},
                    "late": bool(m.get("late")), "taken_at": _date(m.get("taken_at")),
                    "missing": ", ".join(m.get("gaps", []))})
    if not post_ids:
        rows["learnings"] = [{"learning": item["learning"], "business": biz, "status": "active",
                              "added_at": _date(item.get("added_at"))} for item in space.get_learnings()]
        rows["followers"] = [{"key": f"{biz}@{f['platform']}@{f['date']}", "business": biz, "platform": f["platform"],
                              "date": f["date"], "followers": f["followers"], "missing": f.get("gap", "")}
                             for f in space.get_followers()]
        rows["tests"] = [{"test_id": t["id"], "business": biz, "change": t["change"], "value": str(t["value"]),
                          "idea": t["idea"], "status": t["status"], "source": t["source"], "priority": t["priority"],
                          "platform": t.get("platform"), "posts": ", ".join(t["posts"]), "needed": t["needed"],
                          "result": json.dumps(t.get("result") or {}, ensure_ascii=False),
                          "record": json.dumps(t, ensure_ascii=False), "added_at": _date(t.get("added_at")),
                          "ended_at": _date(t.get("ended_at"))} for t in space.get_experiments()]
    return rows


def restore(space: BusinessSpace, rows: dict) -> dict:
    """A fresh disk: rebuild the JSON files from the Dexere rows (the shape export() gives), then pull the media."""
    from .agents import creator, planner

    biz = (rows.get("businesses") or [{}])[0]
    if biz.get("brand_brief"):
        space.save_brand_brief(json.loads(biz["brand_brief"]))
    if biz.get("accounts"):
        space.save_connected_accounts(json.loads(biz["accounts"]))
    drafts: dict = {}
    for d in rows.get("drafts", []):
        if d.get("version", 0) >= drafts.get(d["post"], {}).get("version", 0):
            drafts[d["post"]] = d
    prefix = storage.key_for(space.tenant_id, "")
    calendar = []
    for r in rows.get("posts", []):
        post = {"id": r["post_id"], "topic": r["topic"], "format": r["format"],
                "platforms": [x.strip() for x in (r.get("platforms") or "").split(",") if x.strip()],
                "scheduled_at": r["scheduled_at"], "status": r["status"], "plan_kind": r.get("plan_kind"),
                "why": r.get("why"), "script": json.loads(r.get("script") or "{}"),
                "change_notes": [x for x in (r.get("change_notes") or "").split("\n") if x],
                "approved_at": r.get("approved_at"), "posted_at": r.get("posted_at")}
        links = json.loads(r.get("links") or "{}")
        if links:
            post["publish_results"] = {k: {"url": v} for k, v in links.items()}
        d = drafts.get(r["post_id"])
        if d:
            keys = [k for k in (d.get("media") or "").split("\n") if k]
            prompts = json.loads(d.get("prompts") or "[]")
            draft = {"version": d.get("version"), "final_copy": d.get("final_copy"), "caption": d.get("caption"),
                     "hashtags": (d.get("hashtags") or "").split(), "r2": keys,
                     "checks": json.loads(d.get("checks") or "[]"), "simulated": bool(d.get("simulated")),
                     "files": [k[len(prefix):] for k in keys if FINAL.match(k.rsplit("/", 1)[-1])]}
            draft["scenes" if post["format"] in ("reel", "video") else "image_prompts"] = prompts
            draft["sizes"] = {pl: creator.ai_models.size_for(pl, post["format"]) for pl in post["platforms"]}
            draft["platform_text"] = creator.platform_texts(draft, post["platforms"])
            post["draft"] = draft
        calendar.append(post)
    space.save_calendar(calendar)
    learnings = [r["learning"] for r in rows.get("learnings", []) if r.get("status", "active") == "active"]
    if learnings and not space.get_learnings():
        space.add_learnings(learnings)
    if not space.get_followers():
        for f in sorted(rows.get("followers", []), key=lambda f: f["date"]):
            space.add_followers(f["platform"], f["date"], f.get("followers"), "restored", f.get("missing") or "")
    if rows.get("tests") and not space.get_experiments():
        space.save_experiments([json.loads(r["record"]) for r in rows["tests"]])
    pulled = 0
    if media.on():
        for post in calendar:
            pulled += media.pull(space, post["id"])
    planner.write_plan_file(space)
    return {"status": "restored", "posts": len(calendar), "learnings": len(learnings), "media_files_pulled": pulled}
