"""Owner app: where the business owner talks, previews posts, approves, and gets alerts.

Two parts:
  - The terminal (this file): the chat with the Manager, and reviews when Telegram is off.
  - The phone: a Telegram bot (tools/telegram.py + agents/owner_approval.py), when
    TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are in .env. Previews, Approve / Ask for changes buttons, alerts.
Later (plan): a web and mobile Owner app can use the same functions in agents/owner_approval.py
(approve, request_changes, send_preview).
"""


class ConsoleOwnerApp:
    def say(self, who: str, text: str) -> None:
        print(f"\n{who}: {text}")

    def ask(self, who: str, question: str) -> str:
        self.say(who, question)
        return input("You: ").strip()

    def alert(self, text: str) -> None:
        print(f"\n[ALERT to owner] {text}")

    def preview(self, post: dict) -> None:
        draft = post.get("draft", {})
        print("\n" + "=" * 60)
        print(f"PREVIEW  {post['id']}  |  {post['format']}  |  {', '.join(post['platforms'])}")
        print(f"Time:    {post['scheduled_at']}")
        print(f"Topic:   {post['topic']}")
        print("-" * 60)
        print(draft.get("final_copy", ""))
        print()
        print(draft.get("caption", ""))
        print(" ".join(draft.get("hashtags", [])))
        print("-" * 60)
        for f in draft.get("files", []):
            print(f"File:    {f}")
        if draft.get("media", {}).get("captions"):
            print(f"Captions: {draft['media']['captions']}")
        if draft.get("simulated"):
            print("NOTE:    Some media is SIMULATED (a key is missing). It cannot be posted until you add the key.")
        for w in draft.get("warnings", []):
            print(f"WARNING: {w}")
        print("=" * 60)
