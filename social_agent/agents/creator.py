"""3. Creator - makes text, images, videos.

Steps (from the plan):
  1. Gets the script for each post.
  2. Text: writes the final copy in the brand voice.
  3. Image: writes an image prompt, then makes the image with an AI image model.
  4. Video: splits the script into scenes, makes clips with an AI video model,
     adds voiceover, then edits.
  5. Adds captions and sizes each file per platform (9:16, 1:1, 16:9).
  6. Saves all files to the media library.
  7. Self-check: the agent opens every image, frame sheet and the voice check (status "checking"),
     fixes what fails, then mark_checked sends the draft for owner approval (status "pending_approval").
Gets: script from the Planner; media from AI models.
Gives: draft to Owner approval; files to the media library.

Outside services (each switches on when its key is in .env; see tools/ai_models.py):
  images and video clips: MachGen (Nano-Banana-2, Vidu-Q3-Turbo; else OpenAI images, Runway video)
  voiceover: Fish Audio, word times: Whisper on Groq
  edit, captions and sizes per platform: ffmpeg + Pillow (no key).
The owner can also give own photos or clips: media_library/<post id>/owner/ (used before AI).
The agent that runs this step (for example Grok Bot) writes the text (step 2). Called without it, run() answers
"needs_text" with the rules, the scripts and the shape; the agent writes each draft and calls it again with
`drafts` ({post id: draft}). MOCK (demo and tests) uses fixed sample text and skips the self-check.
The rules for the text and the self-check are in rules/CREATIVE_RULES.md.
Images, clips and voice are kept in media_library/<post id>/cache/ by their prompt: a remake pays only for the
items whose prompt changed.
Media library per post: script.json, draft_vN.json, raw images, clips, voiceover, captions .srt,
and the final files named v<N>_<size>_... (for example v1_9x16_final.mp4, v1_1x1_image1.jpg).
"""
import hashlib
import json
import math
from pathlib import Path

from .. import config
from ..business_space import BusinessSpace
from ..tools import ai_models, media, media_edit
from ..tools.http import ServiceError
from . import planner

SYSTEM = """You are the Creator in a social media team for one business.
Turn the Planner's script into a ready draft. Follow the creative rules below (rules/CREATIVE_RULES.md) exactly.
- Write in the brand voice from the brand brief. Never use banned topics.
- final_copy: the post text. caption: short on-screen or first-line caption.
- post / carousel: write image_prompts (1 for a post, 3-5 for a carousel). Each one is a STRONG, detailed prompt
  (rules section 1): subject with real details, action, setting, light, camera, mood and style, textures.
- reel / video: the script has shots in order (show, say, seconds). Make one scene per shot (split a shot with two
  actions or two ideas). Each scene: visual (the first frame, a strong prompt), motion (only what changes, with
  strong verbs), voiceover (the spoken line), say (optional: the same line spelled for the voice), seconds.
  Start with the hook: the most eye-catching shot.
- If the owner asked for changes, follow the notes exactly and change nothing else.
- On a fix after the self-check, keep every prompt that passed WORD FOR WORD (it is reused at no cost) and
  rewrite only the failed ones."""

RULES_FILE = config.PROJECT_ROOT / "rules" / "CREATIVE_RULES.md"

# Added to every prompt in code, for every business (see rules/CREATIVE_RULES.md).
IMAGE_RULE = ("Photorealistic, true real-world scale and physics, natural faces and exactly two normal hands per "
              "person, every person looks different, nothing floating. No text, no letters, no logos, no watermark")
MOTION_RULE = ("Real-world physics. People are already in the middle of their action and keep moving for the whole "
               "clip, facing their task, never looking at the camera. Every change has a visible cause first. The "
               "action only moves forward: nothing reverses, appears or moves by itself. Solid things never pass "
               "through each other")


def rules_text() -> str:
    return SYSTEM + "\n\n" + (RULES_FILE.read_text(encoding="utf-8") if RULES_FILE.exists() else "")

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXT = (".mp4", ".mov", ".m4v")

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "final_copy": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "image_prompts": {"type": "array", "items": {"type": "string"}},
        "scenes": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "visual": {"type": "string", "description": "the first frame: a strong image prompt"},
                "motion": {"type": "string", "description": "only what changes during the clip"},
                "voiceover": {"type": "string", "description": "the spoken line, shown as captions"},
                "say": {"type": "string", "description": "optional: the line spelled for the voice"},
                "seconds": {"type": "number"}},
                "required": ["visual", "voiceover"]},
        },
    },
    "required": ["final_copy", "caption", "hashtags"],
}


