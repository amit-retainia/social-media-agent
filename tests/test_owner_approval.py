"""Owner approval (step 4): terminal and phone (Telegram, against a fake Bot API server)."""
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from social_agent import config
from tests.test_flow import Base, ScriptedApp

CHAT = "111"


class FakeTelegram:
    """Replaces requests.request for api.telegram.org. Owner answers are queued as updates."""

    def __init__(self):
        self.calls, self.updates, self.next_id, self.next_msg = [], [], 1, 100

    def press(self, data, message_id, chat=CHAT):
        self._add({"callback_query": {"id": f"cb{self.next_id}", "data": data,
                                      "message": {"message_id": message_id, "chat": {"id": int(chat)}}}})

    def text(self, text, reply_to=None, chat=CHAT):
        msg = {"message_id": 999, "chat": {"id": int(chat)}, "text": text}
        if reply_to:
            msg["reply_to_message"] = {"message_id": reply_to}
        self._add({"message": msg})

    def _add(self, u):
        u["update_id"] = self.next_id
        self.next_id += 1
        self.updates.append(u)

    def sent(self, method):
        return [c for c in self.calls if c.method == method]

    def __call__(self, method_, url, **kw):
        method = url.rsplit("/", 1)[1]
        assert "/botTEST:TOKEN/" in url, url
        params = kw.get("json") or kw.get("data") or {}
        self.calls.append(SimpleNamespace(method=method, params=params, files=kw.get("files")))
        if method == "getUpdates":
            offset = params.get("offset") or 0
            result = [u for u in self.updates if u["update_id"] >= offset]
        elif method in ("sendMessage", "sendPhoto", "sendVideo"):
            self.next_msg += 1
            result = {"message_id": self.next_msg}
        elif method == "sendMediaGroup":
            result = [{"message_id": 50}, {"message_id": 51}]
        else:
            result = True
        return SimpleNamespace(status_code=200, text="", json=lambda: {"ok": True, "result": result})


class Setup(Base):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(config, "MOCK", True),):
            p.start()
            self.patches.append(p)
        from social_agent.business_space import BusinessSpace, now_utc
        self.space = BusinessSpace("sweet-crumbs")
        self.space.save_brand_brief({"business_name": "Sweet Crumbs", "brand_voice": "warm",
                                     "timezone": "Asia/Kolkata"})
        self.space.add_posts([{"topic": "New chocolate cake", "format": "post", "platforms": ["instagram"],
                               "scheduled_at": (now_utc() + timedelta(days=2)).isoformat(),
                               "script": {"copy_idea": "cake", "caption": "cake", "hashtags": ["#cake"]}}])
        self.pid = self.space.get_calendar()[0]["id"]

    def phone(self):
        fake = FakeTelegram()
        for p in (mock.patch("social_agent.tools.http.requests.request", fake),
                  mock.patch.object(config, "TELEGRAM_BOT_TOKEN", "TEST:TOKEN"),
                  mock.patch.object(config, "TELEGRAM_CHAT_ID", CHAT)):
            p.start()
            self.patches.append(p)
        return fake


class TerminalTest(Setup):
    def test_no_then_note_then_yes(self):
        from social_agent.agents import creator, owner_approval
        creator.run(self.space)
        app = ScriptedApp(["no", "Say it is eggless", "yes"])
        r = owner_approval.run(self.space, app)
        post = self.space.get_post(self.pid)
        self.assertEqual(r["results"], [{"id": self.pid, "result": "approved"}])
        self.assertEqual(post["status"], "approved")
        self.assertEqual(post["change_notes"], ["Say it is eggless"])
        self.assertEqual(post["draft"]["version"], 2)
        self.assertEqual(post["approved_via"], "terminal")


