"""Manager agent - runs the team for one business.

The owner talks only to the Manager. The Manager:
- talks with the owner,
- keeps the Business space up to date,
- gives tasks to the Planner and runs the other agents in order:
  1 Interview -> 2 Plan + script -> 3 Create -> 4 Owner approval -> 5 Post -> 6 Measure -> back to 2.

Each step agent is a tool for the Manager (Claude tool use).
"""
import json

from .. import config, llm
from ..business_space import BusinessSpace
from . import analyst, creator, interviewer, owner_approval, planner, publisher

NAME = "Manager"

SYSTEM = """You are the Manager agent of a social media team for ONE small business.
The business owner talks only to you. You run the team with your tools:
1. run_interviewer  - learns business needs, connects accounts, writes the brand brief.
2. plan_week        - the Planner makes a weekly content calendar with a script per post.
   plan_posts       - the Planner adds post(s) for owner news on the next suitable day.
   show_plan        - read the content plan (content_plan.json).
3. create_drafts    - the Creator makes text, images and videos.
4. owner_review     - the owner previews each draft: approve, or ask for changes.
5. publish_due      - the Publisher posts approved posts at their time and replies to comments.
6. run_analyst      - the Analyst measures results and sends learnings to the Planner.
Also: show_business_space, update_brand_brief.

How to work:
- If there is no brand brief, start with run_interviewer.
- When the owner has news ("I baked a new chocolate cake, I want people to know"):
  plan_posts (1 post unless the owner asks for more) -> create_drafts with the new post_ids -> owner_review.
  Then tell the owner the day and time the post goes out.
- When the owner asks for a plan for the week ("plan my week", "what should I post?"): plan_week.
  Show the days, formats and hooks. Ask if the Creator should make the drafts now.
- Never post without owner approval. Call publish_due with post_now=true only if the owner asks
  to post now.
- When the owner shares a lasting fact (new opening hours, new product), call update_brand_brief.
- Keep replies short, friendly and simple. Say what the team did and what comes next."""