def run(space: BusinessSpace, post_ids: list[str] | None = None, drafts: dict | None = None) -> dict:
    """Make drafts for planned posts and posts with change requests. `drafts`: {post id: text}, by the agent.
    A draft in "checking" is made again only when the agent gives its fixed text (after a failed self-check)."""
    drafts = drafts or {}
    posts = space.posts_with_status("planned", "changes_requested") + [
        p for p in space.posts_with_status("checking") if p["id"] in drafts]
    if post_ids:
        posts = [p for p in posts if p["id"] in post_ids]
    if not posts:
        return {"status": "nothing_to_do", "message": "No planned posts or change requests."}
    missing = [p for p in posts if p["id"] not in drafts and not config.MOCK]
    made = [make_draft(space, post, drafts.get(post["id"])) for post in posts if post not in missing]
    planner.write_plan_file(space)  # Keep content_plan.json up to date.
    if config.MOCK and made:  # Demo and tests: no self-check.
        owner_approval_send(space, made)
    result = needs_text(space, missing) if missing else {"status": "drafts_ready", "next_step": "Owner approval."}
    if made:
        result["drafts"] = made
    if made and not config.MOCK:
        if not missing:
            result["status"] = "check_first"
        result["check"] = ("Self-check (rules/CREATIVE_RULES.md section 6): open EVERY file in each draft's "
                           "`review`, write pass or fail with what you saw. Fix a fail: run create_drafts again with "
                           "that post's draft, the failed prompts rewritten and every other prompt unchanged (only "
                           "changed items are paid for). At most 2 fix rounds. When all pass, run mark_checked.")
    return result


def mark_checked(space: BusinessSpace, post_id: str, checks: list[dict]) -> dict:
    """The agent checked every review file. All pass -> the draft goes to the owner (pending_approval)."""
    post = space.get_post(post_id)
    if not post or post["status"] != "checking":
        return {"error": f"{post_id} is not a draft in checking."}
    review = post.get("draft", {}).get("review", {})
    wanted = review.get("images", []) + review.get("sheets", [])
    done = {c.get("file") for c in checks}
    if [f for f in wanted if f not in done]:
        return {"error": "Check every review file first.", "not_checked": [f for f in wanted if f not in done]}
    failed = [c for c in checks if str(c.get("result", "")).lower() != "pass" and not c.get("owner_decides")]
    if failed:
        return {"error": "Some checks failed. Fix them (create_drafts with the rewritten prompts), or mark an item "
                         "\"owner_decides\": true after 2 fix rounds.", "failed": failed}
    draft = dict(post["draft"], checks=checks)
    space.update_post(post_id, draft=draft, status="pending_approval")
    planner.write_plan_file(space)
    owner_approval_send(space, [{"id": post_id}])
    return {"status": "pending_approval", "id": post_id, "next_step": "Show the draft to the owner."}


def owner_approval_send(space: BusinessSpace, made: list[dict]) -> None:
    """Step 7: send the drafts for owner approval. Plan: "Push or email alert for each new draft"."""
    from . import owner_approval  # (imported here: owner_approval also imports the Creator)
    try:
        on_phone = bool(owner_approval.send_previews(space))  # Discord, or Telegram, if set.
    except Exception as exc:  # noqa: BLE001 - the drafts are saved; the next round sends them again
        on_phone = False
        space.add_alert(f"Could not send the draft previews: {exc}", phone=False)
    space.add_alert(f"{len(made)} new draft(s) wait for your approval: " + ", ".join(m["id"] for m in made),
                    phone=not on_phone)


