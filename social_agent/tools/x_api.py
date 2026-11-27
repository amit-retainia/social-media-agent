"""X (Twitter) API v2: post text with images or a video, read a post's numbers and the follower count.

Keys (one X account; per business add _<BUSINESS>): X_API_KEY, X_API_SECRET (the app's consumer keys),
X_ACCESS_TOKEN, X_ACCESS_SECRET (the account's user keys, "Read and write"). developer.x.com -> your app -> Keys.
Signed with OAuth 1.0a user context (requests-oauthlib). Media is uploaded from the disk (no public link needed).
Comments: reading replies needs X's search API (higher paid tiers), so the team does not read or answer X replies.
Docs (checked 2026-11-25): https://docs.x.com/x-api/posts/create-post , https://docs.x.com/x-api/media/media-upload
"""
import time
from pathlib import Path

from .http import ServiceError, request

API = "https://api.x.com/2"
CHUNK = 4 * 1024 * 1024
NAMES = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")


def _auth(creds: dict):
    from requests_oauthlib import OAuth1  # Imported here, so the app runs without X.
    return OAuth1(creds["X_API_KEY"], creds["X_API_SECRET"], creds["X_ACCESS_TOKEN"], creds["X_ACCESS_SECRET"])


def _call(creds: dict, method: str, path: str, **kw) -> dict:
    resp = request(method, f"{API}/{path}", service="X", auth=_auth(creds), **kw)
    return resp.json() if resp.content or resp.text else {}


def connect(creds: dict) -> dict:
    me = _call(creds, "GET", "users/me").get("data", {})
    return {"account": me.get("username")}


def publish(creds: dict, text: str, files: list[Path], is_video: bool) -> dict:
    """One post. Up to 4 images, or 1 video. Returns {"platform_post_id", "url"}."""
    media = [_upload_video(creds, files[0])] if is_video and files else [_upload_image(creds, f) for f in files[:4]]
    body: dict = {"text": text}
    if media:
        body["media"] = {"media_ids": media}
    data = _call(creds, "POST", "tweets", json=body).get("data", {})
    return {"platform_post_id": data["id"], "url": f"https://x.com/i/status/{data['id']}"}


def _upload_image(creds: dict, path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    data = _call(creds, "POST", "media/upload", data={"media_category": "tweet_image", "media_type": mime},
                 files={"media": (path.name, path.read_bytes(), mime)})
    return data["data"]["id"]


def _upload_video(creds: dict, path: Path) -> str:
    """Chunked upload: initialize -> append parts -> finalize -> wait until X has processed it."""
    raw = path.read_bytes()
    media_id = _call(creds, "POST", "media/upload/initialize",
                     json={"media_type": "video/mp4", "total_bytes": len(raw), "media_category": "tweet_video"}
                     )["data"]["id"]
    for i, start in enumerate(range(0, len(raw), CHUNK)):
        _call(creds, "POST", f"media/upload/{media_id}/append", data={"segment_index": str(i)},
              files={"media": (path.name, raw[start:start + CHUNK], "application/octet-stream")})
    info = _call(creds, "POST", f"media/upload/{media_id}/finalize").get("data", {}).get("processing_info")
    for _ in range(60):  # Up to about 10 minutes.
        if not info or info.get("state") == "succeeded":
            return media_id
        if info.get("state") == "failed":
            raise ServiceError(f"X could not process the video: {info.get('error')}")
        time.sleep(max(int(info.get("check_after_secs") or 5), 1))
        info = _call(creds, "GET", "media/upload", params={"command": "STATUS", "media_id": media_id}
                     ).get("data", {}).get("processing_info")
    raise ServiceError("X took too long to process the video.")


def metrics(creds: dict, post_id: str) -> dict:
    m = _call(creds, "GET", f"tweets/{post_id}", params={"tweet.fields": "public_metrics"}
              ).get("data", {}).get("public_metrics", {})
    shares = None if m.get("retweet_count") is None else m["retweet_count"] + m.get("quote_count", 0)
    return {"reach": m.get("impression_count"), "views": m.get("impression_count"),
            "likes": m.get("like_count"), "comments": m.get("reply_count"),
            "shares": shares, "saves": m.get("bookmark_count")}


def followers(creds: dict) -> int | None:
    me = _call(creds, "GET", "users/me", params={"user.fields": "public_metrics"}).get("data", {})
    return (me.get("public_metrics") or {}).get("followers_count")
