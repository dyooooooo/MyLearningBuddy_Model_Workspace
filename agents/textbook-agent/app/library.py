from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.paths import PathJailError, resolve_in_home
from app.workspace import ensure_workspace


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    cleaned = "".join(ch for ch in (value or "").strip() if ch.isalnum() or ch in "-_")
    if not cleaned:
        raise ValueError("id required")
    return cleaned


def textbooks_root() -> Path:
    ensure_workspace()
    return settings.textbooks_dir


def book_dir(book_id: str) -> Path:
    dest = resolve_in_home(_safe_id(book_id), base=textbooks_root())
    if dest == textbooks_root().resolve():
        raise ValueError("id required")
    return dest


def _meta_path(book_id: str) -> Path:
    return book_dir(book_id) / "meta.json"


def _toc_path(book_id: str) -> Path:
    return book_dir(book_id) / "toc.json"


def _pdf_path(book_id: str) -> Path:
    folder = book_dir(book_id)
    named = folder / "book.pdf"
    if named.is_file():
        return named
    pdfs = sorted(folder.glob("*.pdf"))
    return pdfs[0] if pdfs else named


def _page_path(book_id: str, page_no: int) -> Path:
    return book_dir(book_id) / "pages" / f"{int(page_no):04d}.md"


def _image_path(book_id: str, page_no: int) -> Path:
    return book_dir(book_id) / "images" / f"p{int(page_no):04d}.jpg"


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return data if isinstance(data, type(default)) or (isinstance(default, dict) and isinstance(data, dict)) else default


def read_meta(book_id: str) -> dict[str, Any] | None:
    path = _meta_path(book_id)
    if not path.is_file():
        folder = book_dir(book_id)
        if not folder.is_dir():
            return None
        return _default_meta(book_id, folder)
    data = _read_json(path, {})
    if not isinstance(data, dict) or not data:
        return None
    data.setdefault("id", book_id)
    return data