def needs_text(space: BusinessSpace, posts: list[dict]) -> dict:
    """Everything the agent needs to write the drafts (step 2)."""
    return {
        "status": "needs_text",
        "step": "create_drafts",
        "do": "Write one draft per post by these rules and this shape. Then run create_drafts with "
              "{\"drafts\": {\"<post id>\": <draft>, ...}}.",
        "rules": rules_text(),
        "shape": DRAFT_SCHEMA,
        "data": {
            "brand_brief": space.get_brand_brief() or {},
            "posts": [{"id": p["id"], "topic": p["topic"], "format": p["format"], "platforms": p["platforms"],
                       "script": p["script"], "owner_change_notes": p.get("change_notes", []),
                       "previous_draft": {k: v for k, v in (p.get("draft") or {}).items()
                                          if k in ("final_copy", "caption", "hashtags", "image_prompts", "scenes",
                                                   "checks")}}
                      for p in posts],
        },
    }


def make_draft(space: BusinessSpace, post: dict, text: dict | None = None) -> dict:
    """`text`: the draft text written by the agent (final_copy, caption, hashtags, image_prompts or scenes)."""
    brief = space.get_brand_brief() or {}
    folder = space.media_folder(post["id"])
    version = len(post.get("change_notes", [])) + 1
    warnings: list[str] = []
    if media.on() and not (folder / "cache").exists():
        try:  # A fresh disk: bring back the images already paid for, so nothing is paid twice.
            media.pull(space, post["id"])
        except Exception as exc:  # noqa: BLE001 - without them the draft is only made again (costs money)
            warnings.append(f"Could not read the kept media from R2: {str(exc)[:200]}")

    # Steps 1-2: get the script; the final copy (+ image prompts or scenes) comes from the agent.
    if text is not None:
        draft = {k: v for k, v in text.items() if k in DRAFT_SCHEMA["properties"]}
        draft.setdefault("final_copy", post["script"].get("caption", post["topic"]))
        draft.setdefault("caption", post["script"].get("caption", ""))
        draft.setdefault("hashtags", post["script"].get("hashtags", []))
    elif config.MOCK:
        draft = _mock_draft(post, brief)
    else:
        raise ValueError(f"No draft text for {post['id']}. Run create_drafts and write the text it asks for.")

    # Steps 3-5: media files, captions, sized per platform.
    aspects = sorted({ai_models.size_for(p, post["format"]) for p in post["platforms"]})
    owner_media = _owner_media(space, post, folder)
    if post["format"] in ("reel", "video"):
        files, extra = _make_video(draft, post, aspects, owner_media, folder, version, warnings)
    else:
        files, extra = _make_images(draft, post, aspects, owner_media, folder, version, warnings)

    # Step 6: save all to the media library.
    def rel(f: Path) -> str:  # Paths inside the Business space are saved relative to it.
        return str(f.relative_to(space.root)) if f.is_relative_to(space.root) else str(f)
    draft["files"] = [rel(f) for f in files]
    draft["media"] = {k: ([rel(f) for f in v] if isinstance(v, list) else rel(v)) for k, v in extra.items()}
    draft["sizes"] = {p: ai_models.size_for(p, post["format"]) for p in post["platforms"]}
    draft["platform_text"] = platform_texts(draft, post["platforms"])
    draft["version"] = version
    draft["made_at"] = space_today()
    draft["simulated"] = any(ai_models.is_placeholder(f) for f in files)
    if warnings:
        draft["warnings"] = warnings
    draft["review"] = _review(space, post, draft, files, folder, version, extra)
    if media.on():  # Keep the paid images and the final files in R2.
        try:
            draft["r2"] = media.push(space, post["id"])
        except Exception as exc:  # noqa: BLE001 - the next draft run tries again
            draft.setdefault("warnings", []).append(f"Could not keep the media in R2: {str(exc)[:200]}")
    (folder / f"draft_v{version}.json").write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")

    # Step 7: the self-check first ("checking"), then owner approval ("pending_approval"). MOCK skips the check.
    space.update_post(post["id"], draft=draft, status="pending_approval" if config.MOCK else "checking")
    return {"id": post["id"], "version": version, "files": draft["files"], "simulated": draft["simulated"],
            "warnings": warnings, "review": draft["review"]}


def space_today() -> str:
    from ..business_space import now_utc
    return now_utc().date().isoformat()


