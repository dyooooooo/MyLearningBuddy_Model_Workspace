"""Shared image byte helpers."""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError


def jpeg_size(image_bytes: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(image_bytes)) as im:
        return im.size


def validate_jpeg(image_bytes: bytes) -> tuple[int, int]:
    try:
        return jpeg_size(image_bytes)
    except UnidentifiedImageError as exc:
        raise ValueError("Invalid JPEG") from exc
