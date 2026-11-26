"""Real-service code paths, tested with a fake HTTP server (no keys, no network).

Each test sets a dummy key, so the real integration switches on, and checks the requests it sends.
"""
import base64
import subprocess
from types import SimpleNamespace
from unittest import mock

from social_agent import config
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
                headers = body.pop("_headers", {}) if isinstance(body, dict) else {}
                return SimpleNamespace(status_code=status, content=b"", text=str(body), json=lambda b=body: b,
                                       headers=headers)
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

    def test_voice_fish_and_groq_word_times(self):
        from social_agent.tools import ai_models
        from tests.test_creator import make_mp3
        mp3 = make_mp3(self.tmp / "src.mp3", 2.0)
        heard = {"words": [{"word": " Hello,", "start": 0.1, "end": 0.5}, {"word": "dear", "start": 0.6, "end": 0.9},
                           {"word": "friends!", "start": 1.0, "end": 1.6}]}
        fake, patch = http([("POST", "api.fish.audio/v1/tts", mp3), ("POST", "/audio/transcriptions", heard)])
        with patch, mock.patch.object(config, "FISH_API_KEY", "fish-test"), \
                mock.patch.object(config, "GROQ_API_KEY", "gsk-test"):
            out, alignment = ai_models.voiceover_with_timing("Hello, my friends!", self.tmp / "voice")
        self.assertEqual(out.suffix, ".mp3")
        self.assertEqual(out.read_bytes(), mp3)
        self.assertEqual("".join(alignment["characters"]), "Hello, my friends!")
        starts = alignment["character_start_times_seconds"]
        self.assertAlmostEqual(starts[0], 0.1)  # "Hello," heard at 0.1 s
        self.assertAlmostEqual(starts[10], 1.0)  # "friends!" heard at 1.0 s
        self.assertTrue(0.5 <= starts[7] < 1.0)  # "my" was misheard: placed in the gap
        tts = fake.calls[0]
        self.assertEqual(tts.headers["Authorization"], "Bearer fish-test")
        self.assertEqual(tts.headers["model"], "s2.1-pro-free")
        self.assertEqual(tts.json["text"], "Hello, my friends!")
        self.assertEqual(tts.json["reference_id"], config.FISH_VOICE_ID)
        self.assertEqual(fake.calls[1].data["timestamp_granularities[]"], "word")

    def test_voice_without_groq_is_spread_over_voice_length(self):
        from social_agent.tools import ai_models
        from tests.test_creator import make_mp3
        _, patch = http([("POST", "api.fish.audio/v1/tts", make_mp3(self.tmp / "src.mp3", 2.0))])
        with patch, mock.patch.object(config, "FISH_API_KEY", "fish-test"), \
                mock.patch.object(config, "GROQ_API_KEY", ""):
            _, alignment = ai_models.voiceover_with_timing("One two", self.tmp / "voice")
        self.assertAlmostEqual(alignment["character_end_times_seconds"][-1], 2.0, delta=0.1)

    def test_voice_check_finds_missing_words(self):
        from social_agent.tools import ai_models
        from tests.test_creator import make_mp3
        media = self.tmp / "final.mp3"
        make_mp3(media, 2.0)
        heard = {"words": [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.2}
                           for i, w in enumerate(["Our", "new", "cake", "is", "here."])]}
        _, patch = http([("POST", "/audio/transcriptions", heard)])
        with patch, mock.patch.object(config, "GROQ_API_KEY", "gsk"):
            out = ai_models.verify_words(media, "Our new chocolate cake is here.")
        self.assertEqual((out["ok"], out["missing"]), (False, ["chocolate"]))

    def test_say_spelling_times_move_to_caption_words(self):
        from social_agent.tools import ai_models
        from tests.test_creator import alignment_for
        spoken = "Price is four-ninety-nine rupees"
        al = ai_models.realign(alignment_for(spoken, 4.0), "Price is Rs 499", 4.0)
        self.assertEqual("".join(al["characters"]), "Price is Rs 499")
        self.assertAlmostEqual(al["character_start_times_seconds"][9], 9 / len(spoken) * 4.0, places=2)

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