def _review(space, post, draft, files, folder, version, extra) -> dict:
    """What the agent must look at: every image, a frame sheet per scene clip, and the voice check."""
    root = space.root
    out: dict = {"images": [], "sheets": []}
    if post["format"] not in ("reel", "video"):
        out["images"] = [str(root / f) for f in draft["files"] if not ai_models.is_placeholder(f)]
        return out
    review = folder / "review" / f"v{version}"
    for i, clip in enumerate(c for c in extra.get("clips", []) if not ai_models.is_placeholder(c)):
        try:
            out["sheets"].append(str(media_edit.contact_sheet(clip, review / f"{clip.stem}.jpg")))
        except ServiceError as exc:
            draft.setdefault("warnings", []).append(f"No frame sheet for {clip.name}: {str(exc)[:150]}")
    final = next((f for f in files if f.suffix == ".mp4" and not ai_models.is_placeholder(f)), None)
    if final is not None and config.GROQ_API_KEY:
        script = " ".join(s["voiceover"] for s in draft.get("scenes", []))
        try:
            out["voice_check"] = ai_models.verify_words(final, script)
        except ServiceError as exc:
            out["voice_check"] = {"error": str(exc)[:200]}
    return out


def _cache(folder: Path, kind: str, *parts) -> Path:
    """Where an item made from these exact inputs is kept. The same inputs later = the same file, no new cost."""
    key = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]
    (folder / "cache").mkdir(parents=True, exist_ok=True)
    return folder / "cache" / f"{kind}-{key}"


def _cached(base: Path) -> Path | None:
    found = [p for p in sorted(base.parent.glob(base.name + ".*"))
             if not ai_models.is_placeholder(p) and p.suffix != ".json"]
    return found[0] if found else None


# ---------------- images (post, carousel) ----------------
def _make_images(draft, post, aspects, owner_media, folder, version, warnings):
    """One image per prompt (1 for a post, 3-5 for a carousel), sized for each platform.
    The owner's own photos are used first (no AI cost), then the AI image model."""
    prompts = draft.get("image_prompts") or [f"{post['topic']}, appetizing, natural light"]
    photos = [m for m in owner_media if m.suffix.lower() in IMAGE_EXT]
    files, raw = [], []
    for aspect in aspects:
        tag = aspect.replace(":", "x")
        for i, image_prompt in enumerate(prompts):
            name = folder / f"v{version}_{tag}_image{i + 1}"
            if i < len(photos):
                src = photos[i]
            else:
                full = _image_prompt(image_prompt, aspect)
                base = _cache(folder, "image", config.MACHGEN_IMAGE_MODEL, aspect, full)
                src = _cached(base) or _safe(warnings, ai_models.generate_image, full, aspect, base)
                raw.append(src)
            if ai_models.is_placeholder(src):
                files.append(src.replace(name.with_suffix(src.suffix)))
                raw.remove(src)
                continue
            try:
                files.append(media_edit.fit_image(src, aspect, name))  # Exact size for the platform.
            except Exception as exc:  # noqa: BLE001 - a broken image file must not stop the draft
                warnings.append(f"Could not size image {src.name}: {exc}")
                files.append(ai_models._placeholder_image(image_prompt, aspect, name))
    return files, {"raw_images": raw}


def _image_prompt(prompt: str, aspect: str) -> str:
    shape = {"9:16": "Vertical 9:16 frame", "16:9": "Wide 16:9 frame", "1:1": "Square frame"}.get(aspect, "")
    return f"{prompt.strip().rstrip('.')}. {shape}, main subject in the middle. {IMAGE_RULE}.".strip()


