"""Normalize uploads: any supported raster → JPEG, EXIF rotation, optional downscale."""

from __future__ import annotations

import io
import logging
from pathlib import PurePath
from typing import Any

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from app.image_utils import validate_jpeg

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_EDGE_PX = 4096
JPEG_QUALITY = 92

# Input formats we accept (detected by magic bytes and/or Pillow).
SUPPORTED_INPUT_FORMATS = (
    "JPEG",
    "PNG",
    "HEIC",
    "WEBP",
    "GIF",
    "BMP",
    "TIFF",
)

SUPPORTED_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp",
    ".gif", ".bmp", ".tif", ".tiff",
})

OUTPUT_MIME = "image/jpeg"

_heif_registered = False


def _register_heif() -> None:
    global _heif_registered
    if _heif_registered:
        return
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
        _heif_registered = True
        logger.info("HEIF/HEIC opener registered (pillow-heif)")
    except ImportError:
        logger.warning("pillow-heif not installed; HEIC uploads require pillow-heif + libheif")


def detect_format(data: bytes) -> str | None:
    if len(data) >= 3 and data[0:3] == b"\xff\xd8\xff":
        return "JPEG"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"avif"):
            return "HEIC"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    if len(data) >= 2 and data[:2] == b"BM":
        return "BMP"
    if len(data) >= 4 and data[:4] in (b"II*\x00", b"MM\x00*"):
        return "TIFF"
    return None


def is_allowed_upload(filename: str | None, content_type: str | None) -> bool:
    if content_type and content_type.startswith("image/"):
        return True
    if filename:
        ext = PurePath(filename).suffix.lower()
        return ext in SUPPORTED_EXTENSIONS
    return False


def guess_mime(data: bytes, content_type: str | None, filename: str | None) -> str:
    detected = detect_format(data)
    if detected == "JPEG":
        return "image/jpeg"
    if detected == "PNG":
        return "image/png"
    if detected == "HEIC":
        return "image/heic"
    if detected == "WEBP":
        return "image/webp"
    if content_type and content_type.startswith("image/"):
        return content_type
    if filename:
        ext = PurePath(filename).suffix.lower()
        ext_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".heic": "image/heic",
            ".heif": "image/heif",
            ".webp": "image/webp",
        }
        if ext in ext_map:
            return ext_map[ext]
    return "image/jpeg"


def normalize_image(
    data: bytes,
    mime: str | None = None,
    filename: str | None = None,
) -> tuple[bytes, str, dict[str, Any]]:
    """Return JPEG bytes + metadata suitable for Pillow bbox math and vLLM vision."""
    _register_heif()

    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
        )

    detected = detect_format(data)
    source = detected or mime or filename or "unknown"

    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB",):
                im = im.convert("RGB")
            w, h = im.size
            max_edge = max(w, h)
            if max_edge > MAX_EDGE_PX:
                scale = MAX_EDGE_PX / max_edge
                im = im.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.Resampling.LANCZOS,
                )
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            out = buf.getvalue()
    except UnidentifiedImageError as exc:
        if detected == "HEIC":
            raise HTTPException(
                status_code=400,
                detail=(
                    "HEIC/HEIF image detected but cannot be decoded. "
                    "Ensure pillow-heif and libheif are installed in c2-gateway."
                ),
            ) from exc
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or unsupported image: {exc}",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Image preprocessing failed: {exc}",
        ) from exc

    if detected and detected != "JPEG":
        logger.info(
            "Converted %s (%s, %d bytes) -> JPEG (%d bytes, %dx%d)",
            filename or "upload",
            source,
            len(data),
            len(out),
            im.size[0],
            im.size[1],
        )
    meta = {
        "source_format": detected or "UNKNOWN",
        "width": im.size[0],
        "height": im.size[1],
        "bytes_in": len(data),
        "bytes_out": len(out),
    }
    return out, OUTPUT_MIME, meta


async def read_normalized_upload(file: UploadFile) -> tuple[bytes, str]:
    data, mime, _meta = await read_normalized_upload_with_meta(file)
    return data, mime


async def read_normalized_upload_with_meta(
    file: UploadFile,
    *,
    already_normalized: bool = False,
) -> tuple[bytes, str, dict[str, Any]]:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    if not is_allowed_upload(file.filename, file.content_type):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )
    mime = guess_mime(data, file.content_type, file.filename)
    if already_normalized and detect_format(data) == "JPEG":
        try:
            w, h = validate_jpeg(data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid normalized JPEG") from exc
        meta = {
            "source_format": "JPEG",
            "width": w,
            "height": h,
            "bytes_in": len(data),
            "bytes_out": len(data),
            "fast_path": True,
            "filename": file.filename,
        }
        return data, OUTPUT_MIME, meta
    out, out_mime, meta = normalize_image(data, mime, file.filename)
    meta["filename"] = file.filename
    meta["fast_path"] = False
    return out, out_mime, meta
