"""AI models for the Creator: image, video, voice (plan "Tech choices": a provider layer).

Text is written by the agent that runs the steps. The others switch on by key:
  image  -> MachGen (Nano-Banana-2)                   when MACHGEN_API_KEY is set   (default, see machgen.py)
            else OpenAI GPT Image (gpt-image-2)       when OPENAI_API_KEY is set
  video  -> MachGen (Vidu-Q3-Turbo, 540p)             when MACHGEN_API_KEY is set   (default, see machgen.py)
            else Runway (gen4.5)                      when RUNWAYML_API_SECRET is set
            Image to video by default (VIDEO_MODE=image: an AI image of the scene, then animated),
            or text to video with VIDEO_MODE=text.
            AI_VIDEO_ENABLED=0 turns AI video off. IMAGE_PROVIDER / VIDEO_PROVIDER pick one service by hand.
  voice  -> Fish Audio (s2.1-pro-free, no cost)       when FISH_API_KEY is set; the whole script in one take
            word times from Whisper on Groq           when GROQ_API_KEY is set (else spread over the voice length)
            (the times set the captions and the scene cuts)
Editing, captions and sizes per platform are in media_edit.py (ffmpeg + Pillow, no key).
With no key, each one writes a clearly marked placeholder file, so the flow still runs.
To swap a provider, change only the matching function in this file.
Docs checked 2026-11-25:
  https://developers.openai.com/api/reference/resources/images
  https://docs.dev.runwayml.com/api/
  https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech
  https://console.groq.com/docs/speech-to-text
"""
import base64
import difflib
import json
import re
import subprocess
import time
from pathlib import Path
from xml.sax.saxutils import escape

from .. import config
from . import machgen
from .http import ServiceError, request
from .media_edit import PIXELS, card_image, ffmpeg_exe, media_duration  # noqa: F401 - ffmpeg_exe kept for old imports

OPENAI = "https://api.openai.com/v1"
RUNWAY = "https://api.dev.runwayml.com/v1"
FISH_TTS = "https://api.fish.audio/v1/tts"
GROQ_WHISPER = "https://api.groq.com/openai/v1/audio/transcriptions"

# Size for each platform and format (plan: "sizes each file per platform").
PLATFORM_SIZES = {
    "instagram": {"post": "1:1", "carousel": "1:1", "reel": "9:16", "video": "9:16"},
    "facebook": {"post": "1:1", "carousel": "1:1", "reel": "9:16", "video": "16:9"},
    "linkedin": {"post": "1:1", "carousel": "1:1", "reel": "9:16", "video": "16:9"},
    "x": {"post": "16:9", "carousel": "16:9", "reel": "9:16", "video": "16:9"},
    "tiktok": {"post": "9:16", "carousel": "9:16", "reel": "9:16", "video": "9:16"},
    "youtube": {"post": "16:9", "carousel": "16:9", "reel": "9:16", "video": "16:9"},
}
# gpt-image-2 and newer take any size (sides divisible by 16). Older GPT Image models take 3 sizes.
OPENAI_IMAGE_SIZES = {"1:1": "1024x1024", "9:16": "1024x1824", "16:9": "1824x1024", "4:5": "1024x1280"}
OPENAI_IMAGE_SIZES_OLD = {"1:1": "1024x1024", "9:16": "1024x1536", "16:9": "1536x1024", "4:5": "1024x1536"}
# Runway gen4.5 makes 9:16 or 16:9. 1:1 videos are cut from the 9:16 clips in the edit.
RUNWAY_RATIOS = {"9:16": "720:1280", "16:9": "1280:720", "1:1": "720:1280", "4:5": "720:1280"}
# Simulated files. The Publisher never posts these.
PLACEHOLDER_SUFFIXES = (".svg", ".clip.json", ".voiceover.txt", ".edit.json", ".sim.mp4", ".sim.png", ".sim.jpg")


