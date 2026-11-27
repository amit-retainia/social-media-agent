"""Analyst tests (step 6): checkpoints on time, growth score, fair comparisons, followers, weekly report."""
from datetime import datetime, timedelta
from unittest import mock

from social_agent import config
from tests.test_flow import Base
from tests.test_integrations import http
from tests.test_planner import make_space


def posted(space, fmt="reel", hours_ago=2.0, platforms=("instagram",), hour=18, metrics=None):
    """A posted post, `hours_ago` hours old, scheduled at `hour` local time."""
    from social_agent.agents import planner
    from social_agent.business_space import now_utc
    tz = planner.local_tz(space.get_brand_brief())
    at = now_utc() - timedelta(hours=hours_ago)
    scheduled = at.astimezone(tz).replace(hour=hour, minute=0)
    post = space.add_posts([{"topic": f"{fmt} post", "format": fmt, "platforms": list(platforms),
                             "scheduled_at": scheduled.isoformat(), "script": {}, "status": "posted",
                             "posted_at": at.isoformat()}])[0]
    if metrics is not None:
        space.update_post(post["id"], metrics=metrics)
    return post


def numbers(reach, follows=0, shares=0, saves=0, likes=0, comments=0, gaps=(), late=False):
    return {"reach": reach, "views": reach, "likes": likes, "comments": comments, "shares": shares,
            "saves": saves, "follows": follows, "profile_visits": 0, "gaps": list(gaps), "late": late}


class CollectTest(Base):
    def test_checkpoints_are_saved_on_time_and_late_ones_are_marked(self):
        from social_agent.agents import analyst
        space = make_space("collect")
        fresh = posted(space, hours_ago=0.5)
        on_time = posted(space, hours_ago=2)
        late = posted(space, hours_ago=10)  # The bot was off: 1 h numbers taken at 10 h.
        out = analyst.collect(space)
        self.assertEqual(space.get_post(fresh["id"]).get("metrics"), None)
        m = space.get_post(on_time["id"])["metrics"]
        self.assertEqual(list(m), ["1h"])
        self.assertFalse(m["1h"]["instagram"]["late"])
        self.assertIn("shares", m["1h"]["instagram"])
        self.assertTrue(space.get_post(late["id"])["metrics"]["1h"]["instagram"]["late"])
        self.assertEqual(len(out["metrics_saved"]), 2)
        analyst.collect(space)  # Nothing new is due: nothing changes.
        self.assertEqual(space.get_post(on_time["id"])["metrics"], m)

    def test_follower_count_once_a_day(self):
        from social_agent.agents import analyst
        space = make_space("followers")
        analyst.collect(space)
        analyst.collect(space)
        rows = space.get_followers()
        self.assertEqual(sorted(r["platform"] for r in rows), ["facebook", "instagram"])
        self.assertTrue(all(isinstance(r["followers"], int) for r in rows))

    def test_bot_round_runs_the_counter(self):
        from social_agent import bot
        space = make_space("bot-counter")
        post = posted(space, hours_ago=2)
        bot.tick(bot.LogApp())
        self.assertIn("1h", space.get_post(post["id"])["metrics"])
        self.assertEqual(len(space.get_followers()), 2)
        reports = len(space.get_reports())  # 1, unless it is the report day before REPORT_HOUR.
        bot.tick(bot.LogApp())
        self.assertEqual(len(space.get_reports()), reports)  # One report a week, not one per round.


class ScoreTest(Base):
    def test_growth_score(self):
        from social_agent.agents.analyst import growth_score
        fields = ["follows", "shares", "saves"]
        self.assertEqual(growth_score(numbers(1000, follows=1, shares=2, saves=3), fields), 10.0)
        self.assertEqual(growth_score(numbers(1000, follows=6, shares=10, saves=12), fields), 50.0)
        self.assertIsNone(growth_score(numbers(50, follows=5), fields))  # Too few people saw it.
        self.assertEqual(growth_score(numbers(1000, likes=30, comments=10), []), 40.0)  # No growth numbers.