# ---------------- video (reel, video) ----------------
def _make_video(draft, post, aspects, owner_media, folder, version, warnings):
    """Plan step 4: scenes -> clips (AI video model) -> voiceover -> edit, with captions, per platform size."""
    scenes = draft.get("scenes") or [{"visual": post["topic"], "voiceover": draft["caption"], "seconds": 5}]
    scenes = [dict(s, voiceover=" ".join(s.get("voiceover", "").split())) for s in scenes]
    draft["scenes"] = scenes

    # Voiceover once per post (Fish Audio, one take). Word times (Groq Whisper) set the scene cuts and captions.
    # The voice reads "say" (spelled for the voice) where a scene has it; captions show "voiceover".
    text = " ".join(s["voiceover"] for s in scenes)
    spoken = " ".join(" ".join((s.get("say") or s["voiceover"]).split()) for s in scenes)
    base = _cache(folder, "voice", config.FISH_VOICE_ID, config.FISH_MODEL, config.FISH_SPEED, spoken)
    try:
        voice = _cached(base)
        if voice is not None and voice.with_suffix(".timing.json").exists():
            alignment = json.loads(voice.with_suffix(".timing.json").read_text(encoding="utf-8"))
        else:
            voice, alignment = ai_models.voiceover_with_timing(spoken, base)
        if alignment and spoken != text:  # Times of the spoken words -> the caption words.
            alignment = ai_models.realign(alignment, text, media_edit.media_duration(voice))
    except ServiceError as exc:
        warnings.append(str(exc)[:300])
        voice, alignment = _placeholder_voice(text, folder / f"v{version}_voiceover"), None
    voice_real = voice.suffix == ".mp3"
    tl = media_edit.timeline(scenes, alignment, media_edit.media_duration(voice) if voice_real else 0.0)
    durations = [round(sc["end"] - sc["start"], 2) for sc in tl]
    srt = media_edit.write_srt(media_edit.caption_cues(tl), folder / f"v{version}_captions.srt")

    # Clips once per clip shape (9:16 or 16:9); 1:1 is cut from 9:16 in the edit.
    clips_by_shape: dict[str, list[Path]] = {}
    files = []
    for aspect in aspects:
        shape = ai_models.clip_ratio(aspect)
        if shape not in clips_by_shape:
            clips_by_shape[shape] = [
                _scene_clip(scene, i, shape, durations[i], owner_media, folder, version, warnings)
                for i, scene in enumerate(scenes)]
        clips = clips_by_shape[shape]
        tag = aspect.replace(":", "x")
        simulated = not voice_real or any(ai_models.is_placeholder(c) for c in clips)
        out = folder / f"v{version}_{tag}_final{'.sim' if simulated else ''}.mp4"
        try:
            files.append(media_edit.edit_video(clips, durations, voice if voice_real else None, srt, aspect,
                                               out, warnings))
        except ServiceError as exc:
            warnings.append(str(exc)[:300])
            files.append(_placeholder_edit(clips, voice, draft["caption"], aspect, folder / f"v{version}_{tag}_final"))
    raw = [c for cl in clips_by_shape.values() for c in cl]
    return files, {"clips": raw, "voiceover": voice, "captions": srt}


def _scene_clip(scene, i, shape, seconds, owner_media, folder, version, warnings) -> Path:
    """One clip for one scene. Order: owner media -> AI video (MachGen or Runway) -> slideshow image -> simulated."""
    base = folder / f"v{version}_clip{shape.replace(':', 'x')}_scene{i + 1}"
    try:
        if i < len(owner_media):
            m = owner_media[i]
            if m.suffix.lower() in VIDEO_EXT:
                return m
            return media_edit.still_clip(m, seconds, shape, base.with_suffix(".mp4"))
        frame = _image_prompt(scene["visual"], shape)
        if ai_models.video_on():
            image = None
            if config.VIDEO_MODE == "image" and ai_models.image_on():
                start = _cache(folder, "frame", config.MACHGEN_IMAGE_MODEL, shape, frame)
                image = _cached(start) or ai_models.generate_image(frame, shape, start)
                prompt = f"{(scene.get('motion') or scene['visual']).strip().rstrip('.')}. {MOTION_RULE}."
            else:
                prompt = ". ".join(p.strip().rstrip(".") for p in (scene["visual"], scene.get("motion", ""),
                                                                      MOTION_RULE) if p.strip()) + "."
            secs = math.ceil(seconds)
            clip_base = _cache(folder, "clip", config.MACHGEN_VIDEO_MODEL, config.MACHGEN_VIDEO_HEIGHT, shape, secs,
                               prompt, image.name if image else "")
            return _cached(clip_base) or ai_models.generate_video_clip(
                dict(scene, visual=prompt, seconds=secs), shape, clip_base, image)
        if ai_models.image_on() and config.SLIDESHOW_FALLBACK:
            start = _cache(folder, "frame", config.MACHGEN_IMAGE_MODEL, shape, frame)
            image = _cached(start) or ai_models.generate_image(frame, shape, start)
            return media_edit.still_clip(image, seconds, shape, base.with_suffix(".mp4"))
    except ServiceError as exc:
        warnings.append(f"Scene {i + 1}: {str(exc)[:300]}")
    try:
        return ai_models.simulated_clip(scene, shape, seconds, base)
    except ServiceError as exc:
        warnings.append(f"Scene {i + 1}: {str(exc)[:200]}")
        return _placeholder_clip(scene, shape, base)