class MetaTest(Base):
    """Instagram and Facebook through Meta's Graph API. Media links come from R2 (faked here)."""

    def setUp(self):
        super().setUp()
        for name, value in [("INSTAGRAM_ACCESS_TOKEN", "ig-tok"), ("INSTAGRAM_USER_ID", "ig-user"),
                            ("FACEBOOK_PAGE_ID", "fb-page"), ("FACEBOOK_PAGE_TOKEN", "fb-tok")]:
            self.patches.append(mock.patch.object(config, name, value))
            self.patches[-1].start()
        self.patches.append(mock.patch("social_agent.tools.storage.public_url",
                                       lambda path, key, tenant=None: f"https://r2.example/{path.name}"))
        self.patches[-1].start()
        self.patches.append(mock.patch("social_agent.tools.platforms.time.sleep"))
        self.patches[-1].start()
        from social_agent.tools import platforms
        platforms._uploaded.clear()

    def post(self, fmt, names):
        from social_agent.business_space import BusinessSpace
        space = BusinessSpace("meta")
        post = space.add_posts([{"topic": "New cake", "format": fmt, "platforms": ["instagram"],
                                 "scheduled_at": "2020-01-01T10:00:00+00:00", "script": {}}])[0]
        files = []
        for n in names:
            f = space.media_folder(post["id"]) / n
            f.write_bytes(b"media")
            files.append(str(f.relative_to(space.root)))
        post["draft"] = {"final_copy": "Our new cake!", "hashtags": ["#cake"], "files": files}
        return space, post

    def test_connect(self):
        from social_agent.tools import platforms
        _, patch = http([("GET", "graph.instagram.com/v23.0/me", {"user_id": "1", "username": "sweetcrumbs"}),
                         ("GET", "graph.facebook.com/v23.0/fb-page", {"_status": 400, "error": "Invalid OAuth token"})])
        with patch:
            self.assertEqual(platforms.connect_account("instagram")["account"], "sweetcrumbs")
            self.assertEqual(platforms.connect_account("facebook")["status"], "needs_login")
            self.assertEqual(platforms.connect_account("tiktok")["status"], "not_supported")

    def test_instagram_reel_comments_reply_metrics(self):
        from social_agent.tools import platforms
        space, post = self.post("reel", ["v1_9x16_final.mp4"])
        fake, patch = http([
            ("POST", "ig-user/media_publish", {"id": "m1"}),
            ("POST", "ig-user/media", {"id": "cont1"}),
            ("GET", "/cont1", {"status_code": "FINISHED"}),
            ("GET", "/m1/comments", {"data": [{"id": "c1", "text": "Yum!", "username": "ana"}]}),
            ("POST", "/c1/replies", {"id": "r1"}),
            ("GET", "/m1/insights", {"data": [{"name": "reach", "values": [{"value": 300}]},
                                              {"name": "views", "values": [{"value": 450}]},
                                              {"name": "likes", "values": [{"value": 12}]},
                                              {"name": "comments", "values": [{"value": 3}]}]}),
            ("GET", "/m1", {"permalink": "https://instagram.com/reel/1"}),
        ])
        with patch:
            res = platforms.publish("instagram", post, space)
            create = next(c for c in fake.calls if c.url.endswith("ig-user/media"))
            self.assertEqual(create.params["media_type"], "REELS")
            self.assertEqual(create.params["video_url"], "https://r2.example/v1_9x16_final.mp4")
            self.assertIn("#cake", create.params["caption"])
            self.assertEqual(create.params["access_token"], "ig-tok")
            self.assertEqual((res["platform_post_id"], res["url"]), ("m1", "https://instagram.com/reel/1"))
            post["publish_results"] = {"instagram": res}
            comments = platforms.fetch_new_comments("instagram", post, space)
            self.assertEqual(comments, [{"id": "c1", "from": "ana", "text": "Yum!"}])
            platforms.reply("instagram", "c1", "Thank you!", space)
            m = platforms.get_metrics("instagram", post, "24h", space)
        self.assertEqual((m["likes"], m["comments"], m["reach"], m["views"]), (12, 3, 300, 450))

    def test_instagram_carousel_and_facebook_photos(self):
        from social_agent.tools import platforms
        space, post = self.post("carousel", ["v1_1x1_image1.jpg", "v1_1x1_image2.jpg"])
        ids = iter(range(100))
        fake, patch = http([
            ("POST", "ig-user/media_publish", {"id": "m2"}),
            ("POST", "ig-user/media", lambda kw: {"id": f"ig{next(ids)}"}),
            ("GET", "graph.instagram.com/v23.0/ig", {"status_code": "FINISHED"}),
            ("GET", "/m2", {"permalink": "https://instagram.com/p/2"}),
            ("POST", "fb-page/photos", lambda kw: {"id": f"ph{next(ids)}"}),
            ("POST", "fb-page/feed", {"id": "fb-page_99"}),
        ])
        with patch:
            platforms.publish("instagram", post, space)
            res = platforms.publish("facebook", post, space)
        creates = [c.params for c in fake.calls if c.url.endswith("ig-user/media")]
        self.assertEqual([c.get("is_carousel_item") for c in creates], ["true", "true", None])
        self.assertEqual(creates[2]["media_type"], "CAROUSEL")
        photos = [c.params for c in fake.calls if c.url.endswith("fb-page/photos")]
        self.assertEqual([p["published"] for p in photos], ["false", "false"])
        self.assertEqual(res["platform_post_id"], "fb-page_99")

    def test_publish_errors(self):
        from social_agent.tools import platforms
        space, post = self.post("post", ["v1_1x1_image1.jpg"])
        _, patch = http([("POST", "ig-user/media", {"_status": 400,
                                                    "error": "Error validating access token: Session has expired"})])
        with patch, self.assertRaises(platforms.PublishError) as err:
            platforms.publish("instagram", post, space)
        self.assertTrue(err.exception.login_expired)
        with self.assertRaises(platforms.PublishError) as err:
            platforms.publish("tiktok", post, space)  # Not supported yet: no retry.
        self.assertTrue(err.exception.permanent)
        # A placeholder image cannot be posted: no retry.
        post["draft"]["files"] = ["media_library/p1/v1_1x1_image1.svg"]
        with self.assertRaises(platforms.PublishError) as err:
            platforms.publish("instagram", post, SimpleNamespace(root=self.tmp, tenant_id="t"))
        self.assertTrue(err.exception.permanent)

    def test_keys_per_business(self):
        from social_agent.tools import platforms
        with mock.patch.dict("os.environ", {"INSTAGRAM_USER_ID_SWEET_CRUMBS": "own-user"}):
            self.assertEqual(platforms._keys("instagram", "sweet-crumbs"), ("own-user", "ig-tok"))
            self.assertEqual(platforms._keys("instagram", "other"), ("ig-user", "ig-tok"))


