from __future__ import annotations

import base64
import re
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings
from app.paths import PathJailError, resolve_in_home
from app.workspace import ensure_workspace

MAX_BYTES = 6 * 1024 * 1024
MAX_ITEMS = 8
_SAFE = re.compile(r"[^\w.\-]+")


def decode_b64(raw: str) -> bytes:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty attachment")
    if text.lower().startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    return base64.b64decode(text, validate=False)


def save_items(items: list[dict[str, Any]], *, kind: str) -> list[dict[str, str]]:
    ensure_workspace()
    out: list[dict[str, str]] = []
    for item in items[:MAX_ITEMS]:
        name = str(item.get("name") or ("paste.png" if kind == "image" else "file"))
        data = decode_b64(str(item.get("data") or ""))
        if not data:
            raise ValueError("empty attachment")
        if len(data) > MAX_BYTES:
            raise ValueError(f"{name} exceeds {MAX_BYTES} bytes")
        dest = _unique_path(name)
        dest.write_bytes(data)
        rel = str(dest.relative_to(settings.home))
        out.append(
            {
                "kind": kind,
                "name": dest.name,
                "path": rel,
                "mime_type": str(item.get("mime_type") or _mime_of(dest.name)),
            }
        )
    return out


def get_file(name: str) -> tuple[bytes, str]:
    path = resolve_in_home(Path(name).name, base=settings.inbox_dir)
    if not path.is_file():
        raise FileNotFoundError(name)
    return path.read_bytes(), _mime_of(path.name)


def _unique_path(name: str) -> Path:
    base = _SAFE.sub("_", Path(name or "file").name).strip("._") or "file"
    dest = resolve_in_home(f"{int(time.time())}-{uuid4().hex[:6]}-{base[:80]}", base=settings.inbox_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def _mime_of(name: str) -> str:
    ext = Path(name).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".json": "application/json",
    }.get(ext, "application/octet-stream")


def assert_quota(n: int) -> None:
    if n > MAX_ITEMS:
        raise ValueError(f"at most {MAX_ITEMS} attachments")
    if n < 0:
        raise PathJailError("invalid attachment count")