def size_for(platform: str, fmt: str) -> str:
    return PLATFORM_SIZES.get(platform, {}).get(fmt, "1:1")


def clip_ratio(aspect: str) -> str:
    """The aspect the video clips are made in. 1:1 and 4:5 reuse the 9:16 clips (saves money)."""
    return "16:9" if aspect == "16:9" else "9:16"


def is_placeholder(path: str | Path) -> bool:
    return str(path).endswith(PLACEHOLDER_SUFFIXES)


def _openai_headers() -> dict:
    return {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}


# ---------------- image ----------------
def image_on() -> bool:
    return bool(config._image_provider())


def video_on() -> bool:
    return bool(config._video_provider())


def runway_on() -> bool:  # Old name, kept for compatibility.
    return video_on()


def generate_image(prompt: str, aspect: str, out_path: Path) -> Path:
    """Make one image (MachGen, else OpenAI). Returns the raw image (the Creator then sizes it per platform)."""
    provider = config._image_provider()
    if provider == "MachGen":
        return machgen.generate_image(prompt, aspect, out_path)
    if provider != "OpenAI":
        return _placeholder_image(prompt, aspect, out_path)
    model = config.OPENAI_IMAGE_MODEL
    sizes = OPENAI_IMAGE_SIZES_OLD if model.startswith(("gpt-image-1", "dall-e")) else OPENAI_IMAGE_SIZES
    resp = request("POST", f"{OPENAI}/images/generations", service="OpenAI image",
                   headers=_openai_headers(),
                   json={"model": model, "prompt": prompt[:4000], "n": 1,
                         "size": sizes.get(aspect, "1024x1024"), "quality": config.OPENAI_IMAGE_QUALITY,
                         "output_format": "png"})
    item = resp.json()["data"][0]
    if item.get("b64_json"):  # GPT Image models always return base64.
        data = base64.b64decode(item["b64_json"])
    else:
        data = request("GET", item["url"], service="OpenAI image download").content
    out_path = out_path.with_suffix(".png")
    out_path.write_bytes(data)
    return out_path


def _placeholder_image(prompt: str, aspect: str, out_path: Path) -> Path:
    w, h = PIXELS.get(aspect, (1080, 1080))
    lines, line = [], ""
    for word in prompt.split():
        if len(line) + len(word) > 40:
            lines.append(line)
            line = ""
        line += word + " "
    lines.append(line)
    tspans = "".join(
        f'<tspan x="50%" dy="{0 if i == 0 else 44}">{escape(t.strip())}</tspan>' for i, t in enumerate(lines[:12]))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">'
           f'<rect width="100%" height="100%" fill="#f6e7d8"/>'
           f'<text x="50%" y="12%" font-size="40" text-anchor="middle" font-family="sans-serif">'
           f'AI image placeholder ({aspect}) - add MACHGEN_API_KEY</text>'
           f'<text x="50%" y="30%" font-size="32" text-anchor="middle" font-family="sans-serif">{tspans}</text>'
           f"</svg>")
    out_path = out_path.with_suffix(".svg")
    out_path.write_text(svg, encoding="utf-8")
    return out_path