class XAndLinkedInTest(Base):
    """X (API v2, media from the disk) and LinkedIn (Posts API, media uploaded to LinkedIn)."""

    def setUp(self):
        super().setUp()
        for name, value in [("X_API_KEY", "xk"), ("X_API_SECRET", "xs"), ("X_ACCESS_TOKEN", "xt"),
                            ("X_ACCESS_SECRET", "xa"), ("LINKEDIN_ACCESS_TOKEN", "li-tok"),
                            ("LINKEDIN_AUTHOR_URN", "urn:li:organization:42")]:
            self.patches.append(mock.patch.object(config, name, value))
            self.patches[-1].start()
        self.patches.append(mock.patch("social_agent.tools.x_api._auth", lambda creds: None))
        self.patches[-1].start()

    def post(self, fmt, names, platforms):
        from social_agent.business_space import BusinessSpace
        space = BusinessSpace("xli")
        post = space.add_posts([{"topic": "New cake", "format": fmt, "platforms": platforms,
                                 "scheduled_at": "2020-01-01T10:00:00+00:00", "script": {}}])[0]
        files = []
        for n in names:
            f = space.media_folder(post["id"]) / n
            f.write_bytes(b"media-bytes")
            files.append(str(f.relative_to(space.root)))
        post["draft"] = {"final_copy": "Our new cake (fresh)!", "hashtags": ["#cake"], "files": files}
        return space, post

    def test_x_photo_video_metrics(self):
        from social_agent.tools import platforms
        fake, patch = http([
            ("GET", "/2/users/me", {"data": {"username": "sweetcrumbs"}}),
            ("POST", "/2/media/upload/initialize", {"data": {"id": "v1"}}),
            ("POST", "/2/media/upload/v1/append", {}),
            ("POST", "/2/media/upload/v1/finalize", {"data": {"processing_info": {"state": "succeeded"}}}),
            ("POST", "/2/media/upload", {"data": {"id": "img1"}}),
            ("POST", "/2/tweets", {"data": {"id": "t1"}}),
            ("GET", "/2/tweets/t1", {"data": {"public_metrics": {"impression_count": 500, "like_count": 9,
                                                                 "reply_count": 2}}}),
        ])
        with patch:
            self.assertEqual(platforms.connect_account("x")["account"], "sweetcrumbs")
            space, post = self.post("post", ["v1_16x9_image1.jpg"], ["x"])
            res = platforms.publish("x", post, space)
            body = next(c for c in fake.calls if c.url.endswith("/2/tweets")).json
            self.assertEqual(body["media"], {"media_ids": ["img1"]})
            self.assertIn("#cake", body["text"])
            self.assertEqual(res["url"], "https://x.com/i/status/t1")
            space, post = self.post("video", ["v1_16x9_final.mp4"], ["x"])
            platforms.publish("x", post, space)
            self.assertTrue(any(c.url.endswith("/v1/finalize") for c in fake.calls))
            post["publish_results"] = {"x": res}
            m = platforms.get_metrics("x", post, "24h", space)
            self.assertEqual(platforms.fetch_new_comments("x", post, space), [])  # Not read on X.
        self.assertEqual((m["reach"], m["likes"], m["comments"]), (500, 9, 2))

    def test_linkedin_images_comments_reply_metrics(self):
        from social_agent.tools import platforms
        images = iter(range(10))
        fake, patch = http([
            ("POST", "rest/images", lambda kw: {"value": {"uploadUrl": "https://upload.li/x",
                                                          "image": f"urn:li:image:{next(images)}"}}),
            ("PUT", "upload.li", {}),
            ("POST", "rest/posts", {"_status": 201, "_headers": {"x-restli-id": "urn:li:share:9"}}),
            ("GET", "/comments", {"elements": [{"$URN": "urn:li:comment:(urn:li:share:9,5)",
                                                "actor": "urn:li:person:a", "message": {"text": "Nice!"}}]}),
            ("POST", "/comments", {}),
            ("GET", "rest/socialActions/", {"likesSummary": {"totalLikes": 7},
                                            "commentsSummary": {"aggregatedTotalComments": 1}}),
        ])
        space, post = self.post("carousel", ["v1_1x1_image1.jpg", "v1_1x1_image2.jpg"], ["linkedin"])
        with patch:
            res = platforms.publish("linkedin", post, space)
            body = next(c for c in fake.calls if c.url.endswith("rest/posts")).json
            self.assertEqual(body["author"], "urn:li:organization:42")
            self.assertEqual(len(body["content"]["multiImage"]["images"]), 2)
            self.assertIn("\\(fresh\\)", body["commentary"])  # LinkedIn's reserved characters are escaped.
            self.assertIn("#cake", body["commentary"])
            self.assertEqual(res["platform_post_id"], "urn:li:share:9")
            self.assertEqual(fake.calls[0].headers["LinkedIn-Version"], "202510")
            post["publish_results"] = {"linkedin": res}
            comments = platforms.fetch_new_comments("linkedin", post, space)
            self.assertEqual(comments[0]["text"], "Nice!")
            platforms.reply("linkedin", comments[0]["id"], "Thank you!", space)
            reply = [c for c in fake.calls if c.method == "POST" and c.url.endswith("/comments")][-1].json
            self.assertEqual(reply["parentComment"], "urn:li:comment:(urn:li:share:9,5)")
            m = platforms.get_metrics("linkedin", post, "24h", space)
        self.assertEqual((m["likes"], m["comments"]), (7, 1))
        self.assertIn("reach", m["gaps"])  # LinkedIn does not give reach here.


