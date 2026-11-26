"""A post's kept media in R2 (the same idea as CB Reels' tools/media.py).

Kept in R2, under sms/<business>/media_library/<post id>/:
  cache/image-*, cache/frame-*   the paid AI images (post images, reel start frames), so a fresh disk never pays twice
  v<N>_<size>_*                  the final files per size (images, final videos)
  owner/*                        the owner's own photos and videos
Disk only (cheap or free to make again): clips, voice, captions, frame sheets, the draft JSON.

push() runs after each draft is made and pull() before, only where MEDIA_UPLOAD=1 (set it on the Grok Bot only,
like REELS_UPLOAD in CB Reels). A failed upload never stops a draft: the next draft run tries again.
"""
import hashlib
import os
import re
from pathlib import Path

from .. import config
from . import storage
from .ai_models import is_placeholder


def on() -> bool:
    return storage.r2_on() and config.MEDIA_UPLOAD


def kept_files(folder: Path) -> list[Path]:
    """The files of one post folder that are kept in R2."""
    files = [p for p in sorted((folder / "cache").glob("*")) if p.name.startswith(("image-", "frame-"))]
    files += [p for p in sorted(folder.glob("v*_*")) if re.match(r"v\d+_\d+x\d+_", p.name)]  # v1_9x16_final.mp4
    files += sorted((folder / "owner").glob("*"))
    return [p for p in files if p.is_file() and not is_placeholder(p) and p.suffix != ".json"]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def push(space, post_id: str) -> list[str]:
    """Upload new or changed kept files of a post. Returns every kept file's R2 key."""
    folder = space.media_folder(post_id)
    s3, bucket = storage.client(), config.R2_BUCKET
    keys = []
    for path in kept_files(folder):
        key = storage.key_for(space.tenant_id, path.relative_to(space.root).as_posix())
        digest = _sha256(path)
        try:
            same = s3.head_object(Bucket=bucket, Key=key).get("Metadata", {}).get("sha256") == digest
        except Exception:  # noqa: BLE001 - not there yet
            same = False
        if not same:
            s3.upload_file(str(path), bucket, key, ExtraArgs={
                "ContentType": storage.content_type(path),
                "Metadata": {"sha256": digest, "mtime": str(path.stat().st_mtime)}})
        keys.append(key)
    return keys


def pull(space, post_id: str) -> int:
    """Download the kept files a fresh disk is missing. Returns how many came down."""
    folder = space.media_folder(post_id)
    prefix = storage.key_for(space.tenant_id, folder.relative_to(space.root).as_posix()) + "/"
    s3, bucket = storage.client(), config.R2_BUCKET
    got = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            path = folder / obj["Key"][len(prefix):]
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, obj["Key"], str(path))
            mtime = s3.head_object(Bucket=bucket, Key=obj["Key"]).get("Metadata", {}).get("mtime")
            if mtime:
                os.utime(path, (float(mtime), float(mtime)))
            got += 1
    return got
