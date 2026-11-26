"""Media storage: Cloudflare R2 (S3 API), one private bucket for every business.

  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET (+ optional R2_PUBLIC_BASE_URL).
Every file has one key: sms/<business>/<path inside the business folder>,
for example sms/sweet-crumbs/media_library/post-1a2b/v1_1x1_image1.jpg. media.py keeps each post's paid images and
final files there; public_url() gives the platforms a link to the same key.
"""
import mimetypes
from pathlib import Path

from .. import config
from .http import ServiceError

PREFIX = "sms"


def r2_on() -> bool:
    return all([config.R2_ACCOUNT_ID, config.R2_ACCESS_KEY_ID, config.R2_SECRET_ACCESS_KEY, config.R2_BUCKET])


def client():
    """The S3 client for the R2 bucket."""
    import boto3  # Imported here, so the app runs without R2.
    from botocore.config import Config

    return boto3.client("s3", endpoint_url=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
                        aws_access_key_id=config.R2_ACCESS_KEY_ID, aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
                        region_name="auto",
                        config=Config(signature_version="s3v4", request_checksum_calculation="when_required",
                                      response_checksum_validation="when_required"))


def key_for(tenant_id: str, rel: str) -> str:
    """The R2 key of a file, from its path inside the business folder."""
    return f"{PREFIX}/{tenant_id}/{rel}"


def content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def public_url(path: Path, key: str, tenant_id: str | None = None) -> str:
    """Upload `path` (if needed) and return a link the platforms can download. `key`: <business>/<path>."""
    if not r2_on():
        raise ServiceError("No media storage. Set the R2 keys (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
                           "R2_SECRET_ACCESS_KEY, R2_BUCKET).")
    key = f"{PREFIX}/{key}"
    s3 = client()
    try:
        s3.head_object(Bucket=config.R2_BUCKET, Key=key)  # Already kept by media.py: no second upload.
    except Exception:  # noqa: BLE001 - not there yet
        s3.upload_file(str(path), config.R2_BUCKET, key, ExtraArgs={"ContentType": content_type(path)})
    if config.R2_PUBLIC_BASE_URL:
        return config.R2_PUBLIC_BASE_URL.rstrip("/") + "/" + key
    return s3.generate_presigned_url("get_object", Params={"Bucket": config.R2_BUCKET, "Key": key},
                                     ExpiresIn=7 * 24 * 3600)