class FakeS3:
    """In-memory R2 bucket (the boto3 calls media.py and storage.py use)."""

    def __init__(self):
        self.objects, self.uploads = {}, []

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Metadata": self.objects[Key][1]}

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.uploads.append(key)
        self.objects[key] = (open(path, "rb").read(), (ExtraArgs or {}).get("Metadata", {}))

    def download_file(self, bucket, key, path):
        open(path, "wb").write(self.objects[key][0])

    def get_paginator(self, name):
        fake = self
        return SimpleNamespace(paginate=lambda Bucket, Prefix: [
            {"Contents": [{"Key": k} for k in fake.objects if k.startswith(Prefix)]}])


class MediaKeepTest(Base):
    """Like CB Reels: paid images, final files and owner media are kept in R2; clips and voice stay on disk."""

    def test_push_then_pull_on_a_fresh_disk(self):
        import shutil
        from social_agent.business_space import BusinessSpace
        from social_agent.tools import media
        s3 = FakeS3()
        space = BusinessSpace("keep")
        folder = space.media_folder("post-1")
        (folder / "cache").mkdir()
        for name in ["cache/image-abc.png", "cache/frame-def.png", "cache/clip-123.mp4", "cache/voice-9.mp3",
                     "v1_9x16_final.mp4", "v1_1x1_image1.jpg", "v1_captions.srt", "draft_v1.json"]:
            (folder / name).write_bytes(b"x" + name.encode())
        with mock.patch("social_agent.tools.storage.client", lambda: s3),                 mock.patch.object(config, "R2_BUCKET", "sms-media"):
            keys = media.push(space, "post-1")
            self.assertEqual(sorted(k.rsplit("/", 1)[1] for k in keys),
                             ["frame-def.png", "image-abc.png", "v1_1x1_image1.jpg", "v1_9x16_final.mp4"])
            self.assertTrue(all(k.startswith("sms/keep/media_library/post-1/") for k in keys))
            self.assertEqual(len(media.push(space, "post-1")), 4)
            self.assertEqual(len(s3.uploads), 4)  # Unchanged files are not uploaded again.
            shutil.rmtree(folder)  # A fresh disk.
            self.assertEqual(media.pull(space, "post-1"), 4)
        self.assertEqual((folder / "cache" / "image-abc.png").read_bytes(), b"xcache/image-abc.png")


