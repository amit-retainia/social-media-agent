"""Social platforms, each through its own API (no paid aggregator).

  Instagram  Instagram API with Instagram Login (graph.instagram.com), a professional (business or creator)
             account. Keys: INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_USER_ID.
  Facebook   a Facebook Page (graph.facebook.com). Keys: FACEBOOK_PAGE_ID, FACEBOOK_PAGE_TOKEN.
             Meta downloads the media from a link, so Instagram and Facebook need Cloudflare R2 (storage.py).
  X          x_api.py. Keys: X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET.
  LinkedIn   linkedin.py, a personal profile or a company page. Keys: LINKEDIN_ACCESS_TOKEN, LINKEDIN_AUTHOR_URN.
  Many businesses: add _<BUSINESS> to each key name (capitals, "-" becomes "_"), e.g. INSTAGRAM_USER_ID_SWEET_CRUMBS.
  TikTok and YouTube: not supported yet.
Simulated: when a platform's keys are missing. Nothing is posted.

Functions: connect_account, publish, fetch_new_comments, reply, get_metrics.
Docs (checked 2026-11-25): https://developers.facebook.com/docs/instagram-platform/content-publishing ,
  https://developers.facebook.com/docs/pages-api/posts
"""
import hashlib
import random
import time

from .. import config
from ..business_space import now_utc
from . import linkedin, storage, x_api
from .ai_models import is_placeholder, size_for
from .http import ServiceError, request

PLATFORMS = ["instagram", "facebook", "x", "linkedin", "tiktok", "youtube"]
SUPPORTED = ("instagram", "facebook", "x", "linkedin")
OTHER_APIS = {"x": x_api, "linkedin": linkedin}  # Each has NAMES, connect, publish, metrics (+ comments, reply)
INSTAGRAM = "https://graph.instagram.com/v23.0"
FACEBOOK = "https://graph.facebook.com/v23.0"
LOGIN_WORDS = ("token", "expired", "session", "oauth", "permission")

_uploaded: dict[str, str] = {}  # Local file -> public URL (avoids uploading a file twice).


class PublishError(Exception):
    def __init__(self, message: str, login_expired: bool = False, permanent: bool = False):
        super().__init__(message)
        self.login_expired = login_expired
        self.permanent = permanent  # True = do not retry (for example a placeholder file).


def normalise(name: str) -> str:
    name = name.strip().lower()
    aliases = {"ig": "instagram", "insta": "instagram", "fb": "facebook", "twitter": "x", "yt": "youtube"}
    return aliases.get(name, name)


def _tenant(space) -> str | None:
    return getattr(space, "tenant_id", None)


def _keys(platform: str, tenant_id: str | None) -> tuple[str, str]:
    """(account id, token) for this business, or ("", "")."""
    if platform == "instagram":
        return (config.for_business("INSTAGRAM_USER_ID", tenant_id),
                config.for_business("INSTAGRAM_ACCESS_TOKEN", tenant_id))
    if platform == "facebook":
        return config.for_business("FACEBOOK_PAGE_ID", tenant_id), config.for_business("FACEBOOK_PAGE_TOKEN", tenant_id)
    return "", ""


def _creds(platform: str, tenant_id: str | None) -> dict:
    """The keys of an X or LinkedIn account for this business."""
    return {n: config.for_business(n, tenant_id) for n in OTHER_APIS[platform].NAMES}


def is_real(platform: str = "instagram", tenant_id: str | None = None) -> bool:
    if platform in OTHER_APIS:
        return all(_creds(platform, tenant_id).values())
    return all(_keys(platform, tenant_id))


def _other(platform: str, tenant_id: str | None, fn: str, *args):
    """Call an X or LinkedIn function; its errors become PublishError."""
    try:
        return getattr(OTHER_APIS[platform], fn)(_creds(platform, tenant_id), *args)
    except ServiceError as exc:
        raise PublishError(str(exc), login_expired=_is_login_error(exc.body)) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise PublishError(f"{platform}: unexpected answer ({exc})") from exc


def _api(platform: str, method: str, path: str, token: str, **params) -> dict:
    base = INSTAGRAM if platform == "instagram" else FACEBOOK
    try:
        return request(method, f"{base}/{path}", service=platform.title(),
                       params={**params, "access_token": token}).json()
    except ServiceError as exc:
        raise PublishError(str(exc), login_expired=_is_login_error(exc.body)) from exc


def _is_login_error(text: str) -> bool:
    return any(w in (text or "").lower() for w in LOGIN_WORDS)