class CompareTest(Base):
    def setUp(self):
        super().setUp()
        self.patches.append(mock.patch.object(config, "MOCK", True))
        self.patches[-1].start()

    def test_best_format_needs_enough_posts_and_same_fields(self):
        from social_agent.agents import analyst, planner
        space = make_space("compare")
        for i in range(3):  # Reels: no "follows" on Instagram. Many shares.
            posted(space, "reel", hours_ago=200, metrics={"7d": {"instagram": numbers(
                1000, shares=20 + i, saves=10, gaps=["follows"])}})
        for i in range(3):  # Photo posts: a few follows, few shares.
            posted(space, "post", hours_ago=200, hour=9, metrics={"7d": {"instagram": numbers(
                1000, follows=3, shares=2 + i, saves=4)}})
        out = analyst.run(space)
        c = out["comparison"]["instagram"]
        self.assertEqual(c["score"], "growth: shares, saves")  # Same fields for every post: fair.
        self.assertEqual(c["by_format"]["reel"]["posts"], 3)
        self.assertIn("Best format: reel", " ".join(out["learnings"]))
        self.assertIn("Best hour: 18", " ".join(out["learnings"]))
        best_format, best_time = planner._learned_preferences([x["learning"] for x in space.get_learnings()])
        self.assertEqual((best_format, best_time.hour), ("reel", 18))

    def test_too_early_and_late_numbers_are_not_compared(self):
        from social_agent.agents import analyst
        space = make_space("early")
        for _ in range(2):
            posted(space, "reel", hours_ago=200, metrics={"7d": {"instagram": numbers(1000, shares=30)}})
        for _ in range(3):
            posted(space, "post", hours_ago=200, metrics={"7d": {"instagram": numbers(1000, shares=1)}})
        posted(space, "reel", hours_ago=200, metrics={"7d": {"instagram": numbers(1000, shares=99, late=True)}})
        posted(space, "reel", hours_ago=200, metrics={"7d": {"instagram": numbers(40, shares=9)}})  # Tiny reach.
        out = analyst.run(space)
        c = out["comparison"]["instagram"]
        self.assertEqual(c["by_format"]["reel"]["posts"], 2)  # The late and the tiny reels are left out.
        self.assertEqual(len(c["too_small_reach"]), 1)
        text = " ".join(out["learnings"])
        self.assertIn("Too early to pick a best format", text)
        self.assertNotIn("Best format:", text)


class ReportTest(Base):
    def test_weekly_report_once_on_report_day(self):
        from social_agent.agents import analyst, planner
        space = make_space("report")
        posted(space, "reel", hours_ago=30, metrics={"24h": {"instagram": numbers(1000, follows=4, shares=10, saves=6)}})
        space.add_followers("instagram", "2026-11-16", 1210, "meta")
        space.add_followers("instagram", "2026-11-23", 1252, "meta")
        tz = planner.local_tz(space.get_brand_brief())
        monday_early = datetime(2026, 11, 23, 8, 0, tzinfo=tz)
        self.assertIsNone(analyst.weekly_report(space, monday_early))  # Before REPORT_HOUR (9).
        text = analyst.weekly_report(space, monday_early + timedelta(hours=2))
        self.assertIn("Instagram followers: 1,210 -> 1,252 (+42)", text)
        self.assertIn('Top post: "reel post"', text)
        self.assertIsNone(analyst.weekly_report(space, monday_early + timedelta(days=2)))  # Sent this week.
        self.assertEqual(len(space.get_reports()), 1)
        self.assertTrue(analyst.weekly_report(space, monday_early + timedelta(days=7, hours=2)))  # Next week.


