"""Social platforms: Instagram, Facebook, X, LinkedIn, TikTok, YouTube.

Real: Ayrshare (one API for all six platforms), when AYRSHARE_API_KEY is set.
  The plan's option: "start with an aggregator (e.g. Ayrshare) for speed, then move to direct APIs".
  The owner links each social account once in the Ayrshare dashboard (OAuth). Ayrshare keeps the
  tokens, so no tokens are stored in this project.
  Many businesses: give each business its own Ayrshare profile key (see config.ayrshare_profile_key).
Simulated: when the key is missing. Nothing is posted.

Functions: connect_account, publish, fetch_new_comments, reply, get_metrics.
"""
import hashlib
import random

from .. import config
from ..business_space import now_utc
from . import storage
from .ai_models import is_placeholder, size_for
from .http import ServiceError, request

PLATFORMS = ["instagram", "facebook", "x", "linkedin", "tiktok", "youtube"]
AYRSHARE = "https://api.ayrshare.com/api"
AYRSHARE_NAMES = {"x": "twitter"}  # Our name -> Ayrshare name. Others are the same.
LOGIN_WORDS = ("token", "expired", "reauthor", "re-link", "relink", "not linked", "unauthor", "session")

_uploaded: dict[str, str] = {}  # Local file -> public URL (avoids uploading a file twice).


class PublishError(Exception):
    def __init__(self, message: str, login_expired: bool = False, permanent: bool = False):
        super().__init__(message)
        self.login_expired = login_expired
        self.permanent = permanent  # True = do not retry (for example a placeholder file).


def is_real() -> bool:
    return bool(config.AYRSHARE_API_KEY)


def normalise(name: str) -> str:
    name = name.strip().lower()
    aliases = {"ig": "instagram", "insta": "instagram", "fb": "facebook", "twitter": "x", "yt": "youtube"}
    return aliases.get(name, name)


def _headers(tenant_id: str | None) -> dict:
    headers = {"Authorization": f"Bearer {config.AYRSHARE_API_KEY}", "Content-Type": "application/json"}
    profile = config.ayrshare_profile_key(tenant_id)
    if profile:
        headers["Profile-Key"] = profile
    return headers


# ---------------- connect ----------------
def connect_account(platform: str, tenant_id: str | None = None) -> dict:
    """Check that the account is linked. Real: asks Ayrshare which accounts are linked."""
    platform = normalise(platform)
    if platform not in PLATFORMS:
        return {"status": "not_supported"}
    if not is_real():
        return {"status": "connected", "mode": "simulated", "connected_at": now_utc().isoformat()}
    try:
        user = request("GET", f"{AYRSHARE}/user", service="Ayrshare", headers=_headers(tenant_id)).json()
    except ServiceError as exc:
        return {"status": "error", "mode": "ayrshare", "message": str(exc)}
    linked = {normalise(p) for p in user.get("activeSocialAccounts", [])}
    if platform in linked:
        return {"status": "connected", "mode": "ayrshare", "connected_at": now_utc().isoformat()}
    return {"status": "needs_login", "mode": "ayrshare",
            "message": f"Link your {platform} account at https://app.ayrshare.com (Social Accounts), "
                       "then say 'connect my accounts again'."}


# ---------------- publish ----------------
def publish(platform: str, post: dict, space=None) -> dict:
    """Post one approved post on one platform. Raises PublishError on failure."""
    if not is_real():
        if random.random() < config.SIMULATE_FAIL_RATE:
            raise PublishError(f"{platform} API error (simulated)")
        return {"platform_post_id": f"{platform}-{post['id']}", "url": f"https://{platform}.example/{post['id']}",
                "mode": "simulated"}

    draft = post.get("draft", {})
    text = (draft.get("platform_text") or {}).get(platform)  # The Creator's text for this platform.
    if not text:
        text = (draft.get("final_copy") or draft.get("caption") or post["topic"]).strip()
        tags = " ".join(draft.get("hashtags", []))
        if tags and tags not in text:
            text += "\n\n" + tags
    is_video = post["format"] in ("reel", "video")
    media_urls = _media_urls(platform, post, space) if space is not None else []
    if not media_urls and platform in ("instagram", "tiktok", "youtube"):
        raise PublishError(f"{platform} needs an image or video, and this post has none.", permanent=True)

    name = AYRSHARE_NAMES.get(platform, platform)
    body: dict = {"post": text, "platforms": [name]}
    if media_urls:
        body["mediaUrls"] = media_urls
        body["isVideo"] = is_video
    if platform == "youtube":
        body["youTubeOptions"] = {"title": post["topic"][:100], "shorts": post["format"] == "reel",
                                  "visibility": "public"}
    if platform == "instagram" and post["format"] == "reel":
        body["instagramOptions"] = {"reels": True, "shareReelsFeed": True}
    try:
        data = request("POST", f"{AYRSHARE}/post", service="Ayrshare", headers=_headers(_tenant(space)),
                       json=body).json()
    except ServiceError as exc:
        raise PublishError(str(exc), login_expired=_is_login_error(exc.body)) from exc
    if data.get("status") != "success":
        message = str(data.get("errors") or data.get("message") or data)
        raise PublishError(f"Ayrshare: {message[:300]}", login_expired=_is_login_error(message))
    item = next((p for p in data.get("postIds", []) if p.get("platform") == name), {})
    return {"ayrshare_id": data.get("id"), "platform_post_id": item.get("id"),
            "url": item.get("postUrl"), "mode": "ayrshare"}