# ---------------- video clip ----------------
def generate_video_clip(scene: dict, aspect: str, out_path: Path, image: Path | None = None) -> Path:
    """Make one scene clip (MachGen, else Runway). With `image`, the model animates that image."""
    provider = config._video_provider()
    if provider == "MachGen":
        return machgen.generate_video_clip(scene, aspect, out_path, image)
    if provider != "Runway":
        out_path = out_path.with_suffix(".clip.json")
        out_path.write_text(json.dumps({"aspect": aspect, **scene}, indent=2), encoding="utf-8")
        return out_path
    headers = {"Authorization": f"Bearer {config.RUNWAYML_API_SECRET}", "X-Runway-Version": "2024-11-06"}
    seconds = int(min(max(round(scene.get("seconds") or 4), 2), 10))  # gen4.5: 2 to 10 seconds.
    body = {"model": config.RUNWAY_VIDEO_MODEL, "promptText": scene["visual"][:1000],
            "ratio": RUNWAY_RATIOS.get(aspect, "720:1280"), "duration": seconds}
    endpoint = "text_to_video"
    if image is not None and image.suffix in (".png", ".jpg", ".jpeg", ".webp"):
        endpoint = "image_to_video"
        body["promptImage"] = _data_uri(image)
    task = request("POST", f"{RUNWAY}/{endpoint}", service="Runway video", headers=headers, json=body).json()
    deadline = time.time() + config.VIDEO_TIMEOUT_SECONDS
    while task.get("status") not in ("SUCCEEDED", "FAILED", "CANCELLED"):
        if time.time() > deadline:
            raise ServiceError(f"Runway video: timed out (task {task.get('id')})")
        time.sleep(10)  # Runway asks for 5 seconds or more between polls.
        task = request("GET", f"{RUNWAY}/tasks/{task['id']}", service="Runway video", headers=headers).json()
    if task["status"] != "SUCCEEDED":
        raise ServiceError(f"Runway video failed: {task.get('failure') or task['status']}")
    data = request("GET", task["output"][0], service="Runway video download").content  # URLs expire in 24-48 h.
    out_path = out_path.with_suffix(".mp4")
    out_path.write_bytes(data)
    return out_path


def _data_uri(image: Path) -> str:
    """Runway takes a data URI up to 5 MB. Big images are made smaller first (JPEG)."""
    data, mime = image.read_bytes(), "image/png" if image.suffix == ".png" else "image/jpeg"
    if len(data) > 3_000_000:
        from io import BytesIO

        from PIL import Image
        with Image.open(image) as img:
            img = img.convert("RGB")
            img.thumbnail((1600, 1600))
            buf = BytesIO()
            img.save(buf, "JPEG", quality=88)
        data, mime = buf.getvalue(), "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


# ---------------- voice ----------------
def generate_voiceover(text: str, out_path: Path) -> Path:
    """Voiceover only (no timestamps). Kept for simple use; the Creator uses voiceover_with_timing."""
    return voiceover_with_timing(text, out_path)[0]


def voiceover_with_timing(text: str, out_path: Path) -> tuple[Path, dict | None]:
    """Fish Audio reads the whole script in one take. Whisper on Groq times each word.
    Returns (mp3 file, alignment: times for each character of OUR text) or a placeholder."""
    if not config.FISH_API_KEY:
        out_path = out_path.with_suffix(".voiceover.txt")
        out_path.write_text(text, encoding="utf-8")
        return out_path, None
    resp = request("POST", FISH_TTS, service="Fish Audio", timeout=300,
                   headers={"Authorization": f"Bearer {config.FISH_API_KEY}", "model": config.FISH_MODEL},
                   json={"text": text, "reference_id": config.FISH_VOICE_ID, "format": "mp3", "mp3_bitrate": 192,
                         "prosody": {"speed": config.FISH_SPEED}})
    out_path = out_path.with_suffix(".mp3")
    out_path.write_bytes(resp.content)
    heard = heard_words(out_path) if config.GROQ_API_KEY else []
    alignment = _alignment(text, heard, media_duration(out_path))
    out_path.with_suffix(".timing.json").write_text(json.dumps(alignment), encoding="utf-8")
    return out_path, alignment


def heard_words(voice: Path) -> list[dict]:
    """Every spoken word with its start and end (seconds), from Whisper on Groq."""
    resp = request("POST", GROQ_WHISPER, service="Groq Whisper", timeout=300,
                   headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                   data={"model": config.GROQ_WHISPER_MODEL, "temperature": "0", "response_format": "verbose_json",
                         "timestamp_granularities[]": "word"},
                   files={"file": (voice.name, voice.read_bytes(), "audio/mpeg")})
    return [{"w": w["word"].strip(), "s": w["start"], "e": w["end"]} for w in resp.json().get("words", [])]


