"""Trends and web search (used by the Planner).

Real: Claude with its web search tool, when ANTHROPIC_API_KEY is set
(TRENDS_WEB_SEARCH=0 turns it off). Web results are cached for one day per business type.
Simulated: a fixed list (mock mode, or when the search fails).
"""
import json
import re
from datetime import date

from .. import config, llm

_cache: dict[tuple[str, str, str], list[str]] = {}

SYSTEM = ("You find current social media trends for a small business. Search the web. "
          "Answer with a JSON array of 5 short strings only. Each string is one trend or idea "
          "(seasonal events, popular formats, trending topics) that fits the business.")


def get_trends(business_type: str, today: date, location: str = "") -> list[str]:
    key = (business_type, location, today.isoformat())
    if key in _cache:
        return _cache[key]
    trends = None
    if not config.MOCK and config.TRENDS_WEB_SEARCH:
        try:
            trends = _web_trends(business_type, today, location)
        except Exception:  # noqa: BLE001 - trends are nice to have; use the fixed list
            trends = None
    if trends:
        _cache[key] = trends  # Cache only real results, so a failed search is tried again later.
        return trends
    return _fixed(business_type, today)


def _web_trends(business_type: str, today: date, location: str) -> list[str] | None:
    prompt = (f"Today is {today.isoformat()}. Business: {business_type or 'small business'}"
              f"{' in ' + location if location else ''}. What content trends and upcoming dates "
              "(next 2 weeks) should its Instagram, TikTok and Facebook posts use?")
    response = llm.call_claude(SYSTEM, [{"role": "user", "content": prompt}],
                               tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}])
    text = llm.text_of(response)
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return None
    items = [str(x).strip() for x in json.loads(match.group(0)) if str(x).strip()]
    return items[:5] or None


def _fixed(business_type: str, today: date) -> list[str]:
    month = today.strftime("%B")
    return [
        f"Seasonal content for {month}",
        "Behind-the-scenes short videos do well on reels and TikTok",
        f"'Day in the life' of a {business_type or 'small business'} owner",
        "Customer reviews and user photos build trust",
    ]
