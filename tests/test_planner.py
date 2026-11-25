"""Planner tests (step 2). No API key needed.  Run:  python -m unittest -v"""
import json
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest import mock

from social_agent import config, llm
from tests.test_flow import Base, tool

BRIEF = {"business_name": "Sweet Crumbs", "business_type": "bakery", "location": "Pune",
         "timezone": "Asia/Kolkata", "brand": "Family bakery", "brand_voice": "warm",
         "offers": ["chocolate cake", "cookies", "sourdough bread"], "audience": "families",
         "goals": ["walk-ins"], "platforms": ["instagram", "facebook"], "posts_per_week": 3}


def make_space(name="planner-bakery", brief=None):
    from social_agent.business_space import BusinessSpace
    space = BusinessSpace(name)
    space.save_brand_brief(brief or BRIEF)
    space.save_connected_accounts({p: {"status": "connected"} for p in ["instagram", "facebook"]})
    return space


class PlannerMockTest(Base):
    def setUp(self):
        super().setUp()
        self.patches.append(mock.patch.object(config, "MOCK", True))
        self.patches[-1].start()

    def test_weekly_calendar(self):
        from social_agent.agents import planner
        space = make_space()
        r = planner.plan_week(space)
        self.assertEqual(r["status"], "planned")
        posts = space.get_calendar()
        tz = planner.local_tz(BRIEF)
        days = [datetime.fromisoformat(p["scheduled_at"]).astimezone(tz).strftime("%A") for p in posts]
        self.assertEqual(days, ["Monday", "Wednesday", "Friday"])
        self.assertEqual([p["format"] for p in posts], ["post", "reel", "carousel"])
        self.assertTrue(all(p["scheduled_at"].endswith("+05:30") for p in posts))  # Local time zone.
        reel = posts[1]["script"]
        for key in ("hook", "script", "caption", "hashtags"):  # Plan: hook, script, caption, hashtags.
            self.assertTrue(reel[key], key)
        self.assertTrue(all(s["show"] and s["say"] for s in reel["script"]))  # What to show, what to say.
        self.assertTrue(all(t.startswith("#") for t in reel["hashtags"]))
        self.assertTrue(posts[0]["script"]["copy_idea"])
        self.assertTrue(posts[2]["script"]["slides"])
        # Plan file, ready for the Creator.
        plan = json.loads((space.root / "content_plan.json").read_text())
        self.assertEqual(len(plan["ready_for_creator"]), 3)
        self.assertEqual(plan["weeks"][0]["days"][1]["day"], "Wednesday")
        self.assertTrue((space.media_folder(posts[1]["id"]) / "script.json").exists())
        # Second run: the week is already planned.
        self.assertEqual(planner.plan_week(space)["status"], "already_planned")

    def test_news_goes_to_next_free_day_and_uses_learnings(self):
        from social_agent.agents import planner
        from social_agent.business_space import now_utc
        space = make_space()
        tz = planner.local_tz(BRIEF)
        tomorrow = (datetime.now(tz) + timedelta(days=1)).date()
        busy = [datetime.combine(datetime.now(tz).date() + timedelta(days=i), time(12), tz) for i in range(2)]
        space.add_posts([{"topic": "busy", "format": "post", "platforms": ["instagram"],
                          "scheduled_at": t.isoformat(), "script": {}} for t in busy])
        space.add_learnings(["Best format: carousel (engagement 9%)", "Best hour: 10 (engagement 8%)"])
        r = planner.run(space, "I baked a new chocolate cake, I want people to know")
        new = space.get_post(r["posts"][0]["id"])
        when = datetime.fromisoformat(new["scheduled_at"]).astimezone(tz)
        self.assertEqual(when.date(), tomorrow + timedelta(days=1))  # Today and tomorrow are busy.
        self.assertEqual(when.time(), time(10, 0))  # Learning: best hour.
        self.assertEqual(new["format"], "carousel")  # Learning: best format.
        self.assertEqual(new["topic"], "New chocolate cake")
        self.assertGreater(datetime.fromisoformat(new["scheduled_at"]), now_utc())

    def test_blocked_without_brief(self):
        from social_agent.agents import planner
        from social_agent.business_space import BusinessSpace
        self.assertEqual(planner.run(BusinessSpace("empty"), "x")["status"], "blocked")

    def test_manager_routes_news_and_week(self):
        from social_agent.agents.manager import Manager
        from tests.test_flow import ScriptedApp
        space = make_space()
        m = Manager(space, ScriptedApp(["yes"]))
        out = m.handle("plan my week")
        self.assertIn("Planner made your week", out)
        m.handle("I baked a new chocolate cake, I want people to know")
        statuses = sorted(p["status"] for p in space.get_calendar())
        self.assertEqual(statuses, ["approved", "planned", "planned", "planned"])  # Only the news post drafted.


