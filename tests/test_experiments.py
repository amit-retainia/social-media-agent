"""Tests of the A/B tests (experiments.py): the queue, the Planner's test posts, and the decision."""
from datetime import datetime
from unittest import mock

from social_agent import config
from tests.test_analyst import numbers, posted
from tests.test_flow import Base
from tests.test_planner import make_space


class TestsOn(Base):
    def setUp(self):
        super().setUp()
        self.patches.append(mock.patch.object(config, "TEST_SHARE", 0.3))
        self.patches[-1].start()


class QueueTest(TestsOn):
    def test_owner_idea_goes_first_and_bad_or_double_ideas_are_refused(self):
        from social_agent import experiments
        space = make_space("queue")
        experiments.add(space, "idea", "3 tips carousel", evidence="@cakeshop: 5x usual likes", source="scout")
        out = experiments.add(space, "format", "video", source="owner", first=True)
        self.assertEqual(out["queue_position"], 1)
        self.assertEqual(experiments.add(space, "format", "Video")["status"], "already_in_queue")
        self.assertIn("error", experiments.add(space, "format", "story"))
        self.assertIn("error", experiments.add(space, "hour", 3))  # The Planner only follows 6-22.
        self.assertEqual(experiments.current(space)["value"], "video")
        self.assertEqual(experiments.show(space)["waiting_for_owner"][0]["idea"], "3 tips carousel")

    def test_empty_queue_gets_formats_and_hours_not_tried(self):
        from social_agent import experiments
        space = make_space("auto")
        space.add_learnings(["Best format: reel (instagram, 7d: score 40 vs 20)"])
        test = experiments.current(space)
        self.assertEqual((test["change"], test["value"]), ("format", "carousel"))  # Not reel: that is the usual way.
        queued = [(t["change"], t["value"]) for t in experiments.show(space)["queue"]]
        self.assertNotIn(("format", "reel"), queued)
        self.assertIn(("hour", 12), queued)

    def test_nothing_runs_when_tests_are_off(self):
        from social_agent import experiments
        space = make_space("off")
        with mock.patch.object(config, "TEST_SHARE", 0):
            self.assertIsNone(experiments.current(space))


