"""Media storage: gives each media file a public URL, so the social platforms can fetch it.

  Cloudflare R2  when R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY and R2_BUCKET are set (S3 API).
The media library on disk stays the master copy (one folder per post).
"""
import mimetypes
from pathlib import Path

from .. import config
from .http import ServiceError


def r2_on() -> bool:
    return all([config.R2_ACCOUNT_ID, config.R2_ACCESS_KEY_ID, config.R2_SECRET_ACCESS_KEY, config.R2_BUCKET])


def public_url(path: Path, key: str, tenant_id: str | None = None) -> str:
    """Upload `path` and return a URL. `key` is the name in storage, e.g. <tenant>/<post>/<file>."""
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if r2_on():
        return _r2(path, key, content_type)
    raise ServiceError("No media storage. Set the R2 keys (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
                       "R2_SECRET_ACCESS_KEY, R2_BUCKET).")


def _r2(path: Path, key: str, content_type: str) -> str:
    import boto3  # Imported here, so the app runs without R2.
    from botocore.config import Config

    s3 = boto3.client("s3", endpoint_url=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
                      aws_access_key_id=config.R2_ACCESS_KEY_ID, aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
                      region_name="auto",
                      config=Config(signature_version="s3v4", request_checksum_calculation="when_required",
                                    response_checksum_validation="when_required"))
    s3.upload_file(str(path), config.R2_BUCKET, key, ExtraArgs={"ContentType": content_type})
    if config.R2_PUBLIC_BASE_URL:
        return config.R2_PUBLIC_BASE_URL.rstrip("/") + "/" + key
    return s3.generate_presigned_url("get_object", Params={"Bucket": config.R2_BUCKET, "Key": key},
                                     ExpiresIn=7 * 24 * 3600)
