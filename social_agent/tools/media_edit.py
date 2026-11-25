"""Media editing for the Creator: sizes per platform, captions, video edit. No key needed.

  Images  -> Pillow: crop and resize to the exact platform size (JPEG).
  Video   -> ffmpeg (bundled by imageio-ffmpeg): trim or hold each clip to its voiceover line,
             size it for the platform, join, burn in captions, add the voiceover.
  Captions-> .srt file, timed by the voiceover (word times from Groq Whisper) or by an estimate.
"""
import re
import subprocess
from pathlib import Path

from .. import config
from .http import ServiceError

# Exact pixel sizes per aspect (plan: "sizes each file per platform, 9:16, 1:1, 16:9").
PIXELS = {"1:1": (1080, 1080), "9:16": (1080, 1920), "16:9": (1920, 1080), "4:5": (1080, 1350)}
WORDS_PER_SECOND = 2.6  # Speech speed estimate, used when there is no real voiceover.
FPS = 30


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - fall back to ffmpeg on PATH
        return "ffmpeg"


def run_ffmpeg(args: list[str], cwd: Path | None = None, what: str = "ffmpeg") -> None:
    result = subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", *args], capture_output=True,
                            text=True, cwd=cwd)
    if result.returncode != 0:
        raise ServiceError(f"{what} failed: {result.stderr[-400:]}")


def media_duration(path: Path) -> float:
    """Length of an audio or video file in seconds (0.0 if unknown)."""
    result = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


# ---------------- images ----------------
def fit_image(src: Path, aspect: str, out_path: Path) -> Path:
    """Center-crop and resize an image to the platform size. Saves a JPEG."""
    from PIL import Image, ImageOps

    w, h = PIXELS.get(aspect, (1080, 1080))
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img = ImageOps.fit(img, (w, h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.45))
        out_path = out_path.with_suffix(".jpg")
        img.save(out_path, "JPEG", quality=92, optimize=True)
    return out_path