class PlannerClaudeTest(Base):
    """Claude mode with a fake Claude: the Planner fixes bad times and script shapes."""

    def setUp(self):
        super().setUp()
        self.patches.append(mock.patch.object(config, "MOCK", False))
        self.patches[-1].start()

    def test_claude_plan_is_normalised(self):
        from social_agent.agents import planner
        from social_agent.business_space import now_utc
        space = make_space()
        claude_posts = {"posts": [{
            "topic": "New chocolate cake", "format": "reel", "platforms": ["instagram", "tiktok"],
            "scheduled_at": "2020-01-01T10:00:00+05:30",  # In the past: must be fixed.
            "script": {"hook": "Look at this!", "script": "Cake close-up, then a slice.",
                       "caption": "New cake!", "hashtags": ["cake", "#Pune Bakery"]}}]}
        prompts = []

        def fake_structured(system, prompt, name, schema):
            prompts.append(prompt)
            return claude_posts

        with mock.patch.object(llm, "structured", fake_structured), \
                mock.patch("social_agent.agents.planner.get_trends", return_value=["Winter treats"]):
            space.add_learnings(["Reels get 2x reach"])
            r = planner.run(space, "I baked a new chocolate cake")
        post = space.get_post(r["posts"][0]["id"])
        self.assertGreater(datetime.fromisoformat(post["scheduled_at"]), now_utc())
        self.assertEqual(post["platforms"], ["instagram"])  # TikTok is not connected.
        self.assertEqual(post["script"]["script"][0]["show"], "Cake close-up, then a slice.")
        self.assertEqual(post["script"]["hashtags"], ["#cake", "#PuneBakery"])
        self.assertIn("Reels get 2x reach", prompts[0])  # Learnings from the Analyst.
        self.assertIn("Winter treats", prompts[0])  # Trends.

    def test_manager_routes_news_to_planner(self):
        from social_agent.agents.manager import Manager
        from tests.test_flow import ScriptedApp
        space = make_space()
        replies = [SimpleNamespace(content=[tool("plan_posts", {"task": "new chocolate cake"})]),
                   SimpleNamespace(content=[tool("show_plan", {}, "tu2")]),
                   SimpleNamespace(content=[SimpleNamespace(type="text", text="Planned for tomorrow!")])]
        with mock.patch.object(llm, "call_claude", lambda *a, **k: replies.pop(0)), \
                mock.patch.object(llm, "structured", lambda *a: {"posts": [{
                    "topic": "Cake", "format": "post", "platforms": ["instagram"],
                    "scheduled_at": (now() + timedelta(days=2)).isoformat(),
                    "script": {"copy_idea": "Cake photo", "caption": "New!", "hashtags": ["#cake"]}}]}), \
                mock.patch("social_agent.agents.planner.get_trends", return_value=[]):
            out = Manager(space, ScriptedApp([])).handle("I baked a new chocolate cake, I want people to know")
        self.assertEqual(out, "Planned for tomorrow!")
        self.assertEqual(len(space.get_calendar()), 1)
        self.assertEqual(space.get_content_plan()["ready_for_creator"], [space.get_calendar()[0]["id"]])


def now():
    from social_agent.business_space import now_utc
    return now_utc()