def realign(alignment: dict, text: str, total: float) -> dict:
    """Times of the spoken text (with "say" spellings) moved onto the caption text, word by word."""
    chars, starts, ends = (alignment["characters"], alignment["character_start_times_seconds"],
                           alignment["character_end_times_seconds"])
    heard, word, s0 = [], "", 0.0
    for i, ch in enumerate(chars + [" "]):
        if ch.isspace():
            if word:
                heard.append({"w": word, "s": s0, "e": ends[i - 1]})
            word = ""
        else:
            if not word:
                s0 = starts[i]
            word += ch
    spoken, shown = len(heard), text.split()
    if spoken == len(shown):  # Same word count (the rule for "say"): word i of the caption = word i spoken.
        heard = [dict(h, w=w) for h, w in zip(heard, shown)]
    return _alignment(text, heard, total)


def verify_words(media: Path, script: str) -> dict:
    """The self-check's voice check: Whisper listens to the final video; every script word must be heard.
    Returns {"ok": bool, "missing": [...], "changed": [...]} (spelling-only differences do not count)."""
    audio = media.with_suffix(".check.mp3")
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000",
                    "-b:a", "48k", str(audio)], check=False, capture_output=True)
    if not audio.exists():
        raise ServiceError("Could not read the video's sound for the voice check.")
    heard = [_norm(h["w"]) for h in heard_words(audio)]
    audio.unlink(missing_ok=True)
    words = [_norm(w) for w in script.split()]
    missing, changed = [], []
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, words, heard, autojunk=False).get_opcodes():
        if op == "delete":
            missing += words[a1:a2]
        elif op == "replace":
            changed.append({"script": " ".join(words[a1:a2]), "heard": " ".join(heard[b1:b2])})
    return {"ok": not missing and not changed, "missing": missing, "changed": changed}


def _norm(word: str) -> str:
    return re.sub(r"\W", "", word.lower())


def _alignment(text: str, heard: list[dict], total: float) -> dict:
    """Times for each character of `text`. Script words matched to heard words take their times;
    the rest (or all, with no word timings) are spread over the gaps by length."""
    words = text.split()
    times: list[tuple[float, float] | None] = [None] * len(words)
    matcher = difflib.SequenceMatcher(None, [_norm(w) for w in words], [_norm(h["w"]) for h in heard], autojunk=False)
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            h = heard[block.b + k]
            times[block.a + k] = (h["s"], h["e"])
    i = 0
    while i < len(words):
        if times[i]:
            i += 1
            continue
        j = i
        while j < len(words) and not times[j]:
            j += 1
        lo = times[i - 1][1] if i else 0.0
        hi = max(times[j][0] if j < len(words) else total, lo)
        size = sum(len(w) for w in words[i:j])
        t = lo
        for k in range(i, j):
            step = (hi - lo) * len(words[k]) / size
            times[k] = (t, t + step)
            t += step
        i = j
    chars, starts, ends = [], [], []
    for n, (word, (s, e)) in enumerate(zip(words, times)):
        if n:
            chars.append(" ")
            starts.append(ends[-1])
            ends.append(s)
        step = (e - s) / len(word)
        for c, ch in enumerate(word):
            chars.append(ch)
            starts.append(s + c * step)
            ends.append(s + (c + 1) * step)
    return {"characters": chars, "character_start_times_seconds": starts, "character_end_times_seconds": ends}


# ---------------- simulated clip ----------------
def simulated_clip(scene: dict, aspect: str, seconds: float, out_path: Path) -> Path:
    """A real (playable) but simulated clip: a text card. Marked .sim.mp4, so it is never posted."""
    from .media_edit import still_clip

    card = card_image(scene.get("visual", ""), aspect, out_path.with_suffix(".sim"),
                      label="SIMULATED SCENE - add MACHGEN_API_KEY")
    return still_clip(card, seconds, aspect, Path(str(out_path) + ".sim.mp4"), zoom=False)
