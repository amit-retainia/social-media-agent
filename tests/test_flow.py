"""Tests. No API key needed.  Run:  python -m unittest -v

1. Mock mode: the whole loop (interview -> plan -> create -> approval -> post -> measure).
2. Agent mode: the agent writes the text after each "needs_text" answer, like Grok Bot does.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from social_agent import config


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


KEYS = ("MACHGEN_API_KEY", "OPENAI_API_KEY", "RUNWAYML_API_SECRET", "FISH_API_KEY", "GROQ_API_KEY",
        "INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_USER_ID", "FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_TOKEN",
        "DISCORD_WEBHOOK_URL", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET", "ALERT_WEBHOOK_URL", "SMTP_HOST", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET",
        "LINKEDIN_ACCESS_TOKEN", "LINKEDIN_AUTHOR_URN")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(config, "DATA_DIR", self.tmp),
                        mock.patch.object(config, "PUBLISH_RETRY_WAIT", 0),
                        mock.patch.object(config, "MOCK", False),
                        mock.patch.object(config, "TEST_SHARE", 0)]  # No A/B tests unless a test turns them on.
        # Real keys on this computer must never reach a test: each test sets the fake keys it needs.
        for key in KEYS:
            self.patches.append(mock.patch.object(config, key, ""))
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp)


class MockFlowTest(Base):
    def setUp(self):
        super().setUp()
        self.patches.append(mock.patch.object(config, "MOCK", True))
        self.patches[-1].start()

    def test_full_loop(self):
        from social_agent.agents import interviewer
        from social_agent.agents.manager import Manager
        from social_agent.business_space import BusinessSpace

        space = BusinessSpace("test-bakery")
        app = ScriptedApp(INTERVIEW)
        interviewer.run(space, app)
        self.assertEqual(space.get_brand_brief()["business_name"], "Sweet Crumbs")
        self.assertEqual(space.connected_platforms(), ["instagram", "facebook"])

        m = Manager(space, app)
        plan = m.run_tool("plan_posts", {"task": "I baked a new chocolate cake, I want people to know"})
        post_id = plan["posts"][0]["id"]
        self.assertEqual(plan["posts"][0]["format"], "reel")
        self.assertTrue((space.root / "content_plan.json").exists())
        self.assertEqual(m.run_tool("create_drafts", {"post_ids": [post_id]})["status"], "drafts_ready")
        self.assertEqual(m.run_tool("request_changes", {"post_id": post_id, "note": "Make it more fun"})["status"],
                         "remade")
        self.assertEqual(m.run_tool("approve_draft", {"post_id": post_id})["status"], "approved")
        post = space.get_post(post_id)
        self.assertEqual(post["draft"]["version"], 2)
        self.assertTrue((space.root / post["draft"]["files"][0]).exists())

        m.run_tool("publish_due", {"post_now": True})
        self.assertTrue(all(p["status"] == "posted" for p in space.get_calendar()))
        self.assertTrue(any(who == "ALERT" for who, _ in app.log))  # hard comment went to owner

        m.run_tool("run_analyst", {"simulate_time_passed": True})
        self.assertTrue(space.get_learnings())

        # Dexere is the record: export the rows, lose the disk, restore from the rows.
        rows = m.run_tool("export", {})
        self.assertEqual(rows["posts"][0]["post_id"], post_id)
        self.assertEqual(rows["drafts"][0]["draft_id"], f"{post_id}-v2")
        self.assertTrue(rows["metrics"] and rows["comments"] and rows["learnings"])
        self.assertEqual({c["status"] for c in rows["comments"]}, {"replied", "sent_to_owner"})
        before = space.get_post(post_id)
        shutil.rmtree(space.root)
        fresh = BusinessSpace("test-bakery")
        out = Manager(fresh, app).run_tool("restore", {"rows": rows})
        self.assertEqual(out["posts"], 1)
        after = fresh.get_post(post_id)
        self.assertEqual((after["status"], after["draft"]["version"], after["draft"]["final_copy"]),
                         (before["status"], 2, before["draft"]["final_copy"]))
        self.assertEqual(fresh.get_brand_brief()["business_name"], "Sweet Crumbs")
        self.assertIn("error", Manager(fresh, app).run_tool("restore", {"rows": rows}))  # Only on a fresh disk.

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


class AgentWritesTextTest(Base):
    """No MOCK: every step that needs text asks the agent for it, then takes it."""

    def test_agent_loop(self):
        from social_agent.agents.manager import Manager
        from tests.test_planner import make_space
        space = make_space("agent-bakery")
        m = Manager(space, ScriptedApp([]))

        ask = m.run_tool("plan_posts", {"task": "new chocolate cake"})
        self.assertEqual(ask["status"], "needs_text")
        self.assertIn("free_slots", ask["data"])
        self.assertEqual(space.get_calendar(), [])  # Nothing is planned before the agent writes it.
        posts = [{"topic": "New chocolate cake", "format": "post", "platforms": ["instagram"],
                  "scheduled_at": "2020-01-01T10:00:00+05:30",
                  "script": {"copy_idea": "Cake on a wooden table", "caption": "New!", "hashtags": ["cake"]}}]
        plan = m.run_tool("plan_posts", {"task": "new chocolate cake", "posts": posts})
        post_id = plan["posts"][0]["id"]

        ask = m.run_tool("create_drafts", {})
        self.assertEqual(ask["status"], "needs_text")
        self.assertEqual(ask["data"]["posts"][0]["id"], post_id)
        draft = {"final_copy": "Our new chocolate cake is here!", "caption": "New cake", "hashtags": ["#cake"],
                 "image_prompts": ["A chocolate cake on a wooden table, soft window light"]}
        made = m.run_tool("create_drafts", {"drafts": {post_id: draft}})
        self.assertEqual(made["status"], "check_first")  # The agent checks first; the owner sees nothing yet.
        self.assertIn("self-check", made["check"].lower())
        self.assertIn("error", m.run_tool("approve_draft", {"post_id": post_id}))  # Not before the check.
        self.assertEqual(m.run_tool("mark_checked", {"post_id": post_id, "checks": []})["status"], "pending_approval")
        self.assertEqual(space.get_post(post_id)["draft"]["final_copy"], "Our new chocolate cake is here!")

        ask = m.run_tool("request_changes", {"post_id": post_id, "note": "say it is eggless"})
        self.assertEqual(ask["status"], "needs_text")
        self.assertEqual(ask["data"]["posts"][0]["owner_change_notes"], ["say it is eggless"])
        m.run_tool("create_drafts", {"drafts": {post_id: dict(draft, final_copy="Eggless chocolate cake!")}})
        m.run_tool("mark_checked", {"post_id": post_id, "checks": []})
        self.assertEqual(space.get_post(post_id)["draft"]["version"], 2)
        self.assertEqual(m.run_tool("approve_draft", {"post_id": post_id})["status"], "approved")

        m.run_tool("publish_due", {"post_now": True})
        comments = m.run_tool("show_comments", {})
        self.assertEqual(comments["status"], "needs_text")
        friendly = [c for c in comments["data"]["comments"] if "amazing" in c["text"]]
        self.assertEqual(len(comments["data"]["comments"]), 1)  # The complaint went to the owner instead.
        out = m.run_tool("reply_comments", {"replies": [{"post_id": post_id, "comment_id": friendly[0]["comment_id"],
                                                         "text": "Thank you!"}]})
        self.assertEqual(out["results"][0]["result"], "replied")
        self.assertEqual(m.run_tool("show_comments", {})["status"], "nothing_to_do")

        ask = m.run_tool("run_analyst", {"simulate_time_passed": True})
        self.assertEqual(ask["status"], "needs_text")
        m.run_tool("save_learnings", {"learnings": ["Best format: reel"]})
        self.assertEqual(space.get_learnings()[-1]["learning"], "Best format: reel")


if __name__ == "__main__":
    unittest.main()
