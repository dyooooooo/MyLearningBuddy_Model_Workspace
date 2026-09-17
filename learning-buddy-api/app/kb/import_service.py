"""Run MinerU and write per-page markdown into the Agent workspace."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from typing import Any

from app.kb.markdown import _plain_text, blocks_to_page_markdown, group_blocks_by_page
from app.kb.mid import centered_texts_from_blocks, tag_source, write_mid
from app.kb.mineru_client import parse_pdf, probe_mineru_blocks
from app.kb.pdf_pages import open_pdf
from app.kb.pdf_slice import slice_pdf_pages
from app.kb.settings import require_export_config
from app.kb.toc import extract_toc, refine_toc
from app.kb.workspace_store import (
    book_dir,
    book_to_dict,
    delete_book_dir,
    list_metas,
    normalize_category,
    mineru_dir,
    pdf_path,
    read_meta,
    read_toc,
    reset_derived,
    safe_id,
    write_meta,
    write_mineru_toc,
    write_page,
    write_toc,
)

logger = logging.getLogger(__name__)

IDENTIFY_PAGES = 3
IDENTIFY_PREVIEW = "identify_preview.md"


class KbBookGone(RuntimeError):
    """Book was deleted while convert was queued or running."""


def _finalize_toc(book_id: str, toc: dict[str, Any], page_texts: dict[int, str]) -> dict[str, Any]:
    refined = refine_toc(toc, page_texts)
    write_toc(book_id, refined)
    return refined


def _assert_book_alive(book_id: str) -> None:
    from app.clients.redis_client import is_kb_book_revoked

    if read_meta(book_id) is None or is_kb_book_revoked(book_id):
        raise KbBookGone(book_id)


def _mineru_preview_text(markdown: str, blocks: list[dict[str, Any]] | None) -> str:
    text = (markdown or "").strip()
    if text:
        return text
    parts: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        one = _plain_text(block)
        if one:
            parts.append(one)
    return "\n".join(parts).strip()


def identify_kb_book(book_id: str, pages: int = IDENTIFY_PAGES) -> dict[str, Any]:
    """MinerU the first pages only. Write preview text; do not guess subject."""
    require_export_config()
    _assert_book_alive(book_id)
    meta = read_meta(book_id)
    if meta is None:
        raise KbBookGone(book_id)
    path = pdf_path(book_id)
    if not path.is_file():
        raise RuntimeError(f"PDF not found: {path}")
    raw = path.read_bytes()
    if not raw:
        raise RuntimeError(f"PDF empty: {path}")
    keep = max(int(pages or IDENTIFY_PAGES), 1)
    try:
        preview = slice_pdf_pages(raw, keep)
    except Exception:
        logger.warning("slice first pages failed id=%s", book_id, exc_info=True)
        preview = raw
    parsed = parse_pdf(
        preview,
        filename=f"preview-{keep}.pdf",
        book_id=book_id,
        page_ranges=f"1-{keep}",
        upload_figures=False,
    )
    _assert_book_alive(book_id)
    text = _mineru_preview_text(parsed.markdown or "", list(parsed.blocks or []))
    folder = book_dir(book_id)
    folder.mkdir(parents=True, exist_ok=True)
    rel = f"textbooks/{safe_id(book_id)}/{IDENTIFY_PREVIEW}"
    (folder / IDENTIFY_PREVIEW).write_text(text or "（MinerU 未抽出文字）", encoding="utf-8")
    logger.info("kb book preview ready id=%s pages=%s chars=%s", book_id, keep, len(text))
    out = book_to_dict(read_meta(book_id) or meta)
    out["pages_used"] = keep
    out["preview"] = text
    out["preview_path"] = rel
    return out


def _write_mineru_manifest(
    book_id: str,
    *,
    page_ranges: str,
    block_count: int,
    probe: dict[str, Any],
) -> dict[str, Any]:
    dest = mineru_dir(book_id)
    dest.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "book_id": book_id,
        "page_ranges": page_ranges or "",
        "block_count": block_count,
        "files": sorted(p.name for p in dest.iterdir() if p.is_file()),
        "probe": probe,
    }
    (dest / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def dump_mineru_json(book_id: str, page_ranges: str = "") -> dict[str, Any]:
    """Re-run MinerU and keep official JSON under textbooks/<id>/mineru/.

    Does not rewrite pages or images.
    """
    require_export_config()
    _assert_book_alive(book_id)
    meta = read_meta(book_id)
    if meta is None:
        raise KbBookGone(book_id)
    path = pdf_path(book_id)
    if not path.is_file():
        raise RuntimeError(f"PDF not found: {path}")
    raw = path.read_bytes()
    if not raw:
        raise RuntimeError(f"PDF empty: {path}")
    dest = mineru_dir(book_id)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    ranges = (page_ranges or "").strip()
    parsed = parse_pdf(
        raw,
        filename=str(meta.get("filename") or "book.pdf"),
        book_id=book_id,
        page_ranges=ranges,
        persist_dir=dest,
        upload_figures=False,
    )
    probe = probe_mineru_blocks(list(parsed.blocks or []))
    out = _write_mineru_manifest(
        book_id,
        page_ranges=ranges,
        block_count=len(parsed.blocks or []),
        probe=probe,
    )
    logger.info(
        "kb mineru dump id=%s ranges=%s blocks=%s files=%s",
        book_id,
        ranges or "all",
        out["block_count"],
        out["files"],
    )
    return out


def process_kb_book(book_id: str) -> dict[str, Any]:
    require_export_config()
    _assert_book_alive(book_id)
    meta = read_meta(book_id)
    if meta is None:
        raise KbBookGone(book_id)
    reset_derived(book_id)
    _assert_book_alive(book_id)
    meta["status"] = "converting"
    meta["error"] = ""
    write_meta(book_id, meta)
    try:
        from app.kb.agent_notify import notify_import_converting

        notify_import_converting(book_id, conversation_id=str(meta.get("conversation_id") or ""))
    except Exception:
        logger.warning("import converting notify failed id=%s", book_id, exc_info=True)

    path = pdf_path(book_id)
    if not path.is_file():
        raise RuntimeError(f"PDF not found: {path}")
    raw = path.read_bytes()
    if not raw:
        raise RuntimeError(f"PDF empty: {path}")

    parsed = parse_pdf(
        raw,
        filename=str(meta.get("filename") or "book.pdf"),
        book_id=book_id,
        persist_dir=mineru_dir(book_id),
    )
    try:
        from app.kb.figure_recrop import recrop_figures

        recrop_figures(book_id, raw, list(parsed.blocks or []))
    except Exception:
        logger.warning("figure recrop skipped id=%s", book_id, exc_info=True)
    _write_mineru_manifest(
        book_id,
        page_ranges="",
        block_count=len(parsed.blocks or []),
        probe=probe_mineru_blocks(list(parsed.blocks or [])),
    )
    grouped = group_blocks_by_page(list(parsed.blocks or []))
    toc = extract_toc(list(parsed.blocks or []))
    page_texts: dict[int, str] = {}

    with open_pdf(raw) as pages:
        total = pages.page_count()
        if total < 1:
            total = max(grouped.keys(), default=0)
        for page_no in range(1, total + 1):
            _assert_book_alive(book_id)
            page_md = blocks_to_page_markdown(
                grouped.get(page_no, []),
                print_to_pdf=toc.get("print_to_pdf") if isinstance(toc.get("print_to_pdf"), dict) else None,
                book_id=book_id,
            )
            if not page_md.strip() and page_no == 1 and not grouped:
                page_md = (parsed.markdown or "").strip()
            page_texts[page_no] = page_md
            jpeg = pages.page_jpeg(page_no)
            write_page(book_id, page_no, page_md, jpeg)
            write_mid(
                book_id,
                page_no,
                tag_source(
                    page_md,
                    center_texts=centered_texts_from_blocks(grouped.get(page_no, [])),
                ),
            )

    _assert_book_alive(book_id)
    write_mineru_toc(book_id, toc)
    toc = _finalize_toc(book_id, toc, page_texts)
    _assert_book_alive(book_id)
    meta["page_count"] = total
    meta["status"] = "ready"
    meta["error"] = ""
    write_meta(book_id, meta)
    logger.info("kb book ready id=%s pages=%d toc=%d", book_id, total, len(toc.get("entries") or []))
    try:
        from app.kb.agent_notify import notify_import_completed

        notify_import_completed(book_id, conversation_id=str(meta.get("conversation_id") or ""))
    except Exception:
        logger.warning("import completed notify failed id=%s", book_id, exc_info=True)
    return book_to_dict(meta)


def mark_failed(book_id: str, error: str) -> None:
    meta = read_meta(book_id)
    if meta is None:
        return
    meta["status"] = "failed"
    meta["error"] = (error or "")[:4000]
    write_meta(book_id, meta)


def create_kb_book(
    book_id: str,
    *,
    filename: str = "book.pdf",
    label: str = "",
    subject: str = "math",
    grade_band: str = "hs",
    category: str = "textbook",
    textbook_hint: str = "",
    pdf_bytes: bytes = b"",
) -> dict[str, Any]:
    from app.kb.hint import clean_textbook_hint

    folder = book_dir(book_id)
    folder.mkdir(parents=True, exist_ok=True)
    if pdf_bytes:
        (folder / "book.pdf").write_bytes(pdf_bytes)
    hint = clean_textbook_hint(hint=textbook_hint, label=label, filename=filename)
    now = datetime.now(timezone.utc).isoformat()
    meta = {
        "id": book_id,
        "label": label or hint or filename,
        "filename": filename or "book.pdf",
        "subject": subject or "math",
        "grade_band": grade_band or "hs",
        "category": normalize_category(category),
        "textbook_hint": hint,
        "status": "uploaded",
        "page_count": 0,
        "error": "",
        "is_default": False,
        "created_at": now,
        "updated_at": now,
    }
    write_meta(book_id, meta)
    write_toc(book_id, {"entries": [], "print_to_pdf": {}, "toc_pages": []})
    return book_to_dict(meta)


def list_kb_books() -> list[dict[str, Any]]:
    rows = [book_to_dict(m) for m in list_metas()]
    rows.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return rows


def rebuild_toc_from_pages(book_id: str) -> dict:
    meta = read_meta(book_id)
    if meta is None:
        raise ValueError("book not found")
    toc = read_toc(book_id)
    folder = book_dir(book_id) / "pages"
    page_texts: dict[int, str] = {}
    if folder.is_dir():
        for path in folder.glob("*.md"):
            try:
                page_texts[int(path.stem)] = path.read_text(encoding="utf-8")
            except ValueError:
                continue
    return _finalize_toc(book_id, toc, page_texts)


def get_toc(book_id: str) -> dict:
    return read_toc(book_id)


def purge_kb_book(book_id: str) -> dict[str, Any]:
    from app.clients.redis_client import purge_kb_import_queue, revoke_kb_book
    from app.kb.assets import delete_book_assets

    sid = safe_id(book_id)
    revoke_kb_book(sid)
    queue_removed = purge_kb_import_queue(sid)
    minio_removed = delete_book_assets(sid)
    workspace_removed = delete_book_dir(sid)
    return {
        "deleted": sid,
        "queue_removed": queue_removed,
        "minio_removed": minio_removed,
        "workspace_removed": workspace_removed,
    }
