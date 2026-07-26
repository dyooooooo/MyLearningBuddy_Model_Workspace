"""Import job IR blocks — read, patch, replace page, rerender HTML pages."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import CorpusImportJob
from app.services.corpus_import_incremental import (
    _job_meta,
    upsert_page_draft_chunk,
)
from app.services.document_ir import (
    DocumentBlock,
    document_block_to_dict,
    render_block_html,
)
from app.services.import_ir_pipeline import rerender_page_html
from app.services.import_ir_store import load_blocks, save_blocks
from app.services.ir.textbook_profile import default_profile_id
from app.util.json_util import dumps

logger = logging.getLogger(__name__)

# meta keys derived from ``block.text`` by math enrichment. The renderer prefers
# them over ``text``, so a caller that rewrites ``text`` without refreshing them
# would see IR change while HTML stays put.
_TEXT_DERIVED_META = ("text_segments", "math_enrich_version", "latex_validated")


def get_job_blocks(session: Session, job_id: str) -> tuple[CorpusImportJob, list[dict[str, Any]]]:
    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise ValueError(f"import job not found: {job_id}")
    blocks = load_blocks(job)
    if not blocks:
        raise ValueError("job has no IR blocks")
    return job, [document_block_to_dict(b) for b in blocks]


def _apply_order(data: dict[str, Any], order: int) -> None:
    data["order"] = int(order)
    meta = dict(data.get("meta") or {})
    meta["reading_order"] = int(order)
    data["meta"] = meta


def _drop_text_derived_meta(meta: dict[str, Any], patch_meta: Any) -> list[str]:
    """Drop stale math-enrich cache when ``text`` was rewritten.

    Skipped for keys the caller supplied explicitly. Returns the dropped keys so
    the API can tell the caller what was invalidated.
    """
    explicit = patch_meta if isinstance(patch_meta, dict) else {}
    dropped: list[str] = []
    for key in _TEXT_DERIVED_META:
        if key in explicit:
            continue
        if meta.pop(key, None) is not None:
            dropped.append(key)
    return dropped


def _checked_asset_url(value: Any) -> str | None:
    """Assets must stay site-relative (adapters only keep ``/``-rooted urls).

    Blocks external/protocol-relative targets so a caller cannot repoint a
    figure at an off-site image.
    """
    if value is None:
        return None
    url = str(value).strip()
    if not url:
        return None
    if not url.startswith("/") or url.startswith("//"):
        raise ValueError(f"asset_url must be a site-relative path: {url!r}")
    return url


def patch_job_block(
    session: Session,
    job_id: str,
    block_id: str,
    patch: dict[str, Any],
) -> tuple[CorpusImportJob, list[dict[str, Any]], dict[str, Any]]:
    """Patch one IR block.

    Third return value reports what the write actually did to the rendered HTML
    (``html_changed`` / ``block_html``) plus any stale caches invalidated, so a
    caller does not have to re-read the page to find out.
    """
    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise ValueError(f"import job not found: {job_id}")
    blocks = load_blocks(job)
    if not blocks:
        raise ValueError("job has no IR blocks")

    updated: list[DocumentBlock] = []
    found = False
    html_before = ""
    html_after = ""
    dropped: list[str] = []
    for block in blocks:
        if block.id != block_id:
            updated.append(block)
            continue
        found = True
        html_before = render_block_html(block)
        data = block.model_dump()
        if "text" in patch:
            data["text"] = patch["text"]
        if "caption" in patch:
            data["caption"] = patch["caption"]
        meta = dict(data.get("meta") or {})
        if "layout_zone" in patch:
            meta["layout_zone"] = patch["layout_zone"]
        if "pedagogy_role" in patch:
            meta["pedagogy_role"] = patch["pedagogy_role"]
        if "meta" in patch and isinstance(patch["meta"], dict):
            meta.update(patch["meta"])
        if "text" in patch:
            dropped = _drop_text_derived_meta(meta, patch.get("meta"))
        data["meta"] = meta
        if "order" in patch and patch["order"] is not None:
            _apply_order(data, int(patch["order"]))
        elif "reading_order" in meta:
            # keep top-level order aligned if only meta.reading_order was patched
            try:
                data["order"] = int(meta["reading_order"])
            except (TypeError, ValueError):
                pass
        patched = DocumentBlock.model_validate(data)
        html_after = render_block_html(patched)
        updated.append(patched)

    if not found:
        raise ValueError(f"block not found: {block_id}")

    save_blocks(job, updated)
    meta = _job_meta(job)
    meta["block_count"] = len(updated)
    job.meta_json = dumps(meta)
    session.add(job)
    session.commit()
    info = {
        "html_changed": html_after.strip() != html_before.strip(),
        "block_html": html_after,
        "dropped_stale_meta": dropped,
    }
    return job, [document_block_to_dict(b) for b in updated], info


def replace_page_blocks(
    session: Session,
    job_id: str,
    page_no: int,
    page_blocks: list[dict[str, Any]],
    *,
    rerender: bool = True,
) -> tuple[CorpusImportJob, list[dict[str, Any]], str | None, dict[str, Any]]:
    """Replace / reorder all IR blocks on one page.

    Incoming list order becomes reading order (1..n). Each item should keep its
    ``id`` matching an existing block on that page (or be a full new block dict
    with required DocumentBlock fields). Does not re-run layout enrich.

    Fourth return value mirrors :func:`patch_job_block`: whether the page HTML
    actually moved, plus the stale caches invalidated per block.
    """
    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise ValueError(f"import job not found: {job_id}")
    blocks = load_blocks(job)
    if not blocks:
        raise ValueError("job has no IR blocks")

    pn = int(page_no)
    if not page_blocks:
        raise ValueError("page_blocks must be a non-empty list")

    by_id = {b.id: b for b in blocks if b.page_no == pn}
    other = [b for b in blocks if b.page_no != pn]
    html_before = "".join(render_block_html(b) for b in blocks if b.page_no == pn)

    dropped: dict[str, list[str]] = {}
    rebuilt: list[DocumentBlock] = []
    for idx, raw in enumerate(page_blocks, start=1):
        if not isinstance(raw, dict):
            raise ValueError("each page block must be an object")
        bid = str(raw.get("id") or "").strip()
        if bid and bid in by_id:
            data = by_id[bid].model_dump()
        else:
            data = dict(raw)
            data.setdefault("page_no", pn)
            if not data.get("id"):
                raise ValueError(f"block at index {idx} missing id")
        # overlay allowed fields from payload
        for key in ("text", "caption", "type", "bbox", "table_html", "asset_url"):
            if key not in raw:
                continue
            data[key] = _checked_asset_url(raw[key]) if key == "asset_url" else raw[key]
        meta = dict(data.get("meta") or {})
        if "layout_zone" in raw:
            meta["layout_zone"] = raw["layout_zone"]
        if "pedagogy_role" in raw:
            meta["pedagogy_role"] = raw["pedagogy_role"]
        if "meta" in raw and isinstance(raw["meta"], dict):
            meta.update(raw["meta"])
        if "text" in raw:
            gone = _drop_text_derived_meta(meta, raw.get("meta"))
            if gone:
                dropped[str(data.get("id"))] = gone
        data["meta"] = meta
        data["page_no"] = pn
        order_val = raw.get("order", idx)
        try:
            order_i = int(order_val)
        except (TypeError, ValueError):
            order_i = idx
        _apply_order(data, order_i)
        rebuilt.append(DocumentBlock.model_validate(data))

    # Prefer list order if multiple share order; re-number 1..n stably
    for i, b in enumerate(rebuilt, start=1):
        data = b.model_dump()
        _apply_order(data, i)
        rebuilt[i - 1] = DocumentBlock.model_validate(data)

    merged = other + rebuilt
    save_blocks(job, merged)
    meta = _job_meta(job)
    meta["block_count"] = len(merged)
    job.meta_json = dumps(meta)
    session.add(job)
    session.commit()

    html: str | None = None
    if rerender:
        html = rerender_import_page(session, job_id, pn)
        session.commit()

    html_after = "".join(render_block_html(b) for b in rebuilt)
    info = {
        "html_changed": html_after.strip() != html_before.strip(),
        "dropped_stale_meta": dropped,
    }
    return job, [document_block_to_dict(b) for b in rebuilt], html, info


def rerender_import_page(session: Session, job_id: str, page_no: int) -> str:
    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise ValueError(f"import job not found: {job_id}")
    blocks = load_blocks(job)
    if not blocks:
        raise ValueError("job has no IR blocks")

    meta = _job_meta(job)
    profile_id = str(meta.get("textbook_profile") or default_profile_id())
    page_html = rerender_page_html(blocks, page_no, profile_id=profile_id)

    total = len({b.page_no for b in blocks})

    upsert_page_draft_chunk(
        session,
        job,
        page_no=page_no,
        page_content=page_html,
        total_pages=total,
        engine=str(meta.get("parse_engine") or ""),
        content_format="html",
        durable=True,
    )
    logger.info("rerender page job=%s page=%d chars=%d", job_id, page_no, len(page_html))
    return page_html
