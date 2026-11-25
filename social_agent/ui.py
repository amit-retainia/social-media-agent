"""How agents talk to the user. Today: the terminal.

Later you can swap this for a phone app, WhatsApp, Telegram, or a web page.
"""


class ConsoleUI:
    def say(self, who: str, text: str) -> None:
        print(f"\n{who}: {text}")

    def ask(self, who: str, question: str) -> str:
        self.say(who, question)
        return input("You: ").strip()
