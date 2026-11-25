"""Real-service code paths, tested with a fake HTTP server (no keys, no network).

Each test sets a dummy key, so the real integration switches on, and checks the requests it sends.
"""
import base64
import subprocess
from types import SimpleNamespace
from unittest import mock

from social_agent import config, llm
from tests.test_flow import Base


class FakeHTTP:
    """Replaces requests.request. routes: list of (method, url part, response)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, **kw):
        self.calls.append(SimpleNamespace(method=method, url=url, **{k: kw.get(k) for k in
                                                                     ("json", "headers", "params", "data")}))
        for m, part, resp in self.routes:
            if m == method and part in url:
                body = resp(kw) if callable(resp) else resp
                if isinstance(body, bytes):
                    return SimpleNamespace(status_code=200, content=body, text="", json=lambda: {})
                status = body.pop("_status", 200) if isinstance(body, dict) else 200
                return SimpleNamespace(status_code=status, content=b"", text=str(body), json=lambda b=body: b)
        raise AssertionError(f"Unexpected request {method} {url}")


def http(routes):
    fake = FakeHTTP(routes)
    return fake, mock.patch("social_agent.tools.http.requests.request", fake)


class AIModelsTest(Base):
    def setUp(self):
        super().setUp()
        self.patches.append(mock.patch.object(config, "MACHGEN_API_KEY", ""))  # Test the other providers.
        self.patches[-1].start()

    def test_image_openai(self):
        from social_agent.tools import ai_models
        png = base64.b64encode(b"\x89PNG fake").decode()
        fake, patch = http([("POST", "/images/generations", {"data": [{"b64_json": png}]})])
        with patch, mock.patch.object(config, "OPENAI_API_KEY", "sk-test"):
            out = ai_models.generate_image("chocolate cake", "9:16", self.tmp / "img")
        self.assertEqual(out.suffix, ".png")
        self.assertEqual(out.read_bytes(), b"\x89PNG fake")
        self.assertEqual(fake.calls[0].json["size"], "1024x1824")
        self.assertEqual(fake.calls[0].json["model"], "gpt-image-2")
        self.assertEqual(fake.calls[0].headers["Authorization"], "Bearer sk-test")
        # Older GPT Image models only take 3 sizes.
        with patch, mock.patch.object(config, "OPENAI_API_KEY", "sk-test"), \
                mock.patch.object(config, "OPENAI_IMAGE_MODEL", "gpt-image-1.5"):
            ai_models.generate_image("chocolate cake", "9:16", self.tmp / "img2")
        self.assertEqual(fake.calls[1].json["size"], "1024x1536")

    def test_image_placeholder_without_key(self):
        from social_agent.tools import ai_models
        with mock.patch.object(config, "OPENAI_API_KEY", ""):
            self.assertEqual(ai_models.generate_image("cake", "1:1", self.tmp / "img").suffix, ".svg")

    def test_video_runway(self):
        from social_agent.tools import ai_models
        states = iter([{"id": "t1", "status": "RUNNING"},
                       {"id": "t1", "status": "SUCCEEDED", "output": ["https://dnznrvs05pmza.cloudfront.net/t1.mp4"]}])
        fake, patch = http([("POST", "/v1/text_to_video", {"id": "t1", "status": "PENDING"}),
                            ("GET", "/v1/tasks/t1", lambda kw: next(states)),
                            ("GET", "cloudfront.net/t1.mp4", b"MP4DATA")])
        with patch, mock.patch.object(config, "RUNWAYML_API_SECRET", "key_test"), \
                mock.patch("social_agent.tools.ai_models.time.sleep"):
            out = ai_models.generate_video_clip({"visual": "cake close-up", "seconds": 3}, "16:9", self.tmp / "c")
        self.assertEqual(out.read_bytes(), b"MP4DATA")
        self.assertEqual(fake.calls[0].json, {"model": "gen4.5", "promptText": "cake close-up",
                                              "ratio": "1280:720", "duration": 3})
        self.assertEqual(fake.calls[0].headers["X-Runway-Version"], "2024-11-06")

    def test_voice_elevenlabs(self):
        from social_agent.tools import ai_models
        al = {"characters": list("Hello"), "character_start_times_seconds": [0, .1, .2, .3, .4],
              "character_end_times_seconds": [.1, .2, .3, .4, .5]}
        fake, patch = http([("POST", "/text-to-speech/", {"audio_base64": base64.b64encode(b"MP3DATA").decode(),
                                                           "alignment": al, "normalized_alignment": al})])
        with patch, mock.patch.object(config, "ELEVENLABS_API_KEY", "el-test"):
            out, alignment = ai_models.voiceover_with_timing("Hello", self.tmp / "voice")
        self.assertEqual(out.suffix, ".mp3")
        self.assertEqual(out.read_bytes(), b"MP3DATA")
        self.assertEqual(alignment["characters"], list("Hello"))
        self.assertTrue(fake.calls[0].url.endswith("/with-timestamps"))
        self.assertEqual(fake.calls[0].params["output_format"], "mp3_44100_128")
        self.assertEqual(fake.calls[0].headers["xi-api-key"], "el-test")
        self.assertEqual(fake.calls[0].json["text"], "Hello")

    def test_edit_video_with_ffmpeg(self):
        from social_agent.tools import ai_models
        ff = ai_models.ffmpeg_exe()
        clips = []
        for i, color in enumerate(["red", "blue"]):
            clip = self.tmp / f"clip{i}.mp4"
            subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color={color}:s=320x240:d=1",
                            "-pix_fmt", "yuv420p", str(clip)], check=True)
            clips.append(clip)
        voice = self.tmp / "voice.mp3"
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=2", str(voice)],
                       check=True)
        from social_agent.tools import media_edit
        srt = media_edit.write_srt([(0.0, 1.0, "New cake!"), (1.0, 2.5, "Come try it")], self.tmp / "c.srt")
        warnings = []
        out = media_edit.edit_video(clips, [1.0, 1.5], voice, srt, "9:16", self.tmp / "final", warnings)
        self.assertEqual(out.suffix, ".mp4")
        self.assertGreater(out.stat().st_size, 1000)
        self.assertAlmostEqual(media_edit.media_duration(out), 2.5, delta=0.15)  # Clip 1 held to 1.5 s.
        self.assertEqual(warnings, [])  # Captions were burned in.


class AyrshareTest(Base):
    def setUp(self):
        super().setUp()
        self.patches.append(mock.patch.object(config, "AYRSHARE_API_KEY", "ayr-test"))
        self.patches[-1].start()

    def test_connect(self):
        from social_agent.tools import platforms
        _, patch = http([("GET", "/api/user", {"activeSocialAccounts": ["instagram", "twitter"]})])
        with patch:
            self.assertEqual(platforms.connect_account("x")["status"], "connected")
            self.assertEqual(platforms.connect_account("facebook")["status"], "needs_login")

    def test_publish_with_media_upload_comments_reply_metrics(self):
        from social_agent.business_space import BusinessSpace
        from social_agent.tools import platforms
        space = BusinessSpace("ayr")
        post = space.add_posts([{"topic": "New cake", "format": "reel", "platforms": ["instagram"],
                                 "scheduled_at": "2020-01-01T10:00:00+00:00", "script": {}}])[0]
        clip = space.media_folder(post["id"]) / "v1_9x16_final.mp4"
        clip.write_bytes(b"video")
        post["draft"] = {"final_copy": "Our new cake!", "hashtags": ["#cake"],
                         "files": [str(clip.relative_to(space.root))]}
        fake, patch = http([
            ("GET", "/media/uploadUrl", {"uploadUrl": "https://upload.example/x", "accessUrl": "https://cdn/x.mp4"}),
            ("PUT", "upload.example", {}),
            ("POST", "/api/post", {"status": "success", "id": "ayr1",
                                   "postIds": [{"platform": "instagram", "id": "ig1", "postUrl": "https://ig/1"}]}),
            ("GET", "/api/comments/ayr1", {"instagram": [{"commentId": "c1", "comment": "Yum!", "username": "ana"}]}),
            ("POST", "/comments/reply/c1", {"status": "success"}),
            ("POST", "/analytics/post", {"instagram": {"analytics": {"likeCount": 12, "commentsCount": 3,
                                                                    "reachCount": 300, "videoViews": 450}}}),
        ])
        with patch:
            res = platforms.publish("instagram", post, space)
            body = next(c for c in fake.calls if c.url.endswith("/api/post")).json
            self.assertEqual(body["mediaUrls"], ["https://cdn/x.mp4"])
            self.assertTrue(body["instagramOptions"]["reels"])
            self.assertIn("#cake", body["post"])
            self.assertEqual(res["ayrshare_id"], "ayr1")
            post["publish_results"] = {"instagram": res}
            comments = platforms.fetch_new_comments("instagram", post, space)
            self.assertEqual(comments, [{"id": "c1", "from": "ana", "text": "Yum!"}])
            platforms.reply("instagram", "c1", "Thank you!", space)
            m = platforms.get_metrics("instagram", post, "24h", space)
        self.assertEqual((m["likes"], m["comments"], m["reach"], m["views"]), (12, 3, 300, 450))

    def test_publish_errors(self):
        from social_agent.tools import platforms
        post = {"id": "p1", "topic": "t", "format": "post", "platforms": ["linkedin"],
                "draft": {"final_copy": "hi", "files": []}}
        _, patch = http([("POST", "/api/post", {"status": "error", "errors": [
            {"message": "LinkedIn token expired. Please re-link."}]})])
        with patch, self.assertRaises(platforms.PublishError) as err:
            platforms.publish("linkedin", post, None)
        self.assertTrue(err.exception.login_expired)
        # A placeholder image cannot be posted: no retry.
        post["draft"]["files"] = ["media_library/p1/v1_1x1_image1.svg"]
        with self.assertRaises(platforms.PublishError) as err:
            platforms.publish("linkedin", post, SimpleNamespace(root=self.tmp, tenant_id="t"))
        self.assertTrue(err.exception.permanent)


class OtherServicesTest(Base):
    def test_alert_webhook(self):
        from social_agent.business_space import BusinessSpace
        fake, patch = http([("POST", "hooks.example", {})])
        with patch, mock.patch.object(config, "ALERT_WEBHOOK_URL", "https://hooks.example/abc"):
            BusinessSpace("alerts").add_alert("Post failed")
        self.assertIn("Post failed", fake.calls[0].json["text"])

    def test_trends_web_search(self):
        from datetime import date
        from social_agent.tools import trends
        reply = SimpleNamespace(content=[SimpleNamespace(type="text", text='Here: ["Christmas cakes", "Diwali sweets"]')])
        calls = []
        with mock.patch.object(config, "MOCK", False), \
                mock.patch.object(llm, "call_claude", lambda *a, **k: calls.append(k) or reply):
            out = trends.get_trends("bakery", date(2026, 11, 25), "Pune")
        self.assertEqual(out, ["Christmas cakes", "Diwali sweets"])
        self.assertEqual(calls[0]["tools"][0]["type"], "web_search_20250305")

    def test_services_list(self):
        with mock.patch.object(config, "OPENAI_API_KEY", "x"), mock.patch.object(config, "AYRSHARE_API_KEY", ""), \
                mock.patch.object(config, "MACHGEN_API_KEY", ""):
            status = {name: real for name, real, _ in config.services()}
        self.assertTrue(status["AI images (Creator): OpenAI"])
        with mock.patch.object(config, "OPENAI_API_KEY", "x"), mock.patch.object(config, "MACHGEN_API_KEY", "m"), \
                mock.patch.object(config, "RUNWAYML_API_SECRET", "r"):
            status = {name: real for name, real, _ in config.services()}
        self.assertTrue(status["AI images (Creator): MachGen"])  # MachGen is the default.
        self.assertTrue(status["AI video clips (Creator): MachGen"])
        self.assertFalse(status["Social platforms: connect, post, replies, metrics"])


class CreatorFallbackTest(Base):
    def test_failed_image_service_gives_placeholder_and_warning(self):
        from social_agent.agents import creator
        from tests.test_planner import make_space
        space = make_space("fallback")
        post = space.add_posts([{"topic": "Cake", "format": "post", "platforms": ["instagram"],
                                 "scheduled_at": "2030-01-01T10:00:00+00:00",
                                 "script": {"copy_idea": "Cake photo", "caption": "New!", "hashtags": ["#cake"]}}])[0]
        _, patch = http([("POST", "/images/generations", {"_status": 500, "error": "server down"})])
        with patch, mock.patch.object(config, "MOCK", True), mock.patch.object(config, "OPENAI_API_KEY", "sk"):
            creator.make_draft(space, post)
        draft = space.get_post(post["id"])["draft"]
        self.assertTrue(draft["files"][0].endswith(".svg"))
        self.assertIn("HTTP 500", draft["warnings"][0])
