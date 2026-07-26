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
        if not _client.bucket_exists(settings.minio_bucket):
            _client.make_bucket(settings.minio_bucket)
    return _client


def minio_ok() -> bool:
    try:
        get_minio().bucket_exists(settings.minio_bucket)
        return True
    except Exception:
        return False


def frame_key(student_id: str, session_id: str, frame_id: str) -> str:
    return f"{student_id}/{session_id}/{frame_id}.jpg"


def put_frame(student_id: str, session_id: str, frame_id: str, data: bytes) -> None:
    client = get_minio()
    key = frame_key(student_id, session_id, frame_id)
    client.put_object(
        settings.minio_bucket,
        key,
        io.BytesIO(data),
        length=len(data),
        content_type="image/jpeg",
    )


def get_frame(student_id: str, session_id: str, frame_id: str) -> bytes | None:
    client = get_minio()
    key = frame_key(student_id, session_id, frame_id)
    try:
        resp = client.get_object(settings.minio_bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()
    except S3Error:
        return None


def put_import_bytes(key: str, data: bytes, content_type: str = "application/pdf") -> str:
    client = get_minio()
    client.put_object(
        settings.minio_bucket,
        key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return key