class PhoneTest(Setup):
    def test_preview_change_fix_resend_approve(self):
        fake = self.phone()
        from social_agent.agents import creator, owner_approval
        creator.run(self.space)  # Creator step 7: the draft goes to the phone.
        first = fake.sent("sendMessage")[-1]
        self.assertIn("Draft for approval (version 1)", first.params["text"])
        buttons = first.params["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([b["text"] for b in buttons], ["✅ Approve", "✏️ Ask for changes"])
        msg_id = self.space.get_post(self.pid)["approval_request"]["message_id"]
        # No duplicate "new draft" alert on the phone (the preview is the alert).
        self.assertFalse(any("new draft" in c.params.get("text", "") for c in fake.sent("sendMessage")))

        # Ask for changes -> the bot asks for a short note -> the Creator fixes -> a new preview.
        fake.press(buttons[1]["callback_data"], msg_id)
        fake.text("Please say it is eggless")
        owner_approval.poll()
        post = self.space.get_post(self.pid)
        self.assertEqual(post["change_notes"], ["Please say it is eggless"])
        self.assertEqual(post["status"], "pending_approval")
        self.assertEqual(post["draft"]["version"], 2)
        self.assertTrue(any(c.params.get("reply_markup", {}).get("force_reply") for c in fake.sent("sendMessage")))
        new = fake.sent("sendMessage")[-1]
        self.assertIn("version 2", new.params["text"])

        # The old button no longer works.
        fake.press(buttons[0]["callback_data"], msg_id)
        owner_approval.poll()
        self.assertEqual(self.space.get_post(self.pid)["status"], "pending_approval")

        # Approve the new version -> status "approved" (the Publisher's queue).
        fake.press(new.params["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
                   self.space.get_post(self.pid)["approval_request"]["message_id"])
        owner_approval.poll()
        post = self.space.get_post(self.pid)
        self.assertEqual(post["status"], "approved")
        self.assertEqual(post["approved_via"], "telegram")
        self.assertIn("Approved", fake.sent("sendMessage")[-1].params["text"])
        self.assertIn("approved", self.space.get_content_plan() and str(self.space.get_content_plan()))

    def test_reply_yes_to_preview_and_review_tool(self):
        fake = self.phone()
        from social_agent.agents import creator, owner_approval
        creator.run(self.space)
        msg_id = self.space.get_post(self.pid)["approval_request"]["message_id"]
        fake.text("yes", reply_to=msg_id)
        r = owner_approval.run(self.space, ScriptedApp([]), wait_seconds=5)  # Manager's owner_review tool
        self.assertEqual(r["owner_app"], "telegram")
        self.assertEqual(r["results"], [{"id": self.pid, "result": "approved"}])

    def test_reply_note_and_stranger_ignored(self):
        fake = self.phone()
        from social_agent.agents import creator, owner_approval
        creator.run(self.space)
        msg_id = self.space.get_post(self.pid)["approval_request"]["message_id"]
        fake.text("yes", reply_to=msg_id, chat="999")  # Not the owner.
        fake.text("Use a brighter photo", reply_to=msg_id)
        owner_approval.poll()
        post = self.space.get_post(self.pid)
        self.assertEqual(post["change_notes"], ["Use a brighter photo"])
        self.assertEqual(post["status"], "pending_approval")

    def test_media_is_sent(self):
        fake = self.phone()
        from social_agent.agents import owner_approval
        folder = self.space.media_folder(self.pid)
        (folder / "v1_1x1_image1.jpg").write_bytes(b"JPEG")
        (folder / "v1_9x16_image1.jpg").write_bytes(b"JPEG")
        self.space.update_post(self.pid, status="pending_approval", draft={
            "version": 1, "final_copy": "Cake!", "caption": "c", "hashtags": [],
            "files": [f"media_library/{self.pid}/v1_9x16_image1.jpg", f"media_library/{self.pid}/v1_1x1_image1.jpg"],
            "sizes": {"instagram": "1:1"}})
        owner_approval.send_previews(self.space)
        photo = fake.sent("sendPhoto")
        self.assertEqual(len(photo), 1)
        self.assertEqual(photo[0].files["photo"][0], "v1_1x1_image1.jpg")  # the first platform's shape
        self.assertEqual(owner_approval.send_previews(self.space), [])  # not sent twice

    def test_alerts_go_to_phone(self):
        fake = self.phone()
        self.space.add_alert("Post failed on instagram")
        self.assertIn("Post failed on instagram", fake.sent("sendMessage")[-1].params["text"])


class SlowApprovalTest(Setup):
    def test_reminder_then_move(self):
        from social_agent.agents import creator, owner_approval
        from social_agent.business_space import now_utc, parse_time
        creator.run(self.space)
        app = ScriptedApp([])
        self.space.update_post(self.pid, scheduled_at=(now_utc() + timedelta(hours=1)).isoformat())
        self.assertEqual(owner_approval.check_overdue(self.space, app)[0]["result"], "reminded")
        self.assertEqual(owner_approval.check_overdue(self.space, app), [])  # only once
        self.space.update_post(self.pid, scheduled_at=(now_utc() - timedelta(hours=1)).isoformat())
        out = owner_approval.check_overdue(self.space, app)
        self.assertEqual(out[0]["result"], "moved")
        self.assertGreater(parse_time(self.space.get_post(self.pid)["scheduled_at"]), now_utc())
        self.assertEqual(self.space.get_post(self.pid)["status"], "pending_approval")


class BotTest(Setup):
    def test_bot_round_publishes_approved(self):
        fake = self.phone()
        from social_agent import bot
        from social_agent.agents import creator
        from social_agent.business_space import now_utc
        creator.run(self.space)
        msg_id = self.space.get_post(self.pid)["approval_request"]["message_id"]
        self.space.update_post(self.pid, scheduled_at=(now_utc() + timedelta(days=2)).isoformat())
        fake.text("ok", reply_to=msg_id)
        with mock.patch("social_agent.bot.time.sleep"):
            bot.serve(rounds=1)
        self.assertEqual(self.space.get_post(self.pid)["status"], "approved")
        # Due now -> the next tick posts it (simulated platforms).
        self.space.update_post(self.pid, scheduled_at=(now_utc() - timedelta(minutes=1)).isoformat())
        bot.tick(bot.LogApp())
        self.assertEqual(self.space.get_post(self.pid)["status"], "posted")
