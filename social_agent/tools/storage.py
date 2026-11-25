"""Media storage: gives each media file a public URL, so the social platforms can fetch it.

  Amazon S3   when AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY and S3_BUCKET are set (plan "Tech choices").
  Ayrshare    else, when AYRSHARE_API_KEY is set (Ayrshare media storage).
The media library on disk stays the master copy (one folder per post).
"""
import mimetypes
from pathlib import Path

from .. import config
from .http import ServiceError, request

AYRSHARE = "https://api.ayrshare.com/api"


def public_url(path: Path, key: str, tenant_id: str | None = None) -> str:
    """Upload `path` and return a URL. `key` is the name in storage, e.g. <tenant>/<post>/<file>."""
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if config.AWS_ACCESS_KEY_ID and config.AWS_SECRET_ACCESS_KEY and config.S3_BUCKET:
        return _s3(path, key, content_type)
    if config.AYRSHARE_API_KEY:
        return _ayrshare(path, content_type, tenant_id)
    raise ServiceError("No media storage. Set the S3 keys or AYRSHARE_API_KEY.")


def _s3(path: Path, key: str, content_type: str) -> str:
    import boto3  # Imported here, so the app runs without S3.

    s3 = boto3.client("s3", region_name=config.AWS_REGION, aws_access_key_id=config.AWS_ACCESS_KEY_ID,
                      aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY)
    s3.upload_file(str(path), config.S3_BUCKET, key, ExtraArgs={"ContentType": content_type})
    if config.S3_PUBLIC_BASE_URL:
        return config.S3_PUBLIC_BASE_URL.rstrip("/") + "/" + key
    return s3.generate_presigned_url("get_object", Params={"Bucket": config.S3_BUCKET, "Key": key},
                                     ExpiresIn=7 * 24 * 3600)


def _ayrshare(path: Path, content_type: str, tenant_id: str | None) -> str:
    headers = {"Authorization": f"Bearer {config.AYRSHARE_API_KEY}"}
    profile = config.ayrshare_profile_key(tenant_id)
    if profile:
        headers["Profile-Key"] = profile
    info = request("GET", f"{AYRSHARE}/media/uploadUrl", service="Ayrshare media", headers=headers,
                   params={"fileName": path.name, "contentType": content_type}).json()
    request("PUT", info["uploadUrl"], service="Ayrshare media upload",
            headers={"Content-Type": content_type}, data=path.read_bytes())
    return info["accessUrl"]
