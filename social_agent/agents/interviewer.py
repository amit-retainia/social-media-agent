"""1. Interviewer - learns business needs.

Steps (from the plan):
  1. Asks about brand, goals, audience and offers.
  2. Reads the website.
  3. Connects social accounts.
  4. Writes the brand brief.
Gets: interview with the owner; interview answers and website.
Gives: brand brief (to the Business space); business needs to the Planner.

The agent that talks with the owner (for example Grok Bot) asks the questions (QUESTIONS) and saves the
brief with the Manager's update_brand_brief step; connect() checks the accounts.
run() is the same interview in a terminal, with fixed questions.
"""
from ..business_space import BusinessSpace
from ..tools import platforms
from ..tools.website import read_website

NAME = "Interviewer"

# The brand brief: every field the other agents read.
BRIEF_FIELDS = {
    "business_name": "name of the business",
    "business_type": "e.g. bakery",
    "location": "city or area served",
    "timezone": "IANA time zone from the location, e.g. Asia/Kolkata",
    "closed_days": "list, e.g. [\"Sunday\"]",
    "blackout_dates": "list of YYYY-MM-DD with no posts",
    "website": "URL",
    "brand": "what the brand stands for, its story",
    "brand_voice": "tone of posts, e.g. warm, playful",
    "offers": "list of products, services, deals",
    "audience": "who the customers are",
    "goals": "list of what social media should bring",
    "platforms": f"list, from {platforms.SUPPORTED}",
    "posts_per_week": "number, 1 to 7",
    "banned_topics": "list of topics to avoid",
}

# Fixed questions, in order. The agent asks them one at a time; run() asks them in a terminal.
QUESTIONS = [
    ("business_name", "What is the name of your business?"),
    ("business_type", "What kind of business is it? (for example: bakery)"),
    ("location", "Which city or area do you serve?"),
    ("brand", "Tell me about your brand in one or two lines."),
    ("brand_voice", "How should your posts sound? (for example: warm, playful)"),
    ("offers", "What are your main products or offers? (separate with commas)"),
    ("audience", "Who are your customers?"),
    ("goals", "What do you want from social media? (separate with commas)"),
    ("website", "What is your website? (or 'skip')"),
    ("platforms", f"Which platforms? Choose from: {', '.join(platforms.SUPPORTED)} (separate with commas)"),
    ("posts_per_week", "How many posts per week?"),
    ("banned_topics", "Any topics to avoid? (separate with commas, or 'skip')"),
]
LIST_FIELDS = {"offers", "goals", "platforms", "banned_topics", "closed_days", "blackout_dates"}


def connect(space: BusinessSpace, names: list[str]) -> dict:
    """Step 3: check each social account and save the result."""
    accounts = space.get_connected_accounts()
    for name in names:
        accounts[platforms.normalise(name)] = platforms.connect_account(name, space.tenant_id)
    space.save_connected_accounts(accounts)
    return accounts


_connect = connect  # Old name.


def run(space: BusinessSpace, app) -> dict:
    """The interview in a terminal (fixed questions). `app` asks and shows (see bot.LogApp / tests)."""
    from .. import config

    brief: dict = {}
    for field, question in QUESTIONS:
        answer = app.ask(NAME, question)
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
    accounts = connect(space, brief["platforms"])
    for name, acc in accounts.items():
        if acc.get("status") != "connected":
            app.say(NAME, f"{name}: {acc.get('message', acc.get('status'))}")
    space.save_brand_brief(brief)
    app.say(NAME, "Thanks! Your brand brief is saved in the Business space.")
    return {
        "status": "saved",
        "brand_brief": brief,
        "connected_platforms": space.connected_platforms(),
        "business_needs_for_planner": {
            "goals": brief.get("goals"), "audience": brief.get("audience"),
            "offers": brief.get("offers"), "posts_per_week": brief.get("posts_per_week"),
        },
    }
