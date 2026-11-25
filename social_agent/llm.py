"""All Claude calls go through this file (Anthropic Python SDK)."""
from anthropic import Anthropic

from . import config

_client = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=config.API_KEY)
    return _client


def call_claude(system: str, messages: list, tools: list | None = None, tool_choice: dict | None = None):
    """Send one request to Claude. Returns the response message."""
    kwargs = dict(model=config.MODEL, max_tokens=config.MAX_TOKENS, system=system, messages=messages)
    if tools:
        kwargs["tools"] = tools
    if tool_choice:
        kwargs["tool_choice"] = tool_choice
    return get_client().messages.create(**kwargs)


def text_of(response) -> str:
    """Join all text blocks of a response."""
    return "".join(b.text for b in response.content if b.type == "text").strip()


def structured(system: str, prompt: str, name: str, schema: dict) -> dict:
    """Ask Claude for JSON that matches `schema`. Uses a forced tool call."""
    tool = {"name": name, "description": f"Return the {name} result.", "input_schema": schema}
    response = call_claude(system, [{"role": "user", "content": prompt}],
                           tools=[tool], tool_choice={"type": "tool", "name": name})
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Claude did not return structured output.")