class PlatformNumbersTest(Base):
    def setUp(self):
        super().setUp()
        for name, value in [("INSTAGRAM_ACCESS_TOKEN", "ig-tok"), ("INSTAGRAM_USER_ID", "ig-user"),
                            ("FACEBOOK_PAGE_ID", "fb-page"), ("FACEBOOK_PAGE_TOKEN", "fb-tok"),
                            ("X_API_KEY", "k"), ("X_API_SECRET", "s"), ("X_ACCESS_TOKEN", "t"),
                            ("X_ACCESS_SECRET", "ts"), ("LINKEDIN_ACCESS_TOKEN", "li"),
                            ("LINKEDIN_AUTHOR_URN", "urn:li:organization:1")]:
            self.patches.append(mock.patch.object(config, name, value))
            self.patches[-1].start()

    def test_instagram_asks_again_with_fewer_metrics(self):
        from social_agent.tools import platforms

        def insights(kw):
            if "follows" in kw["params"]["metric"]:  # Reels: Instagram refuses follows.
                return {"_status": 400, "error": "metric follows is not supported for this media"}
            return {"data": [{"name": n, "values": [{"value": v}]} for n, v in
                             [("reach", 900), ("views", 1200), ("likes", 40), ("comments", 5), ("saved", 12),
                              ("shares", 9)]]}
        post = {"id": "p1", "format": "reel", "publish_results": {"instagram": {"platform_post_id": "m1"}}}
        fake, patch = http([("GET", "/m1/insights", insights)])
        with patch:
            m = platforms.get_metrics("instagram", post, "24h")
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual((m["reach"], m["saves"], m["shares"]), (900, 12, 9))
        self.assertEqual(sorted(m["gaps"]), ["follows", "profile_visits"])

    def test_follower_counts_and_shares(self):
        from social_agent.tools import platforms
        fake, patch = http([
            ("GET", "graph.instagram.com/v23.0/me", {"followers_count": 1252}),
            ("GET", "graph.facebook.com/v23.0/fb-page", {"followers_count": 305}),
            ("GET", "/2/users/me", {"data": {"public_metrics": {"followers_count": 77}}}),
            ("GET", "/2/tweets/t1", {"data": {"public_metrics": {"impression_count": 500, "like_count": 9,
                                                                 "reply_count": 2, "retweet_count": 3,
                                                                 "quote_count": 1, "bookmark_count": 4}}}),
            ("GET", "/fb1/insights", {"data": [{"values": [{"value": 400}]}]}),
            ("GET", "/fb1", {"reactions": {"summary": {"total_count": 20}}, "comments": {"summary": {"total_count": 2}}}),
        ])
        with patch:
            self.assertEqual(platforms.get_followers("instagram")["followers"], 1252)
            self.assertEqual(platforms.get_followers("facebook")["followers"], 305)
            self.assertEqual(platforms.get_followers("x")["followers"], 77)
            x = platforms.get_metrics("x", {"id": "p", "publish_results": {"x": {"platform_post_id": "t1"}}}, "24h")
            fb = platforms.get_metrics("facebook", {"id": "p", "publish_results": {
                "facebook": {"platform_post_id": "fb1"}}}, "24h")
        self.assertEqual((x["shares"], x["saves"]), (4, 4))
        self.assertEqual((fb["reach"], fb["shares"]), (400, 0))  # Facebook leaves out 0 shares.
        self.assertIsNone(platforms.get_followers("linkedin")["followers"])


class RecordsTest(Base):
    def test_followers_and_new_numbers_go_to_dexere_and_come_back(self):
        from social_agent import records
        from social_agent.business_space import BusinessSpace
        space = make_space("records")
        posted(space, metrics={"24h": {"instagram": numbers(1000, follows=2, shares=3)}})
        space.add_followers("instagram", "2026-11-23", 1252, "meta")
        rows = records.export(space)
        self.assertEqual(rows["metrics"][0]["follows"], 2)
        self.assertEqual(rows["followers"][0]["followers"], 1252)
        fresh = BusinessSpace("records-copy")
        records.restore(fresh, rows)
        self.assertEqual(fresh.get_followers()[0]["followers"], 1252)
