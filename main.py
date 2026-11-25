"""The team's steps, for an AI agent (Grok Bot or any other) to run. The agent talks with the owner.

Run:  python main.py --business my-bakery --tool plan_week --args '{"week": "next"}'
                                     (run one team step and print its result as JSON; see GROK.md)
      python main.py --tool steps    (list every step and its input)
      python main.py --once          (one background round, then exit: for Grok Bot routines or cron)
      python main.py --publish       (Publisher only: post what is due, then exit)
      python main.py --check         (show which outside services are real or simulated)
      python main.py --mock ...      (demo and tests: fixed sample text instead of text from the agent)
      python main.py --bot           (always on, with Telegram: phone approvals, reminders, Publisher on time)
      python main.py --telegram-setup  (check the Telegram bot; find your chat ID)
"""
import argparse
import json

from social_agent import config


def main() -> None:
    parser = argparse.ArgumentParser(description="Social media team: steps for an AI agent to run.")
    parser.add_argument("--business", default="my-business", help="Business space name (one per business)")
    parser.add_argument("--tool", help="Run one team step and exit ('steps' lists them)")
    parser.add_argument("--args", default="{}", help="JSON input for --tool")
    parser.add_argument("--once", action="store_true",
                        help="One round: reminders, previews, Publisher, comments. Then exit")
    parser.add_argument("--publish", action="store_true", help="Run the Publisher once and exit")
    parser.add_argument("--check", action="store_true", help="Show which services are real or simulated")
    parser.add_argument("--mock", action="store_true", help="Fixed sample text (demo and tests)")
    parser.add_argument("--bot", action="store_true",
                        help="Always on: answers from the phone (Telegram), reminders, Publisher on time")
    parser.add_argument("--telegram-setup", action="store_true", help="Check the bot token; show your chat ID")
    args = parser.parse_args()
    if args.mock:
        config.MOCK = True
    if args.check:
        print_services()
    elif args.telegram_setup or args.bot or args.once:
        from social_agent import bot
        bot.setup() if args.telegram_setup else bot.once() if args.once else bot.serve()
    elif args.tool:
        print(json.dumps(run_tool(args.business, args.tool, json.loads(args.args)), indent=2,
                         ensure_ascii=False, default=str))
    elif args.publish:
        from social_agent.agents import publisher
        from social_agent.bot import LogApp
        from social_agent.business_space import BusinessSpace
        print(publisher.run(BusinessSpace(args.business), LogApp()))
    else:
        parser.print_help()


def run_tool(business: str, name: str, tool_args: dict) -> dict:
    """One team step. Draft previews and alerts go to Discord."""
    from social_agent.agents.manager import TOOLS, Manager
    from social_agent.bot import LogApp
    from social_agent.business_space import BusinessSpace

    if name == "steps":
        return TOOLS
    return Manager(BusinessSpace(business), LogApp()).run_tool(name, tool_args)


def print_services() -> None:
    print("\nOutside services (a key in .env switches each one on):")
    for name, real, key in config.services():
        print(f"  [{'REAL' if real else 'SIMULATED'}] {name}" + ("" if real else f"  <- set {key}"))


if __name__ == "__main__":
    main()
