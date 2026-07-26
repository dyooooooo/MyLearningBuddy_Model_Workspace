from __future__ import annotations

import io
import logging

from minio import Minio
from minio.error import S3Error

from app.config import settings

logger = logging.getLogger(__name__)
_client: Minio | None = None


def get_minio() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
    return _client


def minio_ok() -> bool:
    try:
        get_minio().bucket_exists(settings.minio_bucket)
        return True
    except Exception:
        return False


def get_object_bytes(key: str) -> bytes | None:
    client = get_minio()
    try:
        resp = client.get_object(settings.minio_bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()
    except S3Error as e:
        # NoSuchKey is an expected "not found" answer (e.g. read-before-write
        # probes), not a failure — stay quiet; log real errors only.
        if getattr(e, "code", "") != "NoSuchKey":
            logger.warning("MinIO get_object failed key=%s: %s", key, e)
        return None


def delete_objects_by_prefix(prefix: str) -> int:
    """Delete every object under ``prefix`` in the import bucket. Returns count removed."""
    normalized = (prefix or "").lstrip("/")
    if not normalized:
        return 0
    if not normalized.endswith("/"):
        normalized += "/"
    client = get_minio()
    removed = 0
    try:
        for obj in client.list_objects(
            settings.minio_bucket, prefix=normalized, recursive=True
        ):
            try:
                client.remove_object(settings.minio_bucket, obj.object_name)
                removed += 1
            except S3Error as e:
                logger.warning(
                    "MinIO remove_object failed key=%s: %s", obj.object_name, e
                )
    except S3Error as e:
        logger.warning("MinIO list_objects failed prefix=%s: %s", normalized, e)
    return removed


def put_import_bytes(key: str, data: bytes, content_type: str = "application/pdf") -> str:
    client = get_minio()
    if not client.bucket_exists(settings.minio_bucket):
        client.make_bucket(settings.minio_bucket)
    client.put_object(
        settings.minio_bucket,
        key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return key
