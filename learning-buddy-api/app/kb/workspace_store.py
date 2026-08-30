"""Agent workspace textbooks/ — file layout shared with textbook-agent."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_CATEGORIES = {
    "textbook": "textbook",
    "教材": "textbook",
    "knowledge": "knowledge",
    "知识点": "knowledge",
    "syllabus": "syllabus",
    "考纲": "syllabus",
    "material": "material",
    "资料": "material",
    "homework": "homework",
    "作业": "homework",
}


def normalize_category(raw: Any, fallback: str = "textbook") -> str:
    text = str(raw or "").strip()
    if text:
        hit = _CATEGORIES.get(text.lower()) or _CATEGORIES.get(text)
        if hit:
            return hit
    fb = str(fallback or "").strip()
    return _CATEGORIES.get(fb.lower()) or _CATEGORIES.get(fb) or "textbook"


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


def mineru_toc_path(book_id: str) -> Path:
    return book_dir(book_id) / "mineru_toc.json"


def mineru_dir(book_id: str) -> Path:
    """Official MinerU zip JSON, kept next to the book (not MinIO corpus/)."""
    return book_dir(book_id) / "mineru"


def toc_agent_path(book_id: str) -> Path:
    return book_dir(book_id) / "toc_agent.json"


def toc_extract_cursor_path(book_id: str) -> Path:
    return book_dir(book_id) / "toc_extract.json"


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
    data.setdefault("source", "")
    return data


def write_toc(book_id: str, toc: dict[str, Any]) -> None:
    book_dir(book_id).mkdir(parents=True, exist_ok=True)
    toc_path(book_id).write_text(json.dumps(toc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_mineru_toc(book_id: str, toc: dict[str, Any]) -> dict[str, Any]:
    """Persist MinerU's catalog-page hints for the workspace TOC builder."""
    pages: list[int] = []
    for raw in toc.get("toc_pages") or []:
        try:
            page = int(raw)
        except (TypeError, ValueError):
            continue
        if page > 0:
            pages.append(page)
    mapping = toc.get("print_to_pdf") if isinstance(toc.get("print_to_pdf"), dict) else {}
    payload = {
        "toc_pages": sorted(set(pages)),
        "print_to_pdf": mapping,
    }
    folder = book_dir(book_id)
    folder.mkdir(parents=True, exist_ok=True)
    mineru_toc_path(book_id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def reset_derived(book_id: str) -> None:
    """Drop pages, images, MinerU JSON, and the table of contents. Keep the PDF and meta."""
    folder = book_dir(book_id)
    for name in ("pages", "images", "mineru"):
        path = folder / name
        if path.is_dir():
            shutil.rmtree(path)
    for extra in (
        mineru_toc_path(book_id),
        toc_agent_path(book_id),
        toc_extract_cursor_path(book_id),
        book_dir(book_id) / "layout_refine.json",
    ):
        if extra.is_file():
            extra.unlink()
    write_toc(book_id, {"entries": [], "print_to_pdf": {}, "toc_pages": []})


def delete_book_dir(book_id: str) -> bool:
    folder = book_dir(book_id)
    if not folder.is_dir():
        return False
    shutil.rmtree(folder)
    return True


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
        "category": normalize_category(meta.get("category")),
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
