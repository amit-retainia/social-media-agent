"""3. Creator - makes text, images, videos.

Steps (from the plan):
  1. Gets the script for each post.
  2. Text: writes the final copy in the brand voice.
  3. Image: writes an image prompt, then makes the image with an AI image model.
  4. Video: splits the script into scenes, makes clips with an AI video model,
     adds voiceover, then edits.
  5. Adds captions and sizes each file per platform (9:16, 1:1, 16:9).
  6. Saves all files to the media library.
  7. Sends the draft for owner approval.
Gets: script from the Planner; media from AI models.
Gives: draft to Owner approval; files to the media library.

Outside services (each switches on when its key is in .env; see tools/ai_models.py):
  images and video clips: MachGen (default; else OpenAI images, Runway video)   voiceover: ElevenLabs
  edit, captions and sizes per platform: ffmpeg + Pillow (no key).
The owner can also give own photos or clips: media_library/<post id>/owner/ (used before AI).
Media library per post: script.json, draft_vN.json, raw images, clips, voiceover, captions .srt,
and the final files named v<N>_<size>_... (for example v1_9x16_final.mp4, v1_1x1_image1.jpg).
"""
import json
import math
from pathlib import Path

from .. import config, llm
from ..business_space import BusinessSpace
from ..tools import ai_models, media_edit
from ..tools.http import ServiceError
from . import planner

SYSTEM = """You are the Creator in a social media team for one small business.
Turn the Planner's script into a ready draft.
Rules:
- Write in the brand voice from the brand brief. Never use banned topics.
- Do not invent facts (prices, dates, ingredients) that are not in the brief or the script.
- final_copy: the post text. caption: short on-screen or first-line caption.
- post / carousel: write image_prompts (1 for post, 3-5 for carousel). Each is a detailed prompt for an
  AI image model: subject, setting, light, mood, camera. No text or logos in the image.
- reel / video: the script has shots in order (show, say, seconds). Make one scene per shot.
  Each scene: visual (a detailed prompt for the AI video model, based on "show"),
  voiceover (based on "say"), seconds. Start with the hook.
- If the owner asked for changes, follow the notes exactly."""

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
                "visual": {"type": "string"}, "voiceover": {"type": "string"}, "seconds": {"type": "number"}},
                "required": ["visual", "voiceover"]},
        },
    },
    "required": ["final_copy", "caption", "hashtags"],
}


def run(space: BusinessSpace, post_ids: list[str] | None = None) -> dict:
    """Make drafts for planned posts and posts with change requests."""
    posts = space.posts_with_status("planned", "changes_requested")
    if post_ids:
        posts = [p for p in posts if p["id"] in post_ids]
    if not posts:
        return {"status": "nothing_to_do", "message": "No planned posts or change requests."}
    made = [make_draft(space, post) for post in posts]
    planner.write_plan_file(space)  # Keep content_plan.json up to date.
    # Step 7: send the drafts for owner approval. Plan: "Push or email alert for each new draft".
    from . import owner_approval  # (imported here: owner_approval also imports the Creator)
    try:
        on_phone = bool(owner_approval.send_previews(space))  # Owner app on the phone (Telegram), if set.
    except Exception as exc:  # noqa: BLE001 - the drafts are saved; the owner review sends them again
        on_phone = False
        space.add_alert(f"Could not send previews to your phone: {exc}", phone=False)
    space.add_alert(f"{len(made)} new draft(s) wait for your approval: " + ", ".join(m["id"] for m in made),
                    phone=not on_phone)
    return {"status": "drafts_ready", "drafts": made, "next_step": "Owner approval."}


def make_draft(space: BusinessSpace, post: dict) -> dict:
    brief = space.get_brand_brief() or {}
    folder = space.media_folder(post["id"])
    version = len(post.get("change_notes", [])) + 1

    # Steps 1-2: get the script; write final copy (+ image prompts or scenes).
    if config.MOCK:
        draft = _mock_draft(post, brief)
    else:
        prompt = (
            f"Brand brief:\n{json.dumps(brief, indent=2)}\n\n"
            f"Post: topic={post['topic']}, format={post['format']}, platforms={post['platforms']}\n"
            f"Script from the Planner:\n{json.dumps(post['script'], indent=2)}\n"
        )
        if post.get("change_notes"):
            prompt += f"\nOwner change notes (newest last): {post['change_notes']}\n"
            prompt += f"Previous draft:\n{json.dumps(post.get('draft', {}), indent=2)}\n"
        draft = llm.structured(SYSTEM, prompt, "draft", DRAFT_SCHEMA)

    # Steps 3-5: media files, captions, sized per platform.
    warnings: list[str] = []
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
    draft["simulated"] = any(ai_models.is_placeholder(f) for f in files)
    if warnings:
        draft["warnings"] = warnings
    (folder / f"draft_v{version}.json").write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")

    # Step 7: send for owner approval (status "pending_approval" = waiting for approval).
    space.update_post(post["id"], draft=draft, status="pending_approval")
    return {"id": post["id"], "version": version, "files": draft["files"], "simulated": draft["simulated"],
            "warnings": warnings}


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
                src = _safe(warnings, ai_models.generate_image, _image_prompt(image_prompt, aspect), aspect,
                            folder / f"v{version}_raw_{tag}_image{i + 1}")
                raw.append(src)
            if ai_models.is_placeholder(src):
                files.append(src.rename(name.with_suffix(src.suffix)))
                raw.remove(src)
                continue
            try:
                files.append(media_edit.fit_image(src, aspect, name))  # Exact size for the platform.
            except Exception as exc:  # noqa: BLE001 - a broken image file must not stop the draft
                warnings.append(f"Could not size image {src.name}: {exc}")
                files.append(ai_models._placeholder_image(image_prompt, aspect, name))
    return files, {"raw_images": raw}


def _image_prompt(prompt: str, aspect: str) -> str:
    shape = {"9:16": "vertical 9:16", "16:9": "wide 16:9", "1:1": "square"}.get(aspect, "")
    return f"{prompt} {shape} social media photo, no text, no logos, no watermarks.".strip()


# ---------------- video (reel, video) ----------------
def _make_video(draft, post, aspects, owner_media, folder, version, warnings):
    """Plan step 4: scenes -> clips (AI video model) -> voiceover -> edit, with captions, per platform size."""
    scenes = draft.get("scenes") or [{"visual": post["topic"], "voiceover": draft["caption"], "seconds": 5}]
    scenes = [dict(s, voiceover=" ".join(s.get("voiceover", "").split())) for s in scenes]
    draft["scenes"] = scenes

    # Voiceover once per post. ElevenLabs timestamps time the scene cuts and the captions.
    text = " ".join(s["voiceover"] for s in scenes)
    try:
        voice, alignment = ai_models.voiceover_with_timing(text, folder / f"v{version}_voiceover")
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
        if ai_models.video_on():
            image = None
            if config.VIDEO_MODE == "image" and ai_models.image_on():
                image = ai_models.generate_image(_image_prompt(scene["visual"], shape), shape,
                                                 base.with_name(base.name + "_start"))
            want = dict(scene, seconds=math.ceil(seconds))
            return ai_models.generate_video_clip(want, shape, base, image)
        if ai_models.image_on() and config.SLIDESHOW_FALLBACK:
            image = ai_models.generate_image(_image_prompt(scene["visual"], shape), shape,
                                             base.with_name(base.name + "_image"))
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
