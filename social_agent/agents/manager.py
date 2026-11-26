"""Manager - runs the team's steps for one business.

The AI agent that talks with the owner (for example Grok Bot, skill skills/social-manager) is the Manager's
brain: it decides what to do and writes all text. This file runs one step at a time for it:
  `python main.py --business <name> --tool <step> --args '<json>'`
Order: 1 brief -> 2 plan + script -> 3 create -> 4 owner approval -> 5 post -> 6 measure -> back to 2.
A step that needs text answers {"status": "needs_text", "rules", "shape", "data"}; the agent writes the
text and runs the step again with it.
"""
from .. import records
from ..business_space import BusinessSpace
from . import analyst, creator, interviewer, owner_approval, planner, publisher

NAME = "Manager"

# Every step, with its input. main.py --tool runs them; SKILL.md lists them for the agent.
TOOLS = {
    "show_business_space": "section: summary | brand_brief | connected_accounts | content_calendar | content_plan | learnings",
    "brief_questions": "(none) - the brand brief fields and the interview questions",
    "update_brand_brief": "changes: {field: value}",
    "connect_accounts": "platforms: [instagram, facebook]",
    "plan_week": "week: this | next, task (owner wishes), posts (written by you, after needs_text)",
    "plan_posts": "task (owner news), number_of_posts, preferred_day, posts (written by you, after needs_text)",
    "show_plan": "(none)",
    "create_drafts": "post_ids, drafts: {post id: draft} (written by you, after needs_text; again to fix a failed check)",
    "mark_checked": "post_id, checks: [{file, result: pass|fail, note}] - after the self-check, sends it to the owner",
    "show_drafts": "(none)",
    "approve_draft": "post_id",
    "request_changes": "post_id, note (owner's words), draft (optional: the new text, written by you)",
    "publish_due": "post_now (only if the owner says so)",
    "run_analyst": "(none) - then write learnings and run save_learnings",
    "save_learnings": "learnings: [lines]",
    "show_comments": "(none) - comments that wait for a reply",
    "reply_comments": "replies: [{post_id, comment_id, text}]",
    "export": "post_ids (optional) - rows for the Dexere tables; save them after every step that changed something",
    "restore": "rows: {businesses, posts, drafts, comments, metrics, learnings} from Dexere - a fresh disk only",
}


def _draft_view(space: BusinessSpace, post: dict) -> dict:
    """What the owner needs to decide: the text, when and where, and the media files (full paths)."""
    d = post.get("draft", {})
    return {"id": post["id"], "topic": post["topic"], "format": post["format"], "platforms": post["platforms"],
            "scheduled_at": post["scheduled_at"], "version": d.get("version"), "text": d.get("final_copy"),
            "caption": d.get("caption"), "hashtags": d.get("hashtags"),
            "files": [str(space.root / f) for f in d.get("files", [])],
            "simulated": d.get("simulated", False), "warnings": d.get("warnings", [])}


class Manager:
    def __init__(self, space: BusinessSpace, app):
        self.space = space
        self.app = app  # Where alerts are shown (bot.LogApp prints them).

    def run_tool(self, name: str, args: dict) -> dict:
        s, app = self.space, self.app
        if name == "show_business_space":
            return {
                "summary": s.summary, "brand_brief": s.get_brand_brief,
                "connected_accounts": s.get_connected_accounts, "content_calendar": s.get_calendar,
                "content_plan": s.get_content_plan, "learnings": s.get_learnings,
            }[args.get("section", "summary")]()
        if name == "brief_questions":
            return {"fields": interviewer.BRIEF_FIELDS, "questions": [q for _, q in interviewer.QUESTIONS]}
        if name == "update_brand_brief":
            brief = s.get_brand_brief() or {}
            brief.update(args["changes"])
            s.save_brand_brief(brief)
            return {"status": "updated", "brand_brief": brief}
        if name == "connect_accounts":
            return interviewer.connect(s, args["platforms"])
        if name == "plan_posts":
            return planner.run(s, args["task"], args.get("number_of_posts", 1), args.get("preferred_day", ""),
                               posts=args.get("posts"))
        if name == "plan_week":
            return planner.plan_week(s, args.get("task", ""), args.get("week", "next"), posts=args.get("posts"))
        if name == "show_plan":
            return planner.show_plan(s)
        if name == "create_drafts":
            return creator.run(s, args.get("post_ids"), args.get("drafts"))
        if name == "mark_checked":
            return creator.mark_checked(s, args["post_id"], args.get("checks", []))
        if name == "show_drafts":
            return {"drafts": [_draft_view(s, p) for p in s.posts_with_status("pending_approval")]}
        if name in ("approve_draft", "request_changes"):
            post = s.get_post(args["post_id"])
            if not post or post["status"] != "pending_approval":
                return {"error": f"{args['post_id']} is not a draft waiting for approval. Use show_drafts."}
            if name == "approve_draft":
                post = owner_approval.approve(s, post["id"], via="chat")
                return {"status": "approved", "id": post["id"], "scheduled_at": post["scheduled_at"],
                        "platforms": post["platforms"]}
            out = owner_approval.request_changes(s, post["id"], args["note"], via="chat", draft=args.get("draft"))
            if out.get("status") == "needs_text":
                return out
            if s.get_post(post["id"])["status"] == "checking":
                return {"status": "check_first", "drafts": [out], "check": "Self-check the new version, then mark_checked."}
            owner_approval.send_previews(s)
            return {"status": "remade", "draft": _draft_view(s, s.get_post(post["id"]))}
        if name == "publish_due":
            return publisher.run(s, app, post_now=args.get("post_now", False))
        if name == "run_analyst":
            return analyst.run(s, simulate_time_passed=args.get("simulate_time_passed", False))
        if name == "save_learnings":
            s.add_learnings([str(x) for x in args["learnings"]])
            return {"status": "saved", "learnings": s.get_learnings()[-10:]}
        if name == "show_comments":
            return publisher.pending_comments(s)
        if name == "reply_comments":
            return {"status": "done", "results": publisher.send_replies(s, args["replies"])}
        if name == "export":
            return records.export(s, args.get("post_ids"))
        if name == "restore":
            if s.get_calendar() and not args.get("force"):
                return {"error": "This disk already has this business. restore is only for a fresh disk."}
            return records.restore(s, args["rows"])
        return {"error": f"Unknown step {name}. Steps: {', '.join(TOOLS)}"}
