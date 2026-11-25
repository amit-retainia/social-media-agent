"""1. Interviewer - learns business needs.

Steps (from the plan):
  1. Asks about brand, goals, audience and offers.
  2. Reads the website.
  3. Connects social accounts.
  4. Writes the brand brief.
Gets: interview with the owner; interview answers and website.
Gives: brand brief (to the Business space); business needs to the Planner.
"""
import json

from .. import config, llm
from ..business_space import BusinessSpace
from ..tools import platforms
from ..tools.website import read_website

NAME = "Interviewer"
MAX_TURNS = 40

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "business_name": {"type": "string"},
        "business_type": {"type": "string", "description": "e.g. bakery"},
        "location": {"type": "string"},
        "timezone": {"type": "string", "description": "IANA time zone from the location, e.g. Asia/Kolkata"},
        "closed_days": {"type": "array", "items": {"type": "string"}, "description": "e.g. Sunday"},
        "blackout_dates": {"type": "array", "items": {"type": "string"}, "description": "YYYY-MM-DD, no posts"},
        "website": {"type": "string"},
        "brand": {"type": "string", "description": "What the brand stands for, its story"},
        "brand_voice": {"type": "string", "description": "Tone of posts, e.g. warm, playful"},
        "offers": {"type": "array", "items": {"type": "string"}, "description": "Products, services, deals"},
        "audience": {"type": "string"},
        "goals": {"type": "array", "items": {"type": "string"}},
        "platforms": {"type": "array", "items": {"type": "string", "enum": platforms.PLATFORMS}},
        "posts_per_week": {"type": "integer"},
        "banned_topics": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["business_name", "business_type", "brand", "brand_voice", "offers",
                 "audience", "goals", "platforms"],
}

SYSTEM = f"""You are the Interviewer in a social media team for one small business.
Your job: learn the business needs and write the brand brief.

Follow these steps in order:
1. Ask about the brand, goals, audience and offers. Also ask for the location,
   brand voice, how many posts per week, and topics to avoid.
2. Ask for the website. If there is one, call read_website and use what you learn.
3. Ask which social platforms to use. Supported: {", ".join(platforms.PLATFORMS)}.
   Call connect_accounts with the list. If an account says "needs_login", tell the owner
   the message (link it in the Ayrshare dashboard) and go on.
   Fill timezone from the location (IANA name). Ask about closed days.
4. Give a short summary (5 lines at most) and ask "Is this correct?".
   When the owner confirms, call save_brand_brief.

Rules:
- Ask ONE short, friendly question at a time. Use simple words.
- If an answer is vague, ask one short follow-up.
- If the owner says "skip" or does not know, use a sensible default and move on.
- Do not write posts. The Planner and the Creator do that."""

TOOLS = [
    {"name": "read_website", "description": "Read the text of the business website.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "connect_accounts", "description": "Connect the owner's social accounts (OAuth).",
     "input_schema": {"type": "object", "properties": {
         "platforms": {"type": "array", "items": {"type": "string"}}}, "required": ["platforms"]}},
    {"name": "save_brand_brief", "description": "Save the final brand brief after the owner confirms.",
     "input_schema": BRIEF_SCHEMA},
]


def _connect(space: BusinessSpace, names: list[str]) -> dict:
    accounts = space.get_connected_accounts()
    for name in names:
        accounts[platforms.normalise(name)] = platforms.connect_account(name, space.tenant_id)
    space.save_connected_accounts(accounts)
    return accounts


def run(space: BusinessSpace, app, reason: str = "") -> dict:
    """Run the interview with the owner. Returns business needs for the Manager / Planner."""
    if config.MOCK:
        return _run_mock(space, app)

    first = "Start the interview now."
    if reason:
        first += f" Note from the Manager: {reason}"
    old = space.get_brand_brief()
    if old:
        first += f"\nA brand brief already exists. Only update what the owner wants to change:\n{json.dumps(old)}"

    messages = [{"role": "user", "content": first}]
    for _ in range(MAX_TURNS):
        response = llm.call_claude(SYSTEM, messages, tools=TOOLS)
        messages.append({"role": "assistant", "content": response.content})
        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if not tool_uses:
            answer = app.ask(NAME, llm.text_of(response) or "Please tell me more.")
            if answer.lower() in {"quit", "exit", "stop"}:
                return {"status": "stopped", "message": "Owner stopped the interview. Nothing saved."}
            messages.append({"role": "user", "content": answer})
            continue

        text = llm.text_of(response)
        if text:
            app.say(NAME, text)
        results, brief = [], None
        for tu in tool_uses:
            if tu.name == "read_website":
                out = read_website(tu.input["url"])
            elif tu.name == "connect_accounts":
                out = json.dumps(_connect(space, tu.input["platforms"]))
            elif tu.name == "save_brand_brief":
                brief = tu.input
                space.save_brand_brief(brief)
                out = "Saved."
            else:
                out = f"Unknown tool {tu.name}"
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out})
        messages.append({"role": "user", "content": results})
        if brief:
            return _done(space, brief)

    return {"status": "stopped", "message": "Interview too long. Nothing saved."}


def _done(space: BusinessSpace, brief: dict) -> dict:
    return {
        "status": "saved",
        "brand_brief": brief,
        "connected_platforms": space.connected_platforms(),
        "business_needs_for_planner": {
            "goals": brief.get("goals"), "audience": brief.get("audience"),
            "offers": brief.get("offers"), "posts_per_week": brief.get("posts_per_week"),
        },
    }


# ---- Mock mode: fixed questions, no Claude ----
MOCK_QUESTIONS = [
    ("business_name", "What is the name of your business?"),
    ("business_type", "What kind of business is it? (for example: bakery)"),
    ("location", "Which city or area do you serve?"),
    ("brand", "Tell me about your brand in one or two lines."),
    ("brand_voice", "How should your posts sound? (for example: warm, playful)"),
    ("offers", "What are your main products or offers? (separate with commas)"),
    ("audience", "Who are your customers?"),
    ("goals", "What do you want from social media? (separate with commas)"),
    ("website", "What is your website? (or 'skip')"),
    ("platforms", f"Which platforms? Choose from: {', '.join(platforms.PLATFORMS)} (separate with commas)"),
    ("posts_per_week", "How many posts per week?"),
    ("banned_topics", "Any topics to avoid? (separate with commas, or 'skip')"),
]
LIST_FIELDS = {"offers", "goals", "platforms", "banned_topics"}


def _run_mock(space: BusinessSpace, app) -> dict:
    who = NAME + " (mock)"
    brief: dict = {}
    for field, question in MOCK_QUESTIONS:
        answer = app.ask(who, question)
        if answer.lower() in {"quit", "exit", "stop"}:
            return {"status": "stopped", "message": "Owner stopped the interview. Nothing saved."}
        if answer.lower() == "skip":
            answer = ""
        if field in LIST_FIELDS:
            brief[field] = [x.strip() for x in answer.split(",") if x.strip()]
        elif field == "posts_per_week":
            brief[field] = int(answer) if answer.isdigit() else 3
        else:
            brief[field] = answer
    if brief.get("website"):
        brief["website_text"] = read_website(brief["website"])[:500]
    brief["platforms"] = [platforms.normalise(p) for p in brief["platforms"]]
    brief["timezone"] = config.TIMEZONE
    accounts = _connect(space, brief["platforms"])
    for name, acc in accounts.items():
        if acc.get("status") != "connected":
            app.say(who, f"{name}: {acc.get('message', acc.get('status'))}")
    space.save_brand_brief(brief)
    app.say(who, "Thanks! Your brand brief is saved in the Business space.")
    return _done(space, brief)