def write_meta(book_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    folder = book_dir(book_id)
    folder.mkdir(parents=True, exist_ok=True)
    meta = dict(meta)
    meta["id"] = book_id
    meta["updated_at"] = _now()
    path = folder / "meta.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def _default_meta(book_id: str, folder: Path) -> dict[str, Any]:
    pdf = next(iter(sorted(folder.glob("*.pdf"))), None)
    name = pdf.name if pdf else "book.pdf"
    return {
        "id": book_id,
        "label": folder.name,
        "filename": name,
        "subject": "math",
        "grade_band": "hs",
        "textbook_hint": folder.name,
        "status": "uploaded",
        "page_count": len(list((folder / "pages").glob("*.md"))) if (folder / "pages").is_dir() else 0,
        "error": "",
        "is_default": False,
        "created_at": _now(),
        "updated_at": _now(),
    }


def empty_toc() -> dict[str, Any]:
    return {"entries": [], "print_to_pdf": {}, "toc_pages": []}


def read_toc(book_id: str) -> dict[str, Any]:
    data = _read_json(_toc_path(book_id), empty_toc())
    if not isinstance(data, dict):
        return empty_toc()
    data.setdefault("entries", [])
    data.setdefault("print_to_pdf", {})
    data.setdefault("toc_pages", [])
    return data


def write_toc(book_id: str, toc: dict[str, Any]) -> dict[str, Any]:
    book_dir(book_id).mkdir(parents=True, exist_ok=True)
    payload = {
        "entries": toc.get("entries") if isinstance(toc.get("entries"), list) else [],
        "print_to_pdf": toc.get("print_to_pdf") if isinstance(toc.get("print_to_pdf"), dict) else {},
        "toc_pages": toc.get("toc_pages") if isinstance(toc.get("toc_pages"), list) else [],
    }
    _toc_path(book_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def book_to_dict(meta: dict[str, Any]) -> dict[str, Any]:
    book_id = str(meta.get("id") or "")
    toc = read_toc(book_id) if book_id else empty_toc()
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


def list_books() -> list[dict[str, Any]]:
    root = textbooks_root()
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            resolve_in_home(folder)
        except PathJailError:
            continue
        meta = read_meta(folder.name)
        if meta:
            rows.append(book_to_dict(meta))
    rows.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return rows


def get_book(book_id: str) -> dict[str, Any] | None:
    try:
        meta = read_meta(_safe_id(book_id))
    except (ValueError, PathJailError):
        return None
    return book_to_dict(meta) if meta else None


def _clear_defaults(except_id: str) -> None:
    for row in list_books():
        other = str(row.get("id") or "")
        if not other or other == except_id or not row.get("is_default"):
            continue
        meta = read_meta(other)
        if not meta:
            continue
        meta["is_default"] = False
        write_meta(other, meta)


def create_book(
    *,
    pdf_bytes: bytes,
    filename: str,
    book_id: str = "",
    label: str = "",
    subject: str = "math",
    grade_band: str = "hs",
    textbook_hint: str = "",
) -> dict[str, Any]:
    if not pdf_bytes:
        raise ValueError("file empty")
    name = Path(filename or "book.pdf").name
    if not name.lower().endswith(".pdf"):
        raise ValueError("only pdf")
    sid = _safe_id(book_id or uuid.uuid4().hex)
    folder = book_dir(sid)
    if folder.exists() and any(folder.iterdir()):
        raise ValueError("book exists")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "book.pdf").write_bytes(pdf_bytes)
    hint = (textbook_hint or label or "").strip()
    if hint.lower().endswith(".pdf"):
        hint = label.strip() if label and not label.lower().endswith(".pdf") else sid
    now = _now()
    meta = {
        "id": sid,
        "label": (label or hint or name).strip(),
        "filename": name,
        "subject": (subject or "math").strip() or "math",
        "grade_band": (grade_band or "hs").strip() or "hs",
        "textbook_hint": hint or (label or name),
        "status": "uploaded",
        "page_count": 0,
        "error": "",
        "is_default": False,
        "created_at": now,
        "updated_at": now,
    }
    write_meta(sid, meta)
    write_toc(sid, empty_toc())
    return book_to_dict(meta)


def patch_book(
    book_id: str,
    *,
    label: str | None = None,
    textbook_hint: str | None = None,
    subject: str | None = None,
    grade_band: str | None = None,
    is_default: bool | None = None,
    status: str | None = None,
    error: str | None = None,
    page_count: int | None = None,
) -> dict[str, Any] | None:
    sid = _safe_id(book_id)
    meta = read_meta(sid)
    if meta is None:
        return None
    if label is not None:
        meta["label"] = label.strip()
    if textbook_hint is not None:
        hint = textbook_hint.strip()
        meta["textbook_hint"] = hint if hint and not hint.lower().endswith(".pdf") else meta.get("label") or sid
    if subject is not None:
        meta["subject"] = subject.strip() or meta.get("subject") or "math"
    if grade_band is not None:
        meta["grade_band"] = grade_band.strip() or meta.get("grade_band") or "hs"
    if is_default is True:
        _clear_defaults(sid)
        meta["is_default"] = True
    elif is_default is False:
        meta["is_default"] = False
    if status is not None:
        meta["status"] = status
    if error is not None:
        meta["error"] = error
    if page_count is not None:
        meta["page_count"] = int(page_count)
    write_meta(sid, meta)
    return book_to_dict(meta)


def delete_book(book_id: str) -> bool:
    try:
        folder = book_dir(_safe_id(book_id))
    except (ValueError, PathJailError):
        return False
    if not folder.is_dir():
        return False
    shutil.rmtree(folder)
    return True


def reimport(book_id: str) -> dict[str, Any] | None:
    sid = _safe_id(book_id)
    meta = read_meta(sid)
    if meta is None:
        return None
    if not _pdf_path(sid).is_file():
        raise ValueError("pdf missing")
    meta["status"] = "uploaded"
    meta["error"] = ""
    write_meta(sid, meta)
    return book_to_dict(meta)


def get_page_markdown(book_id: str, page_no: int) -> str | None:
    path = _page_path(_safe_id(book_id), page_no)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def get_page_image(book_id: str, page_no: int) -> bytes | None:
    path = _image_path(_safe_id(book_id), page_no)
    if not path.is_file():
        return None
    return path.read_bytes()


def write_page(book_id: str, page_no: int, markdown: str, image: bytes | None = None) -> None:
    sid = _safe_id(book_id)
    md = _page_path(sid, page_no)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(markdown or "", encoding="utf-8")
    if image is not None:
        img = _image_path(sid, page_no)
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(image)


def mark_failed(book_id: str, error: str) -> None:
    try:
        patch_book(book_id, status="failed", error=(error or "")[:4000])
    except (ValueError, PathJailError):
        return