def _owner_media(space: BusinessSpace, post: dict, folder: Path) -> list[Path]:
    """The owner's own photos or clips for this post ("makes or picks the image").
    Put them in media_library/<post id>/owner/ (sorted by name), or list them in post["owner_media"]."""
    found = sorted(p for p in (folder / "owner").glob("*") if p.suffix.lower() in IMAGE_EXT + VIDEO_EXT) \
        if (folder / "owner").is_dir() else []
    for item in post.get("owner_media", []):
        p = Path(item) if Path(item).is_absolute() else space.root / item
        if p.exists():
            found.append(p)
    return found


# ---------------- text per platform ----------------
TEXT_LIMITS = {"x": 280, "instagram": 2200, "tiktok": 2200, "linkedin": 3000, "facebook": 5000, "youtube": 5000}
MAX_HASHTAGS = {"x": 2, "linkedin": 5, "facebook": 5, "instagram": 15, "tiktok": 8, "youtube": 10}


def platform_texts(draft: dict, platforms: list[str]) -> dict:
    """Final text for each platform: copy + hashtags, inside the platform's length limit."""
    out = {}
    copy = draft.get("final_copy") or draft.get("caption") or ""
    for p in platforms:
        tags = " ".join(draft.get("hashtags", [])[:MAX_HASHTAGS.get(p, 10)])
        limit = TEXT_LIMITS.get(p, 2200)
        room = limit - (len(tags) + 2 if tags else 0)
        body = copy if len(copy) <= room else copy[:max(room - 1, 0)].rsplit(" ", 1)[0] + "…"
        out[p] = f"{body}\n\n{tags}".strip() if tags else body
    return out


# ---------------- placeholders (outside service failed or no key) ----------------
def _safe(warnings: list, make, *args):
    """Call a media model. If the outside service fails, note it and use a placeholder file."""
    try:
        return make(*args)
    except ServiceError as exc:
        warnings.append(str(exc)[:300])
        return _PLACEHOLDERS[make.__name__](*args)


def _placeholder_edit(clips, voice, captions, aspect, out_path):
    out_path = out_path.with_suffix(".edit.json")
    out_path.write_text(json.dumps({"aspect": aspect, "clips": [c.name for c in clips], "voiceover": voice.name,
                                    "captions": captions, "note": "Edit failed; see warnings."}, indent=2))
    return out_path


def _placeholder_clip(scene, aspect, out_path):
    out_path = out_path.with_suffix(".clip.json")
    out_path.write_text(json.dumps({"aspect": aspect, **scene}, indent=2), encoding="utf-8")
    return out_path


def _placeholder_voice(text, out_path):
    out_path = out_path.with_suffix(".voiceover.txt")
    out_path.write_text(text, encoding="utf-8")
    return out_path


_PLACEHOLDERS = {"generate_image": ai_models._placeholder_image}


def _mock_draft(post: dict, brief: dict) -> dict:
    s = post["script"]
    note = f" (fixed: {post['change_notes'][-1]})" if post.get("change_notes") else ""
    draft = {"caption": s["caption"], "hashtags": s["hashtags"]}
    if post["format"] in ("reel", "video"):
        draft["final_copy"] = f"{s.get('hook', '')} {s['caption']}".strip() + note
        # One scene per shot from the Planner's script: show -> visual, say -> voiceover.
        draft["scenes"] = [{"visual": f"{shot['show']} Warm light, handheld, vertical.",
                            "voiceover": shot["say"], "seconds": shot.get("seconds", 4)}
                           for shot in s.get("script", [])] or [
            {"visual": post["topic"], "voiceover": s["caption"], "seconds": 4}]
    else:
        draft["final_copy"] = s["caption"] + note
        count = len(s.get("slides", [])) if post["format"] == "carousel" else 1
        slides = s.get("slides") or [post["topic"]]
        draft["image_prompts"] = [f"{slides[i % len(slides)]}. {s.get('copy_idea', '')} Photo, natural light."
                                  for i in range(max(1, min(count, 5)))]
    return draft
