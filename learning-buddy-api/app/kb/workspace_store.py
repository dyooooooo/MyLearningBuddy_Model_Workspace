"""Agent workspace textbooks/ — file layout shared with textbook-agent."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_id(value: str) -> str:
    cleaned = "".join(ch for ch in (value or "").strip() if ch.isalnum() or ch in "-_")
    if not cleaned:
        raise ValueError("id required")
    return cleaned


def textbooks_root() -> Path:
    return Path(settings.agent_textbooks_dir)


def book_dir(book_id: str) -> Path:
    root = textbooks_root().resolve()
    dest = (root / safe_id(book_id)).resolve()
    if dest != root and root not in dest.parents:
        raise ValueError("path outside textbooks")
    return dest


def meta_path(book_id: str) -> Path:
    return book_dir(book_id) / "meta.json"


def toc_path(book_id: str) -> Path:
    return book_dir(book_id) / "toc.json"


def pdf_path(book_id: str) -> Path:
    folder = book_dir(book_id)
    named = folder / "book.pdf"
    if named.is_file():
        return named
    pdfs = sorted(folder.glob("*.pdf"))
    return pdfs[0] if pdfs else named


def page_path(book_id: str, page_no: int) -> Path:
    return book_dir(book_id) / "pages" / f"{int(page_no):04d}.md"


def image_path(book_id: str, page_no: int) -> Path:
    return book_dir(book_id) / "images" / f"p{int(page_no):04d}.jpg"


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return data


def read_meta(book_id: str) -> dict[str, Any] | None:
    path = meta_path(book_id)
    if not path.is_file():
        return None
    data = read_json(path, {})
    return data if isinstance(data, dict) and data else None


def write_meta(book_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    folder = book_dir(book_id)
    folder.mkdir(parents=True, exist_ok=True)
    meta = dict(meta)
    meta["id"] = book_id
    meta["updated_at"] = _now()
    meta_path(book_id).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def read_toc(book_id: str) -> dict[str, Any]:
    data = read_json(toc_path(book_id), {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("entries", [])
    data.setdefault("print_to_pdf", {})
    data.setdefault("toc_pages", [])
    return data


def write_toc(book_id: str, toc: dict[str, Any]) -> None:
    book_dir(book_id).mkdir(parents=True, exist_ok=True)
    toc_path(book_id).write_text(json.dumps(toc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_page(book_id: str, page_no: int, markdown: str, image: bytes | None = None) -> None:
    md = page_path(book_id, page_no)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(markdown or "", encoding="utf-8")
    if image is not None:
        img = image_path(book_id, page_no)
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(image)


def list_metas() -> list[dict[str, Any]]:
    root = textbooks_root()
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            meta = read_meta(folder.name)
        except ValueError:
            continue
        if meta:
            rows.append(meta)
    return rows


def book_to_dict(meta: dict[str, Any]) -> dict[str, Any]:
    book_id = str(meta.get("id") or "")
    toc = read_toc(book_id) if book_id else {}
    entries = toc.get("entries") if isinstance(toc.get("entries"), list) else []
    return {
        "id": book_id,
        "label": str(meta.get("label") or ""),
        "filename": str(meta.get("filename") or "book.pdf"),
        "subject": str(meta.get("subject") or "math"),
        "grade_band": str(meta.get("grade_band") or "hs"),
        "textbook_hint": str(meta.get("textbook_hint") or meta.get("label") or ""),
        "minio_key": f"textbooks/{book_id}/book.pdf" if book_id else "",
        "status": str(meta.get("status") or "uploaded"),
        "page_count": int(meta.get("page_count") or 0),
        "error": str(meta.get("error") or ""),
        "is_default": bool(meta.get("is_default")),
        "toc_entry_count": len(entries),
        "created_at": str(meta.get("created_at") or ""),
        "updated_at": str(meta.get("updated_at") or ""),
    }
