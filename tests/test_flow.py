"""Tests. No API key needed.  Run:  python -m unittest -v

1. Mock mode: the whole loop (interview -> plan -> create -> approval -> post -> measure).
2. Claude mode with a FAKE Claude: checks the tool-use wiring of Manager and Interviewer.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from social_agent import config, llm


class ScriptedApp:
    """Owner app that answers from a list."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.log = []

    def say(self, who, text):
        self.log.append((who, text))

    def ask(self, who, question):
        self.log.append((who, question))
        return self.answers.pop(0)

    def alert(self, text):
        self.log.append(("ALERT", text))

    def preview(self, post):
        self.log.append(("PREVIEW", post["id"]))


INTERVIEW = ["Sweet Crumbs", "bakery", "Pune", "Family bakery since 1998", "warm and playful",
             "cakes, cookies, bread", "families and students", "more walk-ins, more orders",
             "skip", "instagram, facebook", "3", "skip"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(config, "DATA_DIR", self.tmp),
                        mock.patch.object(config, "PUBLISH_RETRY_WAIT", 0)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp)


class MockFlowTest(Base):
    def setUp(self):
        super().setUp()
        self.patches.append(mock.patch.object(config, "MOCK", True))
        self.patches[-1].start()

    def test_full_loop(self):
        from social_agent.agents.manager import Manager
        from social_agent.business_space import BusinessSpace

        space = BusinessSpace("test-bakery")
        # Interview, then news: 1 post -> ask for a change, then approve.
        app = ScriptedApp(INTERVIEW + ["Make it more fun", "yes"])
        m = Manager(space, app)
        m.handle("hi")
        self.assertEqual(space.get_brand_brief()["business_name"], "Sweet Crumbs")
        self.assertEqual(space.connected_platforms(), ["instagram", "facebook"])

        m.handle("I baked a new chocolate cake, I want people to know")
        cal = space.get_calendar()
        self.assertEqual(len(cal), 1)
        self.assertEqual(cal[0]["format"], "reel")
        self.assertTrue((space.root / "content_plan.json").exists())
        self.assertTrue(all(p["status"] == "approved" for p in cal))
        changed = [p for p in cal if p["change_notes"]]
        self.assertEqual(changed[0]["draft"]["version"], 2)
        self.assertTrue((space.root / changed[0]["draft"]["files"][0]).exists())

        m.handle("publish now")
        self.assertTrue(all(p["status"] == "posted" for p in space.get_calendar()))
        self.assertTrue(any(who == "ALERT" for who, _ in app.log))  # hard comment went to owner

        m.handle("how did my posts do?")
        self.assertTrue(space.get_learnings())

    def test_publisher_retries_then_reschedules(self):
        from social_agent.agents import publisher
        from social_agent.business_space import BusinessSpace

        space = BusinessSpace("fail-bakery")
        space.add_posts([{"topic": "t", "format": "post", "platforms": ["instagram"],
                          "scheduled_at": "2020-01-01T10:00:00+00:00", "script": {}, "status": "approved"}])
        app = ScriptedApp([])
        with mock.patch.object(config, "SIMULATE_FAIL_RATE", 1.0), \
                mock.patch("social_agent.tools.platforms.publish", wraps=publisher.platforms.publish) as pub:
            r = publisher.run(space, app)
        self.assertEqual(pub.call_count, 3)
        self.assertEqual(r["published"][0]["result"], "failed")
        post = space.get_calendar()[0]
        self.assertEqual(post["status"], "approved")
        # The Planner moved it to a new free time, at least one day from now.
        from social_agent.business_space import now_utc, parse_time
        self.assertGreater(parse_time(post["scheduled_at"]), now_utc())


# ---- fake Claude ----
def text(t):
    return SimpleNamespace(type="text", text=t)


def tool(name, inp, id_="tu1"):
    return SimpleNamespace(type="tool_use", name=name, input=inp, id=id_)


class FakeClaudeTest(Base):
    def setUp(self):
        super().setUp()
        self.patches.append(mock.patch.object(config, "MOCK", False))
        self.patches[-1].start()

    def test_manager_routes_to_interviewer(self):
        from social_agent.agents.manager import Manager
        from social_agent.business_space import BusinessSpace

        brief = {"business_name": "Sweet Crumbs", "business_type": "bakery", "brand": "family",
                 "brand_voice": "warm", "offers": ["cake"], "audience": "families",
                 "goals": ["walk-ins"], "platforms": ["instagram"]}
        replies = [
            SimpleNamespace(content=[tool("run_interviewer", {})]),                  # Manager
            SimpleNamespace(content=[text("What is your business called?")]),        # Interviewer
            SimpleNamespace(content=[tool("connect_accounts", {"platforms": ["Instagram"]})]),
            SimpleNamespace(content=[tool("save_brand_brief", brief)]),
            SimpleNamespace(content=[text("All set! What news do you have?")]),       # Manager
        ]
        calls = []

        def fake(system, messages, tools=None, tool_choice=None):
            calls.append([dict(m) for m in messages])
            return replies.pop(0)

        with mock.patch.object(llm, "call_claude", fake):
            space = BusinessSpace("fake")
            out = Manager(space, ScriptedApp(["Sweet Crumbs"])).handle("hello")
        self.assertEqual(out, "All set! What news do you have?")
        self.assertEqual(space.get_brand_brief()["business_name"], "Sweet Crumbs")
        self.assertEqual(space.connected_platforms(), ["instagram"])
        # The last Manager call got a tool_result for the interviewer.
        last = calls[-1][-1]["content"][0]
        self.assertEqual(last["type"], "tool_result")


if __name__ == "__main__":
    unittest.main()