def _tenant(space) -> str | None:
    return getattr(space, "tenant_id", None)


def _is_login_error(text: str) -> bool:
    return any(w in (text or "").lower() for w in LOGIN_WORDS)


def _media_urls(platform: str, post: dict, space) -> list[str]:
    """Pick the media files sized for this platform and upload them."""
    files = post.get("draft", {}).get("files", [])
    tag = size_for(platform, post["format"]).replace(":", "x")
    mine = [f for f in files if f"_{tag}_" in f] or files
    if any(is_placeholder(f) for f in mine):
        raise PublishError("The media is a placeholder file. Add MACHGEN_API_KEY (images and video) and "
                           "ELEVENLABS_API_KEY (voice), then make the draft again.", permanent=True)
    urls = []
    for rel in mine:
        path = space.root / rel
        if str(path) not in _uploaded:
            try:
                _uploaded[str(path)] = storage.public_url(path, f"{space.tenant_id}/{rel}", space.tenant_id)
            except ServiceError as exc:
                raise PublishError(f"Media upload failed: {exc}") from exc
        urls.append(_uploaded[str(path)])
    return urls


# ---------------- comments and replies ----------------
def fetch_new_comments(platform: str, post: dict, space=None) -> list[dict]:
    if not is_real():
        return [
            {"id": f"{post['id']}-{platform}-c1", "from": "happy_customer", "text": "This looks amazing! 😍"},
            {"id": f"{post['id']}-{platform}-c2", "from": "angry_customer",
             "text": "My last order was late and cold. I want a refund."},
        ]
    ayr_id = post.get("publish_results", {}).get(platform, {}).get("ayrshare_id")
    if not ayr_id:
        return []
    try:
        data = request("GET", f"{AYRSHARE}/comments/{ayr_id}", service="Ayrshare",
                       headers=_headers(_tenant(space))).json()
    except ServiceError:
        return []
    out = []
    for c in data.get(AYRSHARE_NAMES.get(platform, platform), []) or []:
        who = c.get("username") or c.get("userName") or (c.get("from") or {}).get("name") or "someone"
        out.append({"id": str(c.get("commentId") or c.get("id")), "from": who, "text": c.get("comment", "")})
    return out


def reply(platform: str, comment_id: str, text: str, space=None) -> None:
    if not is_real():
        return
    request("POST", f"{AYRSHARE}/comments/reply/{comment_id}", service="Ayrshare",
            headers=_headers(_tenant(space)),
            json={"platforms": [AYRSHARE_NAMES.get(platform, platform)], "comment": text})


# ---------------- metrics ----------------
def get_metrics(platform: str, post: dict, checkpoint: str, space=None) -> dict:
    """Reach, views, likes, comments. Missing numbers are 0 and listed in "gaps"."""
    if not is_real():
        seed = int(hashlib.md5(f"{post['id']}{platform}{checkpoint}".encode()).hexdigest(), 16)
        rng = random.Random(seed)
        scale = {"1h": 1, "24h": 6, "7d": 15}[checkpoint]
        boost = 2.0 if post.get("format") in ("reel", "video") else 1.0
        reach = int(rng.randint(80, 300) * scale * boost)
        return {"reach": reach, "views": int(reach * rng.uniform(1.0, 1.6)),
                "likes": int(reach * rng.uniform(0.03, 0.12)),
                "comments": int(reach * rng.uniform(0.002, 0.02)), "mode": "simulated"}
    ayr_id = post.get("publish_results", {}).get(platform, {}).get("ayrshare_id")
    empty = {"reach": 0, "views": 0, "likes": 0, "comments": 0, "gaps": ["no post id"], "mode": "ayrshare"}
    if not ayr_id:
        return empty
    name = AYRSHARE_NAMES.get(platform, platform)
    try:
        data = request("POST", f"{AYRSHARE}/analytics/post", service="Ayrshare", headers=_headers(_tenant(space)),
                       json={"id": ayr_id, "platforms": [name]}).json()
    except ServiceError as exc:
        return empty | {"gaps": [str(exc)[:200]]}
    return normalise_metrics((data.get(name) or {}).get("analytics") or {})


# Each platform names its numbers differently. Plan: "Analyst normalises metrics and marks gaps".
METRIC_KEYS = {
    "reach": ["reach", "reachCount", "impressionsUnique", "postImpressionsUnique", "uniqueImpressionsCount"],
    "views": ["views", "viewCount", "videoViews", "plays", "impressions", "impressionCount", "impressionsCount",
              "postImpressions", "playCount"],
    "likes": ["likes", "likeCount", "reactions", "favoriteCount", "likesCount", "reactionCount"],
    "comments": ["comments", "commentCount", "commentsCount", "replyCount"],
}


def normalise_metrics(raw: dict) -> dict:
    out, gaps = {}, []
    for field, keys in METRIC_KEYS.items():
        value = next((raw[k] for k in keys if isinstance(raw.get(k), (int, float))), None)
        if isinstance(raw.get(field), dict):  # e.g. Facebook reactions {"total": n}
            value = raw[field].get("total", value)
        if value is None:
            gaps.append(field)
        out[field] = int(value or 0)
    if not out["reach"] and out["views"]:
        out["reach"] = out["views"]
    out["gaps"] = gaps
    out["mode"] = "ayrshare"
    return out