class OwnerOkTest(TestsOn):
    def test_content_idea_waits_for_the_owners_yes(self):
        from social_agent import experiments
        space = make_space("ok-yes")
        out = experiments.add(space, "idea", "Show the price on screen", evidence="trend: price reels", source="agent")
        self.assertEqual(out["status"], "waiting_for_owner")
        self.assertIn("Try it", out["ask_owner"])
        self.assertEqual(experiments.show(space)["queue"], [])  # Not in the queue before the owner says yes.
        self.assertEqual(experiments.answer(space, out["test"]["id"], True, via="chat")["status"], "queued")
        self.assertIn("error", experiments.answer(space, out["test"]["id"], True, via="chat"))  # Already decided.

    def test_no_parks_the_idea_and_silence_skips_it(self):
        from social_agent import experiments
        space = make_space("ok-no")
        first = experiments.add(space, "idea", "Jokes about other bakeries", source="scout")["test"]
        experiments.answer(space, first["id"], False, via="chat")
        self.assertEqual(experiments.add(space, "idea", "Jokes about other bakeries", source="scout")["status"],
                         "parked")
        second = experiments.add(space, "idea", "Dance trend reel", source="agent")["test"]
        tests = space.get_experiments()
        next(t for t in tests if t["id"] == second["id"])["added_at"] = "2020-01-01T00:00:00+00:00"
        space.save_experiments(tests)
        out = experiments.answer(space, second["id"], True, via="chat")  # Too late: 3 days passed.
        self.assertIn("skipped", out["error"])

    def test_formats_and_owner_ideas_need_no_question(self):
        from social_agent import experiments
        space = make_space("ok-none")
        self.assertEqual(experiments.add(space, "format", "video", source="agent")["status"], "queued")
        self.assertEqual(experiments.add(space, "idea", "Videos of me baking", source="owner")["status"], "queued")

    def test_owner_taps_try_it_on_the_phone(self):
        from social_agent import experiments
        from social_agent.agents import owner_approval
        from tests.test_owner_approval import CHAT, FakeTelegram
        fake = FakeTelegram()
        with mock.patch("social_agent.tools.http.requests.request", fake),                 mock.patch.object(config, "TELEGRAM_BOT_TOKEN", "TEST:TOKEN"),                 mock.patch.object(config, "TELEGRAM_CHAT_ID", CHAT):
            space = make_space("ok-phone")
            out = experiments.add(space, "idea", "3 tips carousel", evidence="@cakeshop: 5x likes", source="scout")
            self.assertEqual(out["asked_on"], "telegram")
            ask = fake.sent("sendMessage")[-1].params
            self.assertIn("3 tips carousel", ask["text"])
            token = ask["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
            fake.press(token, message_id=fake.next_msg)
            owner_approval.poll()
        self.assertEqual(experiments.show(space)["queue"][0]["idea"], "3 tips carousel")
        self.assertIn("Added to the tests", fake.sent("sendMessage")[-1].params["text"])


class PlannerTest(TestsOn):
    def setUp(self):
        super().setUp()
        self.patches.append(mock.patch.object(config, "MOCK", True))
        self.patches[-1].start()

    def test_format_test_in_the_weekly_plan(self):
        from social_agent import experiments
        from social_agent.agents import planner
        space = make_space("plan-format")
        test = experiments.add(space, "format", "carousel", source="owner")["test"]
        r = planner.plan_week(space)
        tagged = [p for p in space.get_calendar() if p.get("test_id") == test["id"]]
        self.assertEqual(len(tagged), 1)  # 3 posts a week: 1 test post (30%).
        self.assertEqual(tagged[0]["format"], "carousel")
        self.assertTrue(tagged[0]["why"].startswith("Test: carousel"))
        self.assertEqual(r["test"]["posts"], [tagged[0]["id"]])
        self.assertEqual(experiments.show(space)["running"]["posts"], [tagged[0]["id"]])

    def test_hour_test_posts_at_that_hour(self):
        from social_agent import experiments
        from social_agent.agents import planner
        space = make_space("plan-hour")
        test = experiments.add(space, "hour", "12:00", source="owner")["test"]
        planner.plan_week(space)
        tagged = [p for p in space.get_calendar() if p.get("test_id") == test["id"]]
        self.assertEqual(datetime.fromisoformat(tagged[0]["scheduled_at"]).hour, 12)

    def test_agent_is_told_which_post_is_the_test(self):
        from social_agent import experiments
        from social_agent.agents import planner
        space = make_space("plan-agent")
        experiments.add(space, "idea", "Start with the price on screen", source="owner")
        with mock.patch.object(config, "MOCK", False):
            ask = planner.plan_week(space)
        self.assertEqual(ask["status"], "needs_text")
        self.assertEqual(ask["data"]["tests"][0]["test"], "Start with the price on screen")
        self.assertIn("is a TEST", ask["data"]["task"])


class DecisionTest(TestsOn):
    def run_test(self, name, test_shares, usual_shares=10, change="format", value="carousel"):
        """A running test with its posts measured at 3 days, and 3 usual posts to compare with."""
        from social_agent import experiments
        space = make_space(name)
        for _ in range(3):
            posted(space, "reel", hours_ago=200, metrics={"3d": {"instagram": numbers(1000, shares=usual_shares)}})
        test = experiments.add(space, change, value)["test"]
        experiments.current(space)
        ids = [posted(space, value if change == "format" else "reel", hours_ago=200,
                      metrics={"3d": {"instagram": numbers(1000, shares=s)}})["id"] for s in test_shares]
        experiments.assign(space, test["id"], ids)
        return space, test

    def status(self, space, test):
        return next(t for t in space.get_experiments() if t["id"] == test["id"])

    def test_won_becomes_the_usual_way(self):
        from social_agent import experiments
        from social_agent.agents import planner
        space, test = self.run_test("won", [20, 22])
        out = experiments.review(space)
        self.assertTrue(out[0].startswith("Best format: carousel (test won: +110%"))
        self.assertEqual(self.status(space, test)["status"], "won")
        best_format, _ = planner._learned_preferences([x["learning"] for x in space.get_learnings()])
        self.assertEqual(best_format, "carousel")
        self.assertIn("Test result", space._read("alerts", [])[-1]["alert"])  # The owner is told.
        self.assertNotEqual(experiments.current(space)["id"], test["id"])  # The next idea starts.

    def test_win_stays_pinned_for_the_planner(self):
        from social_agent import experiments
        from social_agent.agents import planner
        space, _ = self.run_test("pinned", [20, 22])
        experiments.review(space)
        space.add_learnings([f"Too early to pick a best hour on instagram ({i})" for i in range(15)])
        space.add_learnings(["Best format: video (instagram, 24h: score 30 vs 25)"])  # A newer guess, not a test.
        learnings = planner.learnings_for(space)
        self.assertTrue(learnings[-1].startswith("Best format: carousel (test won"))
        self.assertEqual(planner._learned_preferences(learnings)[0], "carousel")

    def test_lost_is_parked(self):
        from social_agent import experiments
        space, test = self.run_test("lost", [2, 3])
        self.assertTrue(experiments.review(space)[0].startswith("Test lost: carousel"))
        self.assertTrue(self.status(space, test)["retry_after"])
        self.assertEqual(experiments.add(space, "format", "carousel")["status"], "parked")

    def test_unclear_gets_1_more_post_then_no_difference(self):
        from social_agent import experiments
        space, test = self.run_test("unclear", [11, 11])  # +10%: a small gap.
        self.assertIn("1 more test post", experiments.review(space)[0])
        self.assertEqual(self.status(space, test)["needed"], 3)
        self.assertEqual(experiments.review(space), [])  # Waits for the new test post.
        more = posted(space, "carousel", hours_ago=200, metrics={"3d": {"instagram": numbers(1000, shares=11)}})
        experiments.assign(space, test["id"], [more["id"]])
        self.assertIn("made no clear difference", experiments.review(space)[0])
        self.assertEqual(self.status(space, test)["status"], "no_difference")

    def test_one_lucky_post_cannot_win_alone(self):
        from social_agent import experiments
        space, test = self.run_test("lucky", [40, 5])  # Middle score +125%, but one post is worse than usual.
        self.assertIn("unclear so far", experiments.review(space)[0])
        more = posted(space, "carousel", hours_ago=200, metrics={"3d": {"instagram": numbers(1000, shares=30)}})
        experiments.assign(space, test["id"], [more["id"]])
        self.assertTrue(experiments.review(space)[0].startswith("Best format: carousel (test won"))  # 2 of 3 better.

    def test_waits_for_numbers_at_the_test_checkpoint(self):
        from social_agent import experiments
        space, test = self.run_test("waiting", [20])
        young = posted(space, "carousel", hours_ago=30, metrics={"24h": {"instagram": numbers(1000, shares=20)}})
        experiments.assign(space, test["id"], [young["id"]])
        self.assertEqual(experiments.review(space), [])
        self.assertIn("1 of 2 test posts measured", experiments.status_line(space))

    def test_tests_go_to_dexere_and_come_back(self):
        from social_agent import records
        from social_agent.business_space import BusinessSpace
        space, test = self.run_test("records", [20, 20])
        rows = records.export(space)
        self.assertEqual(rows["tests"][0]["test_id"], test["id"])
        fresh = BusinessSpace("records-tests-copy")
        records.restore(fresh, rows)
        self.assertEqual(fresh.get_experiments()[0]["posts"], self.status(space, test)["posts"])