def card_image(text: str, aspect: str, out_path: Path, label: str = "SIMULATED") -> Path:
    """A plain card with text. Used for simulated video scenes (no AI video or image key)."""
    from PIL import Image, ImageDraw, ImageFont

    w, h = PIXELS.get(aspect, (1080, 1920))
    img = Image.new("RGB", (w, h), (246, 231, 216))
    draw = ImageDraw.Draw(img)
    try:
        big, small = ImageFont.load_default(size=w // 18), ImageFont.load_default(size=w // 30)
    except TypeError:  # Old Pillow
        big = small = ImageFont.load_default()
    draw.text((w // 2, h // 8), label, fill=(150, 60, 40), font=small, anchor="mm")
    lines, line = [], ""
    for word in text.split():
        if len(line) + len(word) > 22:
            lines.append(line.strip())
            line = ""
        line += word + " "
    lines.append(line.strip())
    y = h // 2 - len(lines[:10]) * (w // 30)
    for t in lines[:10]:
        draw.text((w // 2, y), t, fill=(60, 40, 30), font=big, anchor="mm")
        y += w // 14
    out_path = out_path.with_suffix(".png")
    img.save(out_path)
    return out_path


def still_clip(image: Path, seconds: float, aspect: str, out_path: Path, zoom: bool = True) -> Path:
    """Turn one image into a video clip with a slow zoom (Ken Burns)."""
    w, h = PIXELS.get(aspect, (1080, 1920))
    frames = max(1, int(round(seconds * FPS)))
    if zoom:  # One input frame -> zoompan makes all the frames (no looping, low memory).
        sw, sh = w * 3 // 2 // 2 * 2, h * 3 // 2 // 2 * 2
        vf = (f"scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
              f"zoompan=z='min(1+0.0007*on,1.12)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s={w}x{h}:fps={FPS},"
              "setsar=1,format=yuv420p")
        inputs = ["-i", str(image)]
    else:
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=yuv420p"
        inputs = ["-loop", "1", "-framerate", str(FPS), "-i", str(image)]
    run_ffmpeg([*inputs, "-t", f"{seconds:.2f}", "-vf", vf, "-r", str(FPS), "-c:v", "libx264",
                "-preset", "veryfast", "-threads", "2", str(out_path)], what="ffmpeg still clip")
    return out_path


# ---------------- timing and captions ----------------
def timeline(scenes: list[dict], alignment: dict | None = None, voice_length: float = 0.0) -> list[dict]:
    """Start, end and timed words for each scene.

    With an alignment (ai_models.voiceover_with_timing), times come from the real voice. Else they are estimated.
    The voiceover text is the scene lines joined with one space (same as the Creator sends to TTS).
    """
    if alignment and alignment.get("characters"):
        return _timeline_from_alignment(scenes, alignment, voice_length)
    out, t = [], 0.0
    for s in scenes:
        words = s.get("voiceover", "").split()
        length = max(float(s.get("seconds") or 4), len(words) / WORDS_PER_SECOND + 0.4, 1.5)
        step = (length - 0.4) / max(len(words), 1)
        timed = [(wd, t + i * step, t + (i + 1) * step) for i, wd in enumerate(words)]
        out.append({"start": t, "end": t + length, "words": timed})
        t += length
    return out


def _timeline_from_alignment(scenes: list[dict], al: dict, voice_length: float) -> list[dict]:
    chars, starts, ends = al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]
    # Words with times, and the character index where each word starts.
    words, cur, c_start, c_idx = [], "", 0.0, 0
    for i, ch in enumerate(chars):
        if ch.isspace():
            if cur:
                words.append((cur, c_start, ends[i - 1], c_idx))
            cur = ""
            continue
        if not cur:
            c_start, c_idx = starts[i], i
        cur += ch
    if cur:
        words.append((cur, c_start, ends[len(chars) - 1], c_idx))
    # Scene boundaries by character position.
    bounds, pos = [], 0
    for s in scenes:
        bounds.append(pos)
        pos += len(s.get("voiceover", "")) + 1
    total = max(voice_length, words[-1][2] if words else 0.0)
    out = []
    for n, b in enumerate(bounds):
        nxt = bounds[n + 1] if n + 1 < len(bounds) else len(chars) + 1
        mine = [(w, s, e) for (w, s, e, ci) in words if b <= ci < nxt]
        out.append({"words": mine})
    # Scene n starts when its first word starts (scene 0 at 0); it ends when the next scene starts.
    for n, sc in enumerate(out):
        prev = out[n - 1]["start"] if n else 0.0
        sc["start"] = 0.0 if n == 0 else max(sc["words"][0][1] if sc["words"] else prev + 0.5, prev + 0.1)
    for n, sc in enumerate(out):
        sc["end"] = out[n + 1]["start"] if n + 1 < len(out) else max(total + 0.5, sc["start"] + 1.0)
    return out


def caption_cues(tl: list[dict], max_words: int = 5, max_chars: int = 28) -> list[tuple[float, float, str]]:
    """Short caption lines (a few words each), timed to the voice. Never cross a scene cut."""
    cues = []
    for sc in tl:
        chunk: list = []
        for word in sc["words"]:
            text = " ".join(w for w, _, _ in chunk + [word])
            if chunk and (len(chunk) >= max_words or len(text) > max_chars):
                cues.append((chunk[0][1], chunk[-1][2], " ".join(w for w, _, _ in chunk)))
                chunk = []
            chunk.append(word)
        if chunk:
            if len(chunk) == 1 and cues and len(sc["words"]) > 1 and cues[-1][1] >= sc["words"][0][1]:
                # Do not leave one word alone: join it to the line before (same scene).
                s0, _, t0 = cues.pop()
                cues.append((s0, chunk[-1][2], f"{t0} {chunk[0][0]}"))
            else:
                cues.append((chunk[0][1], chunk[-1][2], " ".join(w for w, _, _ in chunk)))
    # Close small gaps, so captions do not flicker.
    fixed = []
    for i, (s, e, t) in enumerate(cues):
        if i + 1 < len(cues) and cues[i + 1][0] - e < 0.35:
            e = cues[i + 1][0]
        fixed.append((s, max(e, s + 0.5), t))
    return fixed


def write_srt(cues: list[tuple[float, float, str]], path: Path) -> Path:
    def ts(sec: float) -> str:
        ms = int(round(sec * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    body = "".join(f"{i}\n{ts(s)} --> {ts(e)}\n{t}\n\n" for i, (s, e, t) in enumerate(cues, 1))
    path.write_text(body, encoding="utf-8")
    return path


# ---------------- video edit ----------------
# Caption look: white bold text with a dark outline, low in the frame (above the app buttons on 9:16).
CAPTION_LOOK = {"9:16": (0.062, 0.24), "1:1": (0.058, 0.10), "16:9": (0.070, 0.08), "4:5": (0.058, 0.12)}
FONT_FILES = ["DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "Arial-BoldMT.ttf", "LiberationSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"]


def _font(size: int):
    """A bold system font if there is one, else Pillow's built-in font. Works on every OS."""
    from PIL import ImageFont

    if config.CAPTION_FONT:
        try:
            return ImageFont.truetype(config.CAPTION_FONT, size)
        except OSError:
            pass
    for name in FONT_FILES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Old Pillow
        return ImageFont.load_default()


def _band(aspect: str) -> tuple[int, int, int]:
    """Caption band: width, height, and y position in the frame."""
    w, h = PIXELS.get(aspect, (1080, 1920))
    size_k, bottom_k = CAPTION_LOOK.get(aspect, CAPTION_LOOK["9:16"])
    font_px = int(min(w, h) * size_k * (1.0 if aspect == "16:9" else 1.12))
    band_h = int(font_px * 1.25) * 2 + font_px // 2  # room for 2 lines
    return w, band_h, int(h * (1 - bottom_k)) - band_h


def caption_image(text: str, aspect: str, out_path: Path) -> Path:
    """One caption as a transparent PNG band (full width). Empty text = a clear band (gaps)."""
    from PIL import Image, ImageDraw

    w, h = PIXELS.get(aspect, (1080, 1920))
    size_k, _ = CAPTION_LOOK.get(aspect, CAPTION_LOOK["9:16"])
    font = _font(int(min(w, h) * size_k * (1.0 if aspect == "16:9" else 1.12)))
    bw, bh, _ = _band(aspect)
    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Wrap to at most 2 lines that fit 86% of the width.
    lines, line = [], ""
    for word in text.split():
        test = f"{line} {word}".strip()
        if line and draw.textlength(test, font=font) > w * 0.86:
            lines.append(line)
            line = word
        else:
            line = test
    lines.append(line)
    if len(lines) == 2:  # Balance 2 lines, so one word is not left alone.
        words = text.split()
        best = min(range(1, len(words)), key=lambda k: max(draw.textlength(" ".join(words[:k]), font=font),
                                                          draw.textlength(" ".join(words[k:]), font=font)))
        lines = [" ".join(words[:best]), " ".join(words[best:])]
    size = getattr(font, "size", 12)
    stroke, line_h = max(2, int(size * 0.06)), int(size * 1.25)
    y = bh - line_h * len(lines[:2]) - size // 4
    for t in lines[:2]:
        draw.text((bw // 2, y), t, font=font, fill=(255, 255, 255, 255), anchor="ma",
                  stroke_width=stroke, stroke_fill=(0, 0, 0, 230))
        y += line_h
    img.save(out_path)
    return out_path


def caption_track(cues: list, aspect: str, total: float, folder: Path, name: str) -> Path:
    """All captions as ONE image track (ffmpeg concat list): caption bands and clear bands for the gaps."""
    folder.mkdir(exist_ok=True)
    blank = caption_image("", aspect, folder / f"{name}_blank.png")
    rows, t = [], 0.0
    for j, (start, end, text) in enumerate(cues):
        start, end = max(start, t), min(end, total)
        if end <= start:
            continue
        if start > t:
            rows += [f"file '{blank.name}'", f"duration {start - t:.3f}"]
        png = caption_image(text, aspect, folder / f"{name}_cap{j + 1}.png")
        rows += [f"file '{png.name}'", f"duration {end - start:.3f}"]
        t = end
    rows += [f"file '{blank.name}'", f"duration {max(total - t, 0.04):.3f}", f"file '{blank.name}'"]
    listing = folder / f"{name}_captions.txt"
    listing.write_text("ffconcat version 1.0\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return listing


def read_srt(path: Path) -> list[tuple[float, float, str]]:
    def sec(ts: str) -> float:
        hh, mm, rest = ts.strip().split(":")
        s, ms = rest.split(",")
        return int(hh) * 3600 + int(mm) * 60 + int(s) + int(ms) / 1000

    cues = []
    for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
        rows = block.strip().splitlines()
        if len(rows) >= 3 and "-->" in rows[1]:
            start, end = rows[1].split("-->")
            cues.append((sec(start), sec(end), " ".join(rows[2:])))
    return cues


def edit_video(clips: list[Path], durations: list[float], voiceover: Path | None, srt: Path | None,
               aspect: str, out_path: Path, warnings: list | None = None) -> Path:
    """Join the scene clips into one video for one platform size.

    Each clip is trimmed (or its last frame held) to its scene length, so pictures match the voice.
    Captions from `srt` are drawn with Pillow and laid on top (no font setup needed).
    The voiceover (mp3) is the sound track.
    If burning captions fails, the video is made without them and a warning is added.
    """
    if config.BURN_IN_CAPTIONS and srt is not None and srt.exists():
        try:
            return _edit(clips, durations, voiceover, srt, aspect, out_path)
        except ServiceError as exc:
            if warnings is not None:
                warnings.append(f"Captions not burned in ({str(exc)[:160]}). The .srt file is kept.")
    return _edit(clips, durations, voiceover, None, aspect, out_path)


def _edit(clips, durations, voiceover, srt, aspect, out_path: Path) -> Path:
    w, h = PIXELS.get(aspect, (1080, 1920))
    total = sum(durations)
    args: list[str] = []
    for c in clips:
        args += ["-i", str(c.resolve())]
    n = len(clips)
    has_voice = voiceover is not None and voiceover.suffix == ".mp3" and voiceover.exists()
    if has_voice:
        args += ["-i", str(voiceover.resolve())]
    else:  # Silent sound track: some platforms reject videos with no audio.
        args += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=44100:cl=stereo"]
    parts = "".join(
        f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={FPS},"
        f"tpad=stop_mode=clone:stop_duration={d:.2f},trim=duration={d:.2f},setpts=PTS-STARTPTS[v{i}];"
        for i, d in enumerate(durations))
    graph = parts + "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[joined];"
    cues = read_srt(srt) if srt is not None else []
    if cues:
        listing = caption_track(cues, aspect, total, out_path.parent / "caption_frames", out_path.name.split(".")[0])
        args += ["-f", "concat", "-safe", "0", "-i", str(listing.resolve())]
        _, _, band_y = _band(aspect)
        graph += (f"[{n + 1}:v]fps={FPS},format=rgba[caps];"
                  f"[joined][caps]overlay=0:{band_y}:eof_action=pass:format=auto,format=yuv420p[outv];")
    else:
        graph += "[joined]format=yuv420p[outv];"
    graph += f"[{n}:a]apad,atrim=duration={total:.2f}[outa]"
    out_path = out_path.with_suffix(".mp4")
    args += ["-filter_complex", graph, "-map", "[outv]", "-map", "[outa]", "-t", f"{total:.2f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-threads", "2", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_path.name]
    run_ffmpeg(args, cwd=out_path.parent, what="ffmpeg edit")
    return out_path