TOOLS = [
    {"name": "run_interviewer", "description": "Interview the owner and write or update the brand brief.",
     "input_schema": {"type": "object", "properties": {
         "reason": {"type": "string", "description": "What to focus on, if anything"}}}},
    {"name": "plan_posts",
     "description": "Planner adds post(s) about owner news on the next suitable day, with scripts.",
     "input_schema": {"type": "object", "properties": {
         "task": {"type": "string", "description": "The owner's news, in their words"},
         "number_of_posts": {"type": "integer", "minimum": 1, "maximum": 7},
         "preferred_day": {"type": "string",
                           "description": "Optional: today, tomorrow, a weekday name, or YYYY-MM-DD"}},
         "required": ["task"]}},
    {"name": "plan_week",
     "description": "Planner makes a weekly content calendar (posts_per_week from the brief) with scripts.",
     "input_schema": {"type": "object", "properties": {
         "task": {"type": "string", "description": "Optional wishes from the owner for the week"},
         "week": {"type": "string", "enum": ["this", "next"]}}}},
    {"name": "show_plan", "description": "Read the content plan: upcoming posts by day, and posts ready for the Creator.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "create_drafts",
     "description": "Creator makes drafts for planned posts (all of them, or only post_ids).",
     "input_schema": {"type": "object", "properties": {
         "post_ids": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "owner_review", "description": "Show drafts to the owner for approval or changes.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "publish_due", "description": "Publisher posts approved posts whose time has come, and replies to comments.",
     "input_schema": {"type": "object", "properties": {
         "post_now": {"type": "boolean", "description": "Post all approved posts now, ignoring the set time"}}}},
    {"name": "run_analyst", "description": "Analyst pulls metrics, finds what works, saves learnings.",
     "input_schema": {"type": "object", "properties": {
         "simulate_time_passed": {"type": "boolean", "description": "Demo only: act as if 7 days passed"}}}},
    {"name": "show_business_space", "description": "Read the Business space.",
     "input_schema": {"type": "object", "properties": {"section": {"type": "string", "enum": [
         "summary", "brand_brief", "connected_accounts", "content_calendar", "content_plan", "learnings"]}},
         "required": ["section"]}},
    {"name": "update_brand_brief", "description": "Change fields in the brand brief.",
     "input_schema": {"type": "object", "properties": {"changes": {"type": "object"}}, "required": ["changes"]}},
]


class Manager:
    def __init__(self, space: BusinessSpace, app):
        self.space = space
        self.app = app
        self.messages: list = []

    # ---- tools: each one runs a step agent ----
    def run_tool(self, name: str, args: dict) -> dict:
        s, app = self.space, self.app
        if name == "run_interviewer":
            return interviewer.run(s, app, args.get("reason", ""))
        if name == "plan_posts":
            return planner.run(s, args["task"], args.get("number_of_posts", 1), args.get("preferred_day", ""))
        if name == "plan_week":
            return planner.plan_week(s, args.get("task", ""), args.get("week", "next"))
        if name == "show_plan":
            return planner.show_plan(s)
        if name == "create_drafts":
            return creator.run(s, args.get("post_ids"))
        if name == "owner_review":
            return owner_approval.run(s, app)
        if name == "publish_due":
            return publisher.run(s, app, post_now=args.get("post_now", False))
        if name == "run_analyst":
            return analyst.run(s, simulate_time_passed=args.get("simulate_time_passed", False))
        if name == "show_business_space":
            section = args["section"]
            return {
                "summary": s.summary, "brand_brief": s.get_brand_brief,
                "connected_accounts": s.get_connected_accounts, "content_calendar": s.get_calendar, "content_plan": s.get_content_plan,
                "learnings": s.get_learnings,
            }[section]()
        if name == "update_brand_brief":
            brief = s.get_brand_brief() or {}
            brief.update(args["changes"])
            s.save_brand_brief(brief)
            return {"status": "updated", "brand_brief": brief}
        return {"error": f"Unknown tool {name}"}

    # ---- one chat turn ----
    def handle(self, owner_text: str) -> str:
        if config.MOCK:
            return self._handle_mock(owner_text)

        context = json.dumps(self.space.summary())
        self.messages.append({"role": "user", "content": f"{owner_text}\n\n[Business space now: {context}]"})
        for _ in range(12):  # Max tool rounds per turn.
            response = llm.call_claude(SYSTEM, self.messages, tools=TOOLS)
            self.messages.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                return llm.text_of(response)
            text = llm.text_of(response)
            if text:
                self.app.say(NAME, text)
            results = []
            for tu in tool_uses:
                try:
                    out = self.run_tool(tu.name, tu.input)
                except Exception as exc:  # noqa: BLE001 - report errors back to Claude
                    out = {"error": str(exc)}
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": json.dumps(out, ensure_ascii=False, default=str)})
            self.messages.append({"role": "user", "content": results})
        return "I stopped after many steps. Please tell me what to do next."

    # ---- mock mode: keyword routing, no Claude ----
    def _handle_mock(self, text: str) -> str:
        t = text.lower()
        if not self.space.get_brand_brief() or any(w in t for w in ("interview", "set up", "setup", "profile")):
            r = self.run_tool("run_interviewer", {})
            return f"Interview: {r['status']}. Now tell me news to post about, e.g. 'I baked a new chocolate cake'."
        if any(w in t for w in ("result", "stats", "analy", "learn", "how did")):
            r = self.run_tool("run_analyst", {"simulate_time_passed": True})
            return "Analyst: " + json.dumps(r.get("learnings", r.get("message")), ensure_ascii=False)
        if any(w in t for w in ("publish", "post now", "go live")):
            r = self.run_tool("publish_due", {"post_now": "now" in t})
            return "Publisher: " + json.dumps(r["published"], ensure_ascii=False)
        if any(w in t for w in ("plan my week", "weekly", "this week", "next week", "what should i post")):
            r = self.run_tool("plan_week", {"week": "this" if "this week" in t else "next"})
            if r["status"] != "planned":
                return r["message"]
            lines = "\n".join(f"- {p['when']}: {p['format']} - {p['topic']} ({p['hook']})" for p in r["posts"])
            return f"Planner made your week:\n{lines}\nPlan saved: {r['plan_file']}\nSay 'make drafts' to send it to the Creator."
        if any(w in t for w in ("make drafts", "create drafts", "make the posts")):
            r = self.run_tool("create_drafts", {})
            if r["status"] != "drafts_ready":
                return r["message"]
            review = self.run_tool("owner_review", {})
            return "Review: " + json.dumps(review.get("results"))
        if any(w in t for w in ("show plan", "the plan", "calendar")):
            r = self.run_tool("show_plan", {})
            return "\n".join(r.get("calendar_view", [r.get("message", "")]))
        if any(w in t for w in ("show", "brief", "status")):
            return json.dumps(self.space.summary(), indent=2, ensure_ascii=False)
        if any(w in t for w in ("review", "approve")):
            r = self.run_tool("owner_review", {})
            return "Review: " + json.dumps(r.get("results", r.get("message")))
        # Anything else = news for a post: plan -> create -> owner approval.
        plan = self.run_tool("plan_posts", {"task": text, "number_of_posts": 1})
        if plan["status"] != "planned":
            return plan["message"]
        when = ", ".join(f"{p['format']} on {p['when']}" for p in plan["posts"])
        self.app.say(NAME + " (mock)", f"Planner added: {when}. Creator is making the draft...")
        self.run_tool("create_drafts", {"post_ids": [p["id"] for p in plan["posts"]]})
        review = self.run_tool("owner_review", {})
        return ("Done. " + json.dumps(review.get("results"))
                + " Approved posts go out at their time. Say 'publish now' to post them now.")