# ---------------- connect ----------------
def connect_account(platform: str, tenant_id: str | None = None) -> dict:
    """Check that the account's keys work."""
    platform = normalise(platform)
    if platform not in PLATFORMS:
        return {"status": "not_supported"}
    if platform not in SUPPORTED:
        return {"status": "not_supported", "message": f"{platform} is not supported yet. Use instagram or facebook."}
    if not is_real(platform, tenant_id):
        return {"status": "connected", "mode": "simulated", "connected_at": now_utc().isoformat()}
    if platform in OTHER_APIS:
        try:
            info = _other(platform, tenant_id, "connect")
        except PublishError as exc:
            return {"status": "needs_login", "mode": platform,
                    "message": f"The {platform} keys do not work ({str(exc)[:150]}). Make new ones and put them in .env."}
        return {"status": "connected", "mode": platform, **info, "connected_at": now_utc().isoformat()}
    account, token = _keys(platform, tenant_id)
    try:
        if platform == "instagram":
            me = _api(platform, "GET", "me", token, fields="user_id,username")
            name = me.get("username")
        else:
            name = _api(platform, "GET", account, token, fields="name").get("name")
    except PublishError as exc:
        return {"status": "needs_login", "mode": "meta",
                "message": f"The {platform} token does not work ({str(exc)[:150]}). Make a new token and put it in .env."}
    return {"status": "connected", "mode": "meta", "account": name, "connected_at": now_utc().isoformat()}


# ---------------- publish ----------------
def publish(platform: str, post: dict, space=None) -> dict:
    """Post one approved post on one platform. Raises PublishError on failure."""
    if platform not in SUPPORTED:
        raise PublishError(f"{platform} is not supported yet.", permanent=True)
    tenant = _tenant(space)
    if not is_real(platform, tenant):
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
    if platform in OTHER_APIS:  # X and LinkedIn take the files from the disk.
        files = _media_files(platform, post, space) if space is not None else []
        return _other(platform, tenant, "publish", text, files, is_video) | {"mode": platform}
    urls = _media_urls(platform, post, space) if space is not None else []
    if not urls:
        raise PublishError(f"{platform} post needs an image or video, and this post has none.", permanent=True)
    account, token = _keys(platform, tenant)
    if platform == "instagram":
        media_id = _instagram(account, token, text, urls, is_video)
        link = _api(platform, "GET", media_id, token, fields="permalink").get("permalink")
        return {"platform_post_id": media_id, "url": link, "mode": "meta"}
    post_id = _facebook(account, token, text, urls, is_video)
    return {"platform_post_id": post_id, "url": f"https://www.facebook.com/{post_id}", "mode": "meta"}


def _instagram(user: str, token: str, caption: str, urls: list[str], is_video: bool) -> str:
    """Container -> wait until Instagram has processed it -> publish. Returns the media id."""
    if is_video:
        container = _api("instagram", "POST", f"{user}/media", token, media_type="REELS", video_url=urls[0],
                         caption=caption, share_to_feed="true")["id"]
    elif len(urls) == 1:
        container = _api("instagram", "POST", f"{user}/media", token, image_url=urls[0], caption=caption)["id"]
    else:
        children = [_api("instagram", "POST", f"{user}/media", token, image_url=u, is_carousel_item="true")["id"]
                    for u in urls[:10]]
        container = _api("instagram", "POST", f"{user}/media", token, media_type="CAROUSEL",
                         children=",".join(children), caption=caption)["id"]
    for _ in range(120):  # Up to 10 minutes for a video.
        status = _api("instagram", "GET", container, token, fields="status_code").get("status_code")
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            raise PublishError(f"Instagram could not process the media ({status}).")
        time.sleep(5)
    else:
        raise PublishError("Instagram took too long to process the media.")
    return _api("instagram", "POST", f"{user}/media_publish", token, creation_id=container)["id"]


def _facebook(page: str, token: str, message: str, urls: list[str], is_video: bool) -> str:
    """Page post: a video, one photo, or several photos in one post. Returns the post id."""
    if is_video:
        return _api("facebook", "POST", f"{page}/videos", token, file_url=urls[0], description=message)["id"]
    if len(urls) == 1:
        data = _api("facebook", "POST", f"{page}/photos", token, url=urls[0], message=message)
        return data.get("post_id") or data["id"]
    photos = [_api("facebook", "POST", f"{page}/photos", token, url=u, published="false")["id"] for u in urls]
    attached = {f"attached_media[{i}]": f'{{"media_fbid":"{p}"}}' for i, p in enumerate(photos)}
    return _api("facebook", "POST", f"{page}/feed", token, message=message, **attached)["id"]


def _media_files(platform: str, post: dict, space) -> list:
    """The media files sized for this platform (local paths)."""
    files = post.get("draft", {}).get("files", [])
    tag = size_for(platform, post["format"]).replace(":", "x")
    mine = [f for f in files if f"_{tag}_" in f] or files
    if any(is_placeholder(f) for f in mine):
        raise PublishError("The media is a placeholder file. Add MACHGEN_API_KEY (images and video) and "
                           "FISH_API_KEY (voice), then make the draft again.", permanent=True)
    return [space.root / rel for rel in mine]