class ApprovalInChatTest(Base):
    """On Grok Bot: draft previews go to Discord; the owner answers in the chat (Manager tools)."""

    def test_discord_preview_then_change_and_approve(self):
        from social_agent.agents import creator
        from social_agent.agents.manager import Manager
        from tests.test_flow import ScriptedApp
        from tests.test_planner import make_space
        space = make_space("chat-bakery")
        post = space.add_posts([{"topic": "Cake", "format": "post", "platforms": ["instagram"],
                                 "scheduled_at": "2030-01-01T10:00:00+00:00",
                                 "script": {"copy_idea": "Cake photo", "caption": "New!", "hashtags": ["#cake"]}}])[0]
        fake, patch = http([("POST", "discord.example", {})])

        def previews():
            return [c for c in fake.calls if c.data and "payload_json" in c.data]
        with patch, mock.patch.object(config, "MOCK", True), \
                mock.patch.object(config, "DISCORD_WEBHOOK_URL", "https://discord.example/hook"):
            creator.run(space)
            self.assertEqual(len(previews()), 1)
            self.assertIn(f"approve {post['id']}", previews()[0].data["payload_json"])
            manager = Manager(space, ScriptedApp([]))
            self.assertEqual([d["id"] for d in manager.run_tool("show_drafts", {})["drafts"]], [post["id"]])
            out = manager.run_tool("request_changes", {"post_id": post["id"], "note": "say it is eggless"})
            self.assertEqual(out["draft"]["version"], 2)
            self.assertEqual(len(previews()), 2)  # The new version is sent again.
            self.assertEqual(manager.run_tool("approve_draft", {"post_id": post["id"]})["status"], "approved")
            self.assertIn("error", manager.run_tool("approve_draft", {"post_id": post["id"]}))
        self.assertEqual(space.get_post(post["id"])["status"], "approved")


class OtherServicesTest(Base):
    def test_alert_webhook(self):
        from social_agent.business_space import BusinessSpace
        fake, patch = http([("POST", "hooks.example", {})])
        with patch, mock.patch.object(config, "ALERT_WEBHOOK_URL", "https://hooks.example/abc"):
            BusinessSpace("alerts").add_alert("Post failed")
        self.assertIn("Post failed", fake.calls[0].json["text"])

    def test_services_list(self):
        with mock.patch.object(config, "OPENAI_API_KEY", "x"), mock.patch.object(config, "INSTAGRAM_USER_ID", ""), \
                mock.patch.object(config, "MACHGEN_API_KEY", ""):
            status = {name: real for name, real, _ in config.services()}
        self.assertTrue(status["AI images (Creator): OpenAI"])
        with mock.patch.object(config, "OPENAI_API_KEY", "x"), mock.patch.object(config, "MACHGEN_API_KEY", "m"), \
                mock.patch.object(config, "RUNWAYML_API_SECRET", "r"):
            status = {name: real for name, real, _ in config.services()}
        self.assertTrue(status["AI images (Creator): MachGen"])  # MachGen is the default.
        self.assertTrue(status["AI video clips (Creator): MachGen"])
        self.assertFalse(status["Instagram: post, replies, metrics"])


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
