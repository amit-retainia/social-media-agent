"""MachGen: images (Nano-Banana-2) and video clips (Vidu-Q3-Turbo) - the Creator's default.

Switches on when MACHGEN_API_KEY is in .env. Get a key: https://www.machgen.ai -> Profile -> API Keys.
Docs (checked 2026-11-25): https://www.machgen.ai/docs/rest_api , https://www.machgen.ai/docs/reference ,
  https://www.machgen.ai/docs/models . Same endpoints as the official client (pip install machgen-client).

How it works (REST, async):
  POST /api/v0/generate      {"model", "task_type": T2I | T2V | I2V, "prompt", "image_config" | "video_config",
                              "src_image_urls"}  -> {"task_id"}
  GET  /api/v0/tasks/{id}    status PENDING -> RUNNING -> COMPLETED | FAILED (error_msg)
  GET  /api/v0/assets/{id}   the file (needs the same Bearer key)
  POST /api/v0/upload        multipart "file" -> {"artifact_path"}; use it as "@input/<artifact_path>"
  GET  /api/v0/models        exact heights, aspect ratios and durations each model accepts
Values outside a model's lists get HTTP 400, so we read /models first and pick the nearest allowed value.
"""
import time
from pathlib import Path

from .. import config
from .http import ServiceError, request

_capabilities: dict[str, dict] = {}  # model -> {task_type: surface}, cached per run


def is_on() -> bool:
    return bool(config.MACHGEN_API_KEY)


def _base() -> str:
    return config.MACHGEN_API_URL.rstrip("/")


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.MACHGEN_API_KEY}"}


def _call(method: str, path: str, **kw):
    """One API call. On HTTP 429 (too many tasks) it waits and tries again, up to 3 times."""
    for attempt in range(4):
        try:
            return request(method, f"{_base()}{path}", service="MachGen", headers=_headers(), **kw)
        except ServiceError as exc:
            if exc.status != 429 or attempt == 3:
                raise
            time.sleep(15 * (attempt + 1))


# ---------------- model limits ----------------
def surface(model: str, task_type: str) -> dict:
    """What the model accepts for this task type (heights, aspect_ratios, durations...). {} if unknown."""
    if model not in _capabilities:
        try:
            data = _call("GET", "/api/v0/models").json()
        except ServiceError:
            return {}  # Not fatal: we send sensible defaults.
        for m in data.get("models", []):
            surfaces = {s.get("task_type"): dict(s, supports_audio=m.get("supports_audio", False))
                        for s in m.get("surfaces", [])}
            for name in [m.get("model"), *m.get("aliases", [])]:
                _capabilities[name] = surfaces
    return _capabilities.get(model, {}).get(task_type, {})


def _nearest(value, allowed):
    return min(allowed, key=lambda a: abs(a - value)) if allowed else value


def _aspect(aspect: str, allowed: list) -> str:
    if not allowed or aspect in allowed:
        return aspect
    w, h = (int(x) for x in aspect.split(":"))
    target = w / h

    def ratio(a):
        x, y = (float(v) for v in a.split(":"))
        return x / y
    return min(allowed, key=lambda a: abs(ratio(a) - target))


# ---------------- tasks ----------------
def _run_task(body: dict, out_path: Path, kind: str) -> Path:
    task = _call("POST", "/api/v0/generate", json=body).json()
    task_id = task["task_id"]
    deadline = time.time() + config.VIDEO_TIMEOUT_SECONDS
    while task.get("status") not in ("COMPLETED", "FAILED"):
        if time.time() > deadline:
            raise ServiceError(f"MachGen {kind}: timed out (task {task_id})")
        time.sleep(5 if kind == "image" else 10)
        task = _call("GET", f"/api/v0/tasks/{task_id}").json()
    if task["status"] != "COMPLETED":
        raise ServiceError(f"MachGen {kind} failed: {task.get('error_msg') or task['status']}")
    data = _call("GET", f"/api/v0/assets/{task_id}").content
    if kind == "image":  # Models return PNG, JPEG or WebP: save it under its real type.
        out_path = out_path.with_suffix(_image_ext(data))
    out_path.write_bytes(data)
    return out_path


def _image_ext(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def generate_image(prompt: str, aspect: str, out_path: Path) -> Path:
    """Text to image (T2I). Returns the image file (the Creator then sizes it per platform)."""
    model = config.MACHGEN_IMAGE_MODEL
    s = surface(model, "T2I")
    image_config = {"aspect_ratio": _aspect(aspect, s.get("aspect_ratios", [])),
                    "height": _nearest(1024 if aspect != "9:16" else 1440, s.get("heights", []))}
    body = {"model": model, "task_type": "T2I", "prompt": prompt[:s.get("max_prompt_chars") or 4000],
            "image_config": image_config}
    # Ask for PNG only from a model that lists it (Nano-Banana-2 lists none and rejects the field).
    if "png" in (s.get("output_formats") or []):
        body["image_output"] = {"output_format": "png"}
    return _run_task(body, out_path, "image")


def upload(path: Path, model: str) -> str:
    """Upload a local file. Returns the "@input/..." reference for src_image_urls."""
    mime = {".png": "image/png", ".webp": "image/webp"}.get(path.suffix.lower(), "image/jpeg")
    resp = request("POST", f"{_base()}/api/v0/upload", service="MachGen upload",
                   headers=_headers() | {"X-MachGen-Upload-Model": model},
                   files={"file": (path.name, path.read_bytes(), mime)})
    return "@input/" + resp.json()["artifact_path"]


def generate_video_clip(scene: dict, aspect: str, out_path: Path, image: Path | None = None) -> Path:
    """Text to video (T2V), or image to video (I2V) when `image` is given. Returns an MP4."""
    model = config.MACHGEN_VIDEO_MODEL
    task_type = "I2V" if image is not None else "T2V"
    s = surface(model, task_type)
    want = float(scene.get("seconds") or 5)
    durations = s.get("durations") or []
    longer = [d for d in durations if d >= want]
    seconds = min(longer) if longer else (max(durations) if durations else int(round(want)))
    video_config = {"duration_secs": int(seconds),
                    "height": _nearest(config.MACHGEN_VIDEO_HEIGHT, s.get("heights", []))}
    if task_type == "T2V" or s.get("aspect_ratios"):
        video_config["aspect_ratio"] = _aspect(aspect, s.get("aspect_ratios", []))
    body = {"model": model, "task_type": task_type, "prompt": scene["visual"][:s.get("max_prompt_chars") or 2000],
            "video_config": video_config}
    if image is not None:
        body["src_image_urls"] = [upload(image, model)]
    if s.get("supports_audio"):
        video_config["audio"] = False  # We add our own voiceover; no model sound (also cheaper).
    return _run_task(body, out_path.with_suffix(".mp4"), "video")