def _media_urls(platform: str, post: dict, space) -> list[str]:
    """Pick the media files sized for this platform and upload them (Instagram and Facebook)."""
    urls = []
    for path in _media_files(platform, post, space):
        rel = path.relative_to(space.root).as_posix()
        if str(path) not in _uploaded:
            try:
                _uploaded[str(path)] = storage.public_url(path, f"{space.tenant_id}/{rel}", space.tenant_id)
            except ServiceError as exc:
                raise PublishError(f"Media upload failed: {exc}", permanent=not storage.r2_on()) from exc
        urls.append(_uploaded[str(path)])
    return urls


# ---------------- comments and replies ----------------
def fetch_new_comments(platform: str, post: dict, space=None) -> list[dict]:
    tenant = _tenant(space)
    if platform not in SUPPORTED:
        return []
    if not is_real(platform, tenant):
        return [
            {"id": f"{post['id']}-{platform}-c1", "from": "happy_customer", "text": "This looks amazing! 😍"},
            {"id": f"{post['id']}-{platform}-c2", "from": "angry_customer",
             "text": "My last order was late and cold. I want a refund."},
        ]
    post_id = post.get("publish_results", {}).get(platform, {}).get("platform_post_id")
    if not post_id or platform == "x":  # Reading X replies needs X's search API (higher paid tiers).
        return []
    _, token = _keys(platform, tenant)
    try:
        if platform == "linkedin":
            return _other(platform, tenant, "comments", post_id)
        if platform == "instagram":
            data = _api(platform, "GET", f"{post_id}/comments", token, fields="id,text,username")
            return [{"id": c["id"], "from": c.get("username") or "someone", "text": c.get("text", "")}
                    for c in data.get("data", [])]
        data = _api(platform, "GET", f"{post_id}/comments", token, fields="id,message,from")
        return [{"id": c["id"], "from": (c.get("from") or {}).get("name") or "someone", "text": c.get("message", "")}
                for c in data.get("data", [])]
    except PublishError:
        return []


def reply(platform: str, comment_id: str, text: str, space=None) -> None:
    tenant = _tenant(space)
    if platform not in SUPPORTED or platform == "x" or not is_real(platform, tenant):
        return
    if platform == "linkedin":
        _other(platform, tenant, "reply", comment_id, text)
        return
    _, token = _keys(platform, tenant)
    edge = "replies" if platform == "instagram" else "comments"
    _api(platform, "POST", f"{comment_id}/{edge}", token, message=text)


# ---------------- metrics ----------------
def get_metrics(platform: str, post: dict, checkpoint: str, space=None) -> dict:
    """Reach, views, likes, comments. Missing numbers are 0 and listed in "gaps"."""
    tenant = _tenant(space)
    if platform not in SUPPORTED or not is_real(platform, tenant):
        seed = int(hashlib.md5(f"{post['id']}{platform}{checkpoint}".encode()).hexdigest(), 16)
        rng = random.Random(seed)
        scale = {"1h": 1, "24h": 6, "7d": 15}[checkpoint]
        boost = 2.0 if post.get("format") in ("reel", "video") else 1.0
        reach = int(rng.randint(80, 300) * scale * boost)
        return {"reach": reach, "views": int(reach * rng.uniform(1.0, 1.6)),
                "likes": int(reach * rng.uniform(0.03, 0.12)),
                "comments": int(reach * rng.uniform(0.002, 0.02)), "mode": "simulated"}
    post_id = post.get("publish_results", {}).get(platform, {}).get("platform_post_id")
    empty = {"reach": 0, "views": 0, "likes": 0, "comments": 0, "gaps": ["no post id"], "mode": "meta"}
    if not post_id:
        return empty
    _, token = _keys(platform, tenant)
    raw: dict = {}
    try:
        if platform in OTHER_APIS:
            raw = _other(platform, tenant, "metrics", post_id)
        elif platform == "instagram":
            data = _api(platform, "GET", f"{post_id}/insights", token, metric="reach,views,likes,comments")
            raw = {m["name"]: (m.get("values") or [{}])[0].get("value") for m in data.get("data", [])}
        else:
            data = _api(platform, "GET", post_id, token,
                        fields="reactions.summary(true),comments.summary(true)")
            raw = {"likes": (data.get("reactions") or {}).get("summary", {}).get("total_count"),
                   "comments": (data.get("comments") or {}).get("summary", {}).get("total_count")}
            try:
                ins = _api(platform, "GET", f"{post_id}/insights", token, metric="post_impressions_unique")
                raw["reach"] = (ins.get("data") or [{}])[0].get("values", [{}])[0].get("value")
            except PublishError:
                pass
    except PublishError as exc:
        return empty | {"gaps": [str(exc)[:200]]}
    return normalise_metrics(raw)


def normalise_metrics(raw: dict) -> dict:
    out, gaps = {}, []
    for field in ("reach", "views", "likes", "comments"):
        value = raw.get(field)
        if not isinstance(value, (int, float)):
            gaps.append(field)
            value = 0
        out[field] = int(value)
    if not out["reach"] and out["views"]:
        out["reach"] = out["views"]
    out["gaps"] = gaps
    out["mode"] = "meta"
    return out
