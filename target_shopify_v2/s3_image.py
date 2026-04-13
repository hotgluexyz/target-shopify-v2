"""Utilities for uploading base64 product images to S3 and generating pre-signed URLs."""

import base64
import hashlib
import logging
import mimetypes
import os
import re

import boto3

logger = logging.getLogger(__name__)

BASE64_PREFIX = "data:image/"


def is_base64_image(value: str) -> bool:
    return isinstance(value, str) and value.startswith(BASE64_PREFIX)


def has_base64_images(record: dict) -> bool:
    """Return True if the product record or any of its variants have image_blobs."""
    if record.get("image_blobs"):
        return True
    return any(
        variant.get("image_blobs")
        for variant in (record.get("variants") or [])
    )


def get_s3_client(config: dict) -> boto3.client:
    """Build an S3 client. Config keys take priority over environment variables."""
    return boto3.client(
        "s3",
        aws_access_key_id=config.get("aws_access_key_id") or os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=config.get("aws_secret_access_key") or os.environ.get("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=config.get("aws_session_token") or os.environ.get("AWS_SESSION_TOKEN"),
        region_name="us-east-1",
    )


def _resolve_bucket_and_prefix(config: dict) -> tuple:
    """Return (bucket, key_prefix) from config, falling back to job env vars."""
    bucket = config.get("s3_bucket") or os.environ.get("ENV_ID")
    key_prefix = config.get("s3_key_prefix") or os.environ.get("JOB_ROOT", "local")
    if not bucket:
        raise ValueError(
            "No S3 bucket configured. Set 's3_bucket' in the target config "
            "or ensure the ENV_ID environment variable is set."
        )
    return bucket, key_prefix


def upload_base64_image(s3_client, data_uri: str, config: dict) -> tuple:
    """
    Decode a base64 data URI, upload it to S3, and return a pre-signed URL.

    Returns (bucket, key, presigned_url).
    The caller is responsible for deleting the object after use.
    """
    match = re.match(r"data:([^;]+);base64,(.+)", data_uri, re.DOTALL)
    if not match:
        raise ValueError(f"Invalid base64 data URI: {data_uri[:80]}...")

    mime_type = match.group(1)
    image_bytes = base64.b64decode(match.group(2))

    ext = mimetypes.guess_extension(mime_type) or ".jpg"
    if ext in (".jpe", ".jpeg"):
        ext = ".jpg"

    # Content-addressed key: same image always maps to the same S3 object.
    # This makes re-uploads idempotent when the target restarts after a failure.
    content_hash = hashlib.sha256(image_bytes).hexdigest()[:16]
    bucket, key_prefix = _resolve_bucket_and_prefix(config)
    key = f"{key_prefix}/temp-images/{content_hash}{ext}"

    s3_client.put_object(Bucket=bucket, Key=key, Body=image_bytes, ContentType=mime_type)

    # 10-minute expiry: enough for Shopify to process media asynchronously
    presigned_url = s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=600,
    )
    logger.info(f"Uploaded base64 image to s3://{bucket}/{key}")
    return bucket, key, presigned_url


def resolve_blobs_to_urls(image_blobs: list, s3_client, config: dict, uploaded_keys: list) -> list:
    """
    Upload base64 data URIs from image_blobs to S3 and return pre-signed URLs.

    Uploaded keys are appended to uploaded_keys so the caller can clean them up
    after the Shopify mutation completes.
    """
    resolved = []
    for blob in image_blobs:
        if is_base64_image(blob):
            bucket, key, presigned_url = upload_base64_image(s3_client, blob, config)
            uploaded_keys.append((bucket, key))
            resolved.append(presigned_url)
    return resolved


def cleanup_temp_images(s3_client, uploaded_keys: list) -> None:
    """Delete temporary S3 objects created during image upload. Errors are non-fatal."""
    for bucket, key in uploaded_keys:
        try:
            s3_client.delete_object(Bucket=bucket, Key=key)
            logger.info(f"Deleted temp image s3://{bucket}/{key}")
        except Exception as e:
            logger.warning(f"Failed to delete temp image s3://{bucket}/{key}: {e}")
