"""Command-line chat with the Manager agent.

Run:  python main.py                     (chat)
      python main.py --business my-bakery  (choose the business)
      python main.py --mock                (no API key needed)
      python main.py --publish             (Publisher only: post what is due, then exit)
      python main.py --check               (show which outside services are real or simulated)
      python main.py --bot                 (always on: phone approvals, reminders, Publisher on time)
      python main.py --telegram-setup      (check the Telegram bot; find your chat ID)
      python main.py --once                (one --bot round, then exit: for Grok Bot routines or cron)
      python main.py --business my-bakery --tool plan_week --args '{"week": "next"}'
                                           (run one team step and print its result as JSON; see GROK.md)
"""
import argparse
import json
import sys

from social_agent import config


def main() -> None:
    parser = argparse.ArgumentParser(description="Social media agents: chat with the Manager.")
    parser.add_argument("--business", default="my-business", help="Business space name (one per business)")
    parser.add_argument("--mock", action="store_true", help="Run without Claude (fixed answers)")
    parser.add_argument("--publish", action="store_true", help="Run the Publisher once and exit")
    parser.add_argument("--check", action="store_true", help="Show which services are real or simulated")
    parser.add_argument("--bot", action="store_true",
                        help="Always on: answers from the phone (Telegram), reminders, Publisher on time")
    parser.add_argument("--telegram-setup", action="store_true", help="Check the bot token; show your chat ID")
    parser.add_argument("--once", action="store_true",
                        help="One round: phone answers, previews, reminders, Publisher. Then exit")
    parser.add_argument("--tool", help="Run one team step (a Manager tool, or connect_accounts) and exit")
    parser.add_argument("--args", default="{}", help="JSON input for --tool")
    args = parser.parse_args()
    if args.mock:
        config.MOCK = True
    if args.check:
        print_services()
        return
    if args.telegram_setup or args.bot or args.once:
        from social_agent import bot
        bot.setup() if args.telegram_setup else bot.once() if args.once else bot.serve()
        return
    if args.tool:
        print(json.dumps(run_tool(args.business, args.tool, json.loads(args.args)), indent=2,
                         ensure_ascii=False, default=str))
        return

    # Import after config is set.
    from social_agent.agents import publisher
    from social_agent.agents.manager import Manager
    from social_agent.business_space import BusinessSpace
    from social_agent.owner_app import ConsoleOwnerApp

    space = BusinessSpace(args.business)
    app = ConsoleOwnerApp()

    if args.publish:
        print(publisher.run(space, app))
        return

    mode = "MOCK mode (no Claude calls)" if config.MOCK else f"model {config.MODEL}"
    print(f"Social media team for '{space.tenant_id}' - {mode}")
    print(f"Business space: {space.root}")
    print("Talk to the Manager. Type 'quit' to exit.")
    if config.MOCK and not config.API_KEY:
        print("(No ANTHROPIC_API_KEY found, so mock mode is on. See README.)")

    print_services()
    manager = Manager(space, app)
    if not space.get_brand_brief():
        app.say("Manager", "Hi! I run your social media team. First, let's learn about your business.")
        try:
            app.say("Manager", manager.handle("Please start the interview. I am new."))
        except Exception as exc:  # noqa: BLE001 - e.g. a wrong API key
            print(f"\n[Error] {exc}", file=sys.stderr)

    while True:
        try:
            text = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            return
        if not text:
            continue
        if text.lower() in {"quit", "exit", "bye"}:
            print("Bye!")
            return
        try:
            app.say("Manager", manager.handle(text))
        except Exception as exc:  # noqa: BLE001 - keep the chat alive
            print(f"\n[Error] {exc}", file=sys.stderr)


def run_tool(business: str, name: str, tool_args: dict) -> dict:
    """One team step with no live chat (Grok Bot runs these; the bot itself talks with the owner).
    Draft previews and alerts go to Discord."""
    from social_agent.agents import interviewer
    from social_agent.agents.manager import Manager
    from social_agent.bot import LogApp
    from social_agent.business_space import BusinessSpace

    space = BusinessSpace(business)
    if name == "connect_accounts":
        return interviewer._connect(space, tool_args["platforms"])
    if name in ("run_interviewer", "owner_review"):
        return {"error": f"{name} needs a live chat. Ask the owner yourself, then use update_brand_brief "
                         "and connect_accounts; for drafts use show_drafts, approve_draft, request_changes."}
    return Manager(space, LogApp()).run_tool(name, tool_args)


def print_services() -> None:
    print("\nOutside services (a key in .env switches each one on):")
    for name, real, key in config.services():
        print(f"  [{'REAL' if real else 'SIMULATED'}] {name}" + ("" if real else f"  <- set {key}"))


if __name__ == "__main__":
    main()
