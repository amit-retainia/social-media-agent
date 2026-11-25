"""Creator tests (step 3). No keys, no network: outside services are faked.  Run:  python -m unittest -v"""
import base64
import io
import json
import subprocess
from unittest import mock

from social_agent import config
from social_agent.tools import ai_models, machgen, media_edit
from tests.test_flow import Base
from tests.test_integrations import http
from tests.test_planner import make_space

REEL_SCRIPT = {"hook": "Warm cake!", "caption": "New chocolate cake", "hashtags": ["#cake", "#bakery", "#pune"],
               "script": [{"show": "Cake on a table", "say": "Our new chocolate cake is here.", "seconds": 3},
                          {"show": "Slicing the cake", "say": "Come and taste it today.", "seconds": 3}]}
POST_SCRIPT = {"copy_idea": "Cake photo", "caption": "Fresh cake " + "so good " * 40, "hashtags":
               ["#cake", "#bakery", "#pune", "#sweet"]}


def png_bytes(w=1024, h=1024, color=(200, 120, 60)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def make_mp4(path, seconds=2, size="720x1280"):
    subprocess.run([media_edit.ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=s={size}:d={seconds}:r=24", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path.read_bytes()


def make_mp3(path, seconds=4.0):
    subprocess.run([media_edit.ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"sine=f=300:d={seconds}", str(path)], check=True)
    return path.read_bytes()


def words_for(text, seconds):
    """Groq Whisper reply: every word of `text`, spread evenly over `seconds`."""
    words = text.split()
    step = seconds / len(words)
    return {"words": [{"word": w, "start": i * step, "end": (i + 1) * step} for i, w in enumerate(words)]}


def alignment_for(text, seconds):
    step = seconds / len(text)
    return {"characters": list(text), "character_start_times_seconds": [i * step for i in range(len(text))],
            "character_end_times_seconds": [(i + 1) * step for i in range(len(text))]}


class CreatorTest(Base):
    def setUp(self):
        super().setUp()
        for name, value in [("MOCK", True), ("OPENAI_API_KEY", ""), ("RUNWAYML_API_SECRET", ""),
                            ("FISH_API_KEY", ""), ("GROQ_API_KEY", ""), ("AI_VIDEO_ENABLED", True), ("VIDEO_MODE", "text"),
                            ("MACHGEN_API_KEY", ""), ("IMAGE_PROVIDER", "auto"), ("VIDEO_PROVIDER", "auto"),
                            ("SLIDESHOW_FALLBACK", True)]:
            self.patches.append(mock.patch.object(config, name, value))
            self.patches[-1].start()
        for target in ("social_agent.tools.ai_models.time.sleep", "social_agent.tools.machgen.time.sleep"):
            self.patches.append(mock.patch(target))
            self.patches[-1].start()
        machgen._capabilities.clear()
        self.space = make_space("creator-bakery")

    def add(self, fmt, platforms, script):
        return self.space.add_posts([{"topic": "New chocolate cake", "format": fmt, "platforms": platforms,
                                      "scheduled_at": "2030-01-01T10:00:00+00:00", "script": script}])[0]

    def files(self, post):
        draft = self.space.get_post(post["id"])["draft"]
        return draft, [self.space.root / f for f in draft["files"]]

    def test_photo_post_real_image_sized_per_platform(self):
        from PIL import Image
        from social_agent.agents import creator
        post = self.add("post", ["instagram", "x"], POST_SCRIPT)
        img = base64.b64encode(png_bytes(1024, 1024)).decode()
        fake, patch = http([("POST", "/images/generations", {"data": [{"b64_json": img}]})])
        with patch, mock.patch.object(config, "OPENAI_API_KEY", "sk"):
            creator.run(self.space)
        draft, files = self.files(post)
        self.assertEqual(self.space.get_post(post["id"])["status"], "pending_approval")
        self.assertFalse(draft["simulated"])
        sizes = sorted(Image.open(f).size for f in files)
        self.assertEqual(sizes, [(1080, 1080), (1920, 1080)])  # instagram 1:1, x 16:9
        self.assertEqual(sorted(c.json["size"] for c in fake.calls), ["1024x1024", "1824x1024"])
        self.assertLessEqual(len(draft["platform_text"]["x"]), 280)
        self.assertEqual(draft["platform_text"]["x"].count("#"), 2)
        self.assertTrue(any("new draft" in a["alert"] for a in json.loads(
            (self.space.root / "alerts.json").read_text())))

    def test_owner_photo_is_used_before_ai(self):
        from social_agent.agents import creator
        post = self.add("post", ["instagram"], POST_SCRIPT)
        owner = self.space.media_folder(post["id"]) / "owner"
        owner.mkdir()
        (owner / "cake.png").write_bytes(png_bytes(1200, 900))
        fake, patch = http([])
        with patch, mock.patch.object(config, "OPENAI_API_KEY", "sk"):
            creator.run(self.space)
        draft, files = self.files(post)
        self.assertEqual(fake.calls, [])  # No AI image was paid for.
        self.assertEqual(files[0].suffix, ".jpg")
        self.assertFalse(draft["simulated"])

    def test_reel_runway_fish_groq_ffmpeg(self):
        from social_agent.agents import creator
        post = self.add("reel", ["instagram", "youtube"], REEL_SCRIPT)
        post = self.space.update_post(post["id"], format="video")  # instagram 9:16, youtube 16:9
        text = " ".join(s["say"] for s in REEL_SCRIPT["script"])
        mp4 = make_mp4(self.tmp / "src.mp4")
        mp3 = make_mp3(self.tmp / "src.mp3", 4.0)
        tasks = iter(range(100))
        fake, patch = http([
            ("POST", "api.fish.audio/v1/tts", mp3),
            ("POST", "/audio/transcriptions", words_for(text, 3.8)),
            ("POST", "/v1/text_to_video", lambda kw: {"id": f"t{next(tasks)}", "status": "PENDING"}),
            ("GET", "/v1/tasks/", lambda kw: {"status": "SUCCEEDED", "output": ["https://cdn.runway.test/c.mp4"]}),
            ("GET", "cdn.runway.test", mp4)])
        with patch, mock.patch.object(config, "RUNWAYML_API_SECRET", "key"), \
                mock.patch.object(config, "FISH_API_KEY", "fish"), mock.patch.object(config, "GROQ_API_KEY", "gsk"):
            result = creator.run(self.space)
        draft, files = self.files(post)
        self.assertFalse(draft["simulated"], result)
        self.assertEqual(sorted(f.name for f in files), ["v1_16x9_final.mp4", "v1_9x16_final.mp4"])
        tts = [c for c in fake.calls if c.url.endswith("/v1/tts")]
        self.assertEqual(len(tts), 1)  # One voiceover per post.
        self.assertEqual(tts[0].json["text"], text)
        runway = [c for c in fake.calls if c.url.endswith("/text_to_video")]
        self.assertEqual(sorted(c.json["ratio"] for c in runway), ["1280:720", "1280:720", "720:1280", "720:1280"])
        for f in files:
            self.assertAlmostEqual(media_edit.media_duration(f), 4.3, delta=0.3)  # Voice length + 0.5 s.
        srt = (self.space.root / draft["media"]["captions"]).read_text()
        self.assertIn("Our new chocolate cake", srt)
        self.assertEqual(draft.get("warnings", []), [])

    def test_reel_slideshow_from_ai_images_without_runway(self):
        from social_agent.agents import creator
        post = self.add("reel", ["instagram", "facebook"], REEL_SCRIPT)  # both 9:16
        img = base64.b64encode(png_bytes(1024, 1824)).decode()
        mp3 = make_mp3(self.tmp / "v.mp3", 4.0)
        text = " ".join(s["say"] for s in REEL_SCRIPT["script"])
        fake, patch = http([("POST", "/images/generations", {"data": [{"b64_json": img}]}),
                            ("POST", "api.fish.audio/v1/tts", mp3),
                            ("POST", "/audio/transcriptions", words_for(text, 3.8))])
        with patch, mock.patch.object(config, "OPENAI_API_KEY", "sk"), \
                mock.patch.object(config, "FISH_API_KEY", "fish"), mock.patch.object(config, "GROQ_API_KEY", "gsk"):
            creator.run(self.space)
        draft, files = self.files(post)
        self.assertEqual([f.name for f in files], ["v1_9x16_final.mp4"])
        self.assertFalse(draft["simulated"])
        self.assertEqual(len([c for c in fake.calls if "images" in c.url]), 2)  # One image per scene.

    def test_reel_without_keys_is_simulated_and_not_posted(self):
        from social_agent.agents import creator
        from social_agent.tools import platforms
        post = self.add("reel", ["instagram"], REEL_SCRIPT)
        creator.run(self.space)
        draft, files = self.files(post)
        self.assertTrue(draft["simulated"])
        self.assertTrue(files[0].name.endswith("_final.sim.mp4"))
        self.assertTrue(files[0].exists())  # A playable preview, with captions and scene timing.
        self.assertEqual(len(draft["scenes"]), 2)
        with mock.patch.object(config, "INSTAGRAM_ACCESS_TOKEN", "ig"), \
                mock.patch.object(config, "INSTAGRAM_USER_ID", "1"):
            with self.assertRaises(platforms.PublishError) as err:
                platforms.publish("instagram", self.space.get_post(post["id"]), self.space)
        self.assertTrue(err.exception.permanent)

    def test_runway_failure_falls_back_with_warning(self):
        from social_agent.agents import creator
        post = self.add("reel", ["tiktok"], REEL_SCRIPT)
        fake, patch = http([("POST", "/v1/text_to_video", lambda kw: {"_status": 500, "error": "down"})])
        with patch, mock.patch.object(config, "RUNWAYML_API_SECRET", "key"):
            creator.run(self.space)
        draft, files = self.files(post)
        self.assertTrue(draft["simulated"])
        self.assertTrue(any("HTTP 500" in w for w in draft["warnings"]))


class MachGenTest(CreatorTest):
    MODELS = {"models": [
        {"model": "Nano-Banana-2", "aliases": [], "kind": "image", "surfaces": [
            {"task_type": "T2I", "heights": [1024, 2048, 4096], "aspect_ratios": ["1:1", "9:16", "16:9", "4:5"]}]},
        {"model": "Vidu-Q3-Turbo", "aliases": [], "kind": "video", "supports_audio": True,
         "surfaces": [{"task_type": "T2V", "heights": [540, 720], "aspect_ratios": ["16:9", "9:16", "1:1", "4:3"],
                       "durations": list(range(1, 17))},
                      {"task_type": "I2V", "heights": [540, 720], "aspect_ratios": [],
                       "durations": list(range(1, 17))}]}]}

    def routes(self):
        """Fake MachGen API: models list, submit, upload, status. Each test adds its own assets route."""
        ids = iter(range(1000))
        return [("GET", "/api/v0/models", self.MODELS),
                ("POST", "/api/v0/generate", lambda kw: {"task_id": f"t-{next(ids)}", "status": "PENDING"}),
                ("POST", "/api/v0/upload", {"artifact_path": "u/abc.png"}),
                ("GET", "/api/v0/tasks/", {"status": "COMPLETED", "task_output": {}})]

    def test_machgen_photo_post(self):
        from PIL import Image
        from social_agent.agents import creator
        post = self.add("post", ["instagram", "tiktok"], POST_SCRIPT)
        routes = self.routes()
        routes.append(("GET", "/api/v0/assets/", png_bytes(1024, 1024)))
        fake, patch = http(routes)
        with patch, mock.patch.object(config, "MACHGEN_API_KEY", "MGA_k:s"), \
                mock.patch.object(config, "OPENAI_API_KEY", "sk-not-used"):
            creator.run(self.space)
        draft, files = self.files(post)
        self.assertFalse(draft["simulated"])
        self.assertEqual(sorted(Image.open(f).size for f in files), [(1080, 1080), (1080, 1920)])
        gens = [c for c in fake.calls if c.url.endswith("/api/v0/generate")]
        self.assertEqual(len(gens), 2)
        self.assertTrue(all(c.url.startswith("https://api.machgen.ai/") for c in fake.calls))
        self.assertEqual(gens[0].headers["Authorization"], "Bearer MGA_k:s")
        body = gens[0].json
        self.assertEqual((body["model"], body["task_type"]), ("Nano-Banana-2", "T2I"))
        self.assertIn(body["image_config"]["aspect_ratio"], ("1:1", "9:16"))
        self.assertEqual(body["image_config"]["height"], 1024)  # Only allowed heights are sent.
        self.assertNotIn("image_output", body)  # Nano-Banana-2 rejects output_format.
        self.assertFalse(any("openai" in c.url for c in fake.calls))

    def test_machgen_reel_text_to_video(self):
        from social_agent.agents import creator
        post = self.add("reel", ["instagram", "youtube"], REEL_SCRIPT)
        post = self.space.update_post(post["id"], format="video")  # 9:16 and 16:9
        mp4 = make_mp4(self.tmp / "src.mp4", 5)
        routes = self.routes()
        routes.append(("GET", "/api/v0/assets/", mp4))
        fake, patch = http(routes)
        with patch, mock.patch.object(config, "MACHGEN_API_KEY", "MGA_k:s"):
            creator.run(self.space)
        draft, files = self.files(post)
        self.assertEqual(sorted(f.name for f in files), ["v1_16x9_final.sim.mp4", "v1_9x16_final.sim.mp4"])
        self.assertTrue(draft["simulated"])  # No Fish Audio key: voice is simulated, so not postable.
        gens = [c.json for c in fake.calls if c.url.endswith("/api/v0/generate")]
        self.assertEqual(len(gens), 4)  # 2 scenes x 2 shapes
        self.assertEqual({g["task_type"] for g in gens}, {"T2V"})
        self.assertEqual(sorted(g["video_config"]["aspect_ratio"] for g in gens), ["16:9", "16:9", "9:16", "9:16"])
        self.assertTrue(all(g["video_config"]["height"] == 540 and 1 <= g["video_config"]["duration_secs"] <= 16
                            for g in gens))
        self.assertTrue(all(g["video_config"]["audio"] is False for g in gens))  # Our own voiceover only.
        self.assertEqual(draft.get("warnings", []), [])

    def test_machgen_image_to_video_mode(self):
        from social_agent.agents import creator
        post = self.add("reel", ["tiktok"], REEL_SCRIPT)
        mp4 = make_mp4(self.tmp / "src.mp4", 5)
        png = png_bytes(720, 1280)

        def asset(kw, n=iter(range(100))):
            return png if next(n) % 2 == 0 else mp4  # image, then its video, per scene
        routes = self.routes()
        routes.append(("GET", "/api/v0/assets/", asset))
        fake, patch = http(routes)
        with patch, mock.patch.object(config, "MACHGEN_API_KEY", "MGA_k:s"), \
                mock.patch.object(config, "VIDEO_MODE", "image"):
            creator.run(self.space)
        gens = [c.json for c in fake.calls if c.url.endswith("/api/v0/generate")]
        self.assertEqual([g["task_type"] for g in gens], ["T2I", "I2V", "T2I", "I2V"])
        self.assertEqual(gens[1]["src_image_urls"], ["@input/u/abc.png"])
        self.assertNotIn("aspect_ratio", gens[1]["video_config"])  # Vidu I2V takes it from the image.
        uploads = [c for c in fake.calls if c.url.endswith("/api/v0/upload")]
        self.assertEqual(uploads[0].headers["X-MachGen-Upload-Model"], "Vidu-Q3-Turbo")

    def test_machgen_failed_task_gives_warning(self):
        from social_agent.agents import creator
        post = self.add("post", ["instagram"], POST_SCRIPT)
        fake, patch = http([("GET", "/api/v0/models", self.MODELS),
                            ("POST", "/api/v0/generate", {"task_id": "t-1", "status": "PENDING"}),
                            ("GET", "/api/v0/tasks/t-1", {"task_id": "t-1", "status": "FAILED",
                                                           "error_msg": "Your task failed because of policy"})])
        with patch, mock.patch.object(config, "MACHGEN_API_KEY", "MGA_k:s"):
            creator.run(self.space)
        draft, files = self.files(post)
        self.assertTrue(draft["simulated"])
        self.assertIn("policy", draft["warnings"][0])

    def test_machgen_retries_on_429(self):
        post_calls = iter([{"_status": 429, "detail": "Too many submissions"}, {"task_id": "t-9"}])
        fake, patch = http([("GET", "/api/v0/models", self.MODELS),
                            ("POST", "/api/v0/generate", lambda kw: dict(next(post_calls))),
                            ("GET", "/api/v0/tasks/t-9", {"status": "COMPLETED"}),
                            ("GET", "/api/v0/assets/t-9", png_bytes(64, 64))])
        with patch, mock.patch.object(config, "MACHGEN_API_KEY", "MGA_k:s"):
            out = machgen.generate_image("cake", "1:1", self.tmp / "img")
        self.assertEqual(out.suffix, ".png")


class TimingTest(Base):
    def test_timeline_from_alignment_and_captions(self):
        scenes = [{"voiceover": "One two three."}, {"voiceover": "Four five six seven eight nine ten."}]
        text = " ".join(s["voiceover"] for s in scenes)
        tl = media_edit.timeline(scenes, alignment_for(text, 5.0), 5.0)
        self.assertEqual(tl[0]["start"], 0.0)
        self.assertAlmostEqual(tl[1]["start"], 15 / len(text) * 5.0, places=2)  # "Four" starts at char 15.
        self.assertAlmostEqual(tl[1]["end"], 5.5)
        cues = media_edit.caption_cues(tl)
        self.assertEqual([c[2] for c in cues], ["One two three.", "Four five six seven eight", "nine ten."])

    def test_estimated_timeline_without_voice(self):
        tl = media_edit.timeline([{"voiceover": "Hi there", "seconds": 3}, {"voiceover": "Bye", "seconds": 2}])
        self.assertEqual([(t["start"], t["end"]) for t in tl], [(0.0, 3.0), (3.0, 5.0)])

    def test_placeholder_names(self):
        self.assertTrue(ai_models.is_placeholder("a/v1_9x16_final.sim.mp4"))
        self.assertFalse(ai_models.is_placeholder("a/v1_9x16_final.mp4"))
