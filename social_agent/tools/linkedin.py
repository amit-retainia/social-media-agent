"""LinkedIn Posts API: post text with images or a video, read and answer comments, read a post's numbers.

Keys (per business add _<BUSINESS>):
  LINKEDIN_ACCESS_TOKEN   an OAuth token from a LinkedIn developer app (lasts 60 days)
  LINKEDIN_AUTHOR_URN     who posts: urn:li:person:<id> (personal profile, product "Share on LinkedIn",
                          scope w_member_social) or urn:li:organization:<id> (company page, product
                          "Community Management API", scopes w_organization_social + r_organization_social;
                          LinkedIn must approve the app first)
Media is uploaded to LinkedIn from the disk (no public link needed).
Docs (checked 2026-11-25): https://learn.microsoft.com/linkedin/marketing/community-management/shares/posts-api ,
  .../images-api , .../videos-api , .../network-update-social-actions
"""
import time
from pathlib import Path
from urllib.parse import quote

from .http import ServiceError, request

API = "https://api.linkedin.com/rest"
VERSION = "202510"  # LinkedIn-Version header (YYYYMM). LinkedIn keeps each version for about a year.
NAMES = ("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_AUTHOR_URN")


def _headers(creds: dict) -> dict:
    return {"Authorization": f"Bearer {creds['LINKEDIN_ACCESS_TOKEN']}", "LinkedIn-Version": VERSION,
            "X-Restli-Protocol-Version": "2.0.0"}


def _call(creds: dict, method: str, path: str, **kw):
    return request(method, f"{API}/{path}", service="LinkedIn", headers=_headers(creds), **kw)


def connect(creds: dict) -> dict:
    """The keys are checked on the first post (a person token cannot read its own profile without more scopes)."""
    return {"account": creds["LINKEDIN_AUTHOR_URN"]}


def publish(creds: dict, text: str, files: list[Path], is_video: bool) -> dict:
    """One post: 1 video, 1 image, or several images (multi-image). Returns {"platform_post_id", "url"}."""
    author = creds["LINKEDIN_AUTHOR_URN"]
    body: dict = {"author": author, "commentary": _escape(text), "visibility": "PUBLIC",
                  "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                                   "thirdPartyDistributionChannels": []},
                  "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
    if is_video and files:
        body["content"] = {"media": {"id": _upload_video(creds, files[0])}}
    elif len(files) == 1:
        body["content"] = {"media": {"id": _upload_image(creds, files[0])}}
    elif files:
        body["content"] = {"multiImage": {"images": [{"id": _upload_image(creds, f)} for f in files[:20]]}}
    resp = _call(creds, "POST", "posts", json=body)
    urn = (getattr(resp, "headers", None) or {}).get("x-restli-id") or ""
    if not urn:
        raise ServiceError("LinkedIn did not return the post id.")
    return {"platform_post_id": urn, "url": f"https://www.linkedin.com/feed/update/{urn}"}


def _escape(text: str) -> str:
    """LinkedIn's "little text" format: these characters must be escaped, or the post is cut or rejected.
    # is not escaped, so hashtags still work. The backslash goes first."""
    for ch in "\\|{}@[]()<>*_~":
        text = text.replace(ch, "\\" + ch)
    return text


def _upload_image(creds: dict, path: Path) -> str:
    value = _call(creds, "POST", "images", params={"action": "initializeUpload"},
                  json={"initializeUploadRequest": {"owner": creds["LINKEDIN_AUTHOR_URN"]}}).json()["value"]
    request("PUT", value["uploadUrl"], service="LinkedIn upload", data=path.read_bytes(),
            headers={"Authorization": f"Bearer {creds['LINKEDIN_ACCESS_TOKEN']}"})
    return value["image"]


def _upload_video(creds: dict, path: Path) -> str:
    """initializeUpload -> PUT each part -> finalizeUpload -> wait until LinkedIn has processed it."""
    raw = path.read_bytes()
    value = _call(creds, "POST", "videos", params={"action": "initializeUpload"},
                  json={"initializeUploadRequest": {"owner": creds["LINKEDIN_AUTHOR_URN"], "fileSizeBytes": len(raw),
                                                    "uploadCaptions": False, "uploadThumbnail": False}}).json()["value"]
    etags = []
    for part in value["uploadInstructions"]:
        resp = request("PUT", part["uploadUrl"], service="LinkedIn upload",
                       data=raw[part["firstByte"]:part["lastByte"] + 1],
                       headers={"Content-Type": "application/octet-stream"})
        etags.append((getattr(resp, "headers", None) or {}).get("etag", ""))
    _call(creds, "POST", "videos", params={"action": "finalizeUpload"},
          json={"finalizeUploadRequest": {"video": value["video"], "uploadToken": value.get("uploadToken", ""),
                                          "uploadedPartIds": etags}})
    for _ in range(60):  # Up to about 5 minutes.
        status = _call(creds, "GET", f"videos/{quote(value['video'], safe='')}").json().get("status")
        if status == "AVAILABLE":
            return value["video"]
        if status == "PROCESSING_FAILED":
            raise ServiceError("LinkedIn could not process the video.")
        time.sleep(5)
    raise ServiceError("LinkedIn took too long to process the video.")


def comments(creds: dict, post_urn: str) -> list[dict]:
    """Comment ids are "<post urn>|<comment urn>": a reply needs both."""
    data = _call(creds, "GET", f"socialActions/{quote(post_urn, safe='')}/comments").json()
    return [{"id": f"{post_urn}|{c.get('$URN') or c.get('commentUrn', '')}",
             "from": c.get("actor", "someone"), "text": (c.get("message") or {}).get("text", "")}
            for c in data.get("elements", [])]


def reply(creds: dict, comment_id: str, text: str) -> None:
    post_urn, _, comment_urn = comment_id.partition("|")
    _call(creds, "POST", f"socialActions/{quote(post_urn, safe='')}/comments",
          json={"actor": creds["LINKEDIN_AUTHOR_URN"], "object": post_urn, "parentComment": comment_urn,
                "message": {"text": text}})


def metrics(creds: dict, post_urn: str) -> dict:
    data = _call(creds, "GET", f"socialActions/{quote(post_urn, safe='')}").json()
    return {"likes": (data.get("likesSummary") or {}).get("totalLikes"),
            "comments": (data.get("commentsSummary") or {}).get("aggregatedTotalComments")}
