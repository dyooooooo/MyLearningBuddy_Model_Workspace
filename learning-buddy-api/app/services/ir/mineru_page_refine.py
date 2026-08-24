"""35B per-page refine orchestration (MinerU blocks + page JPEG)."""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.clients.vllm import vision_page_refine_json
from app.schemas.mineru_refine import (
    REFINE_MODE_PROMPT,
    REFINE_PROMPT_VERSION,
    REFINE_PROMPT_VERSION_FULL,
    PageRefineResult,
    RefineAllPagesResult,
)
from app.services.document_ir import DocumentBlock
from app.services.ir.pdf_render_cache import PdfRenderCache
from app.services.ir.textbook_profile import default_profile_id, load_textbook_profile
from app.services.pdf_extract import render_page_pixmap

logger = logging.getLogger(__name__)

# Lighter render than the 180 dpi vision OCR path — layout/role only.
REFINE_PAGE_DPI = 150

_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "data" / "prompts"
_DEFAULT_REFINE_MODE = "skip"

# Cost cascade: ``auto`` runs the cheap geometry+rules pass on every page and
# spends a vision call only on the pages that came out low confidence.
AUTO_REFINE_MODE = "auto"
# Concrete mode used for the pages ``auto`` decides to escalate.
AUTO_ESCALATION_MODE = "layout_only"

_REFINE_MODES = ("layout_only", "full", "rerecognize", "skip", AUTO_REFINE_MODE)


def get_refine_mode(profile_id: str | None = None) -> str:
    """Resolve refine_mode from profile (layout_only | full | rerecognize | auto | skip)."""
    pid = profile_id or default_profile_id()
    try:
        profile = load_textbook_profile(pid)
    except FileNotFoundError:
        return _DEFAULT_REFINE_MODE
    mode = str(profile.get("refine_mode") or _DEFAULT_REFINE_MODE).strip().lower()
    if mode not in _REFINE_MODES:
        return _DEFAULT_REFINE_MODE
    return mode


def _prompt_version_for_mode(mode: str) -> str:
    return REFINE_MODE_PROMPT.get(mode, REFINE_PROMPT_VERSION)


def prompt_version_for_mode(mode: str) -> str:
    return _prompt_version_for_mode(mode)


def get_refine_parallel(profile_id: str | None = None) -> int:
    """Resolve refine_parallel from profile (>=1). 1 = serial."""
    pid = profile_id or default_profile_id()
    try:
        profile = load_textbook_profile(pid)
    except FileNotFoundError:
        return 1
    try:
        val = int(profile.get("refine_parallel") or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(4, val))


def load_refine_prompt(profile_id: str | None = None, *, mode: str | None = None) -> str:
    """Base refine prompt + Edition policy via Assembler."""
    from app.services.editions.assembler import assemble_prompt
    from app.services.editions.slots import slot_for_refine_mode

    pid = profile_id or default_profile_id()
    refine_mode = mode or get_refine_mode(pid)
    slot = slot_for_refine_mode(refine_mode)
    try:
        return assemble_prompt(slot.id, pid)
    except FileNotFoundError:
        # Fallback: raw prompt file without edition policy.
        version = _prompt_version_for_mode(refine_mode)
        prompt_path = _PROMPTS_DIR / f"{version}.md"
        if not prompt_path.is_file():
            for alt in (
                REFINE_PROMPT_VERSION_FULL,
                REFINE_PROMPT_VERSION,
                "mineru_page_refine_v1",
            ):
                cand = _PROMPTS_DIR / f"{alt}.md"
                if cand.is_file():
                    prompt_path = cand
                    break
        return prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else ""


def blocks_to_refine_payload(
    blocks: list[DocumentBlock],
    page_no: int,
    *,
    full_text: bool = False,
) -> list[dict[str, Any]]:
    """Serialize page blocks for 35B input.

    ``full_text=True`` for user re-recognize (send longer OCR draft).
    """
    page_blocks = sorted(
        [b for b in blocks if b.page_no == page_no],
        key=lambda b: (b.order, b.id),
    )
    text_limit = 2500 if full_text else 300
    out: list[dict[str, Any]] = []
    for b in page_blocks:
        meta = b.meta or {}
        if meta.get("skip_render") and not full_text:
            continue
        item: dict[str, Any] = {
            "id": b.id,
            "type": b.type,
            "text": (b.text or "")[:text_limit],
            "bbox": b.bbox,
            "mineru_type": meta.get("mineru_type"),
            "zone": meta.get("layout_zone"),
            "pedagogy_role": meta.get("pedagogy_role"),
            "caption": (b.caption or "")[:300] or None,
        }
        if b.type == "equation" and b.text:
            item["latex_draft"] = b.text[:500]
        out.append(item)
    return out


def _render_page_jpeg(
    pdf_bytes: bytes,
    page_no: int,
    *,
    render_cache: PdfRenderCache | None,
) -> bytes:
    if render_cache is not None:
        return render_cache.page_jpeg(page_no, dpi=REFINE_PAGE_DPI)
    return render_page_pixmap(pdf_bytes, page_no, dpi=REFINE_PAGE_DPI)


def refine_page(
    pdf_bytes: bytes,
    page_no: int,
    blocks: list[DocumentBlock],
    *,
    profile_id: str | None = None,
    mode: str | None = None,
    render_cache: PdfRenderCache | None = None,
    jpeg_bytes: bytes | None = None,
    user_note: str = "",
) -> PageRefineResult:
    """Render page JPEG (or use provided) and call 35B; validate PageRefineResult."""
    refine_mode = mode or get_refine_mode(profile_id)
    full_text = refine_mode in ("rerecognize", "content_correct")
    payload = blocks_to_refine_payload(blocks, page_no, full_text=full_text)
    if not payload:
        return PageRefineResult(page_no=page_no, blocks=[], chunks=[])

    jpeg = jpeg_bytes or _render_page_jpeg(
        pdf_bytes, page_no, render_cache=render_cache
    )
    system = load_refine_prompt(profile_id, mode=refine_mode)
    max_tokens = 6144 if full_text else 3072
    raw = vision_page_refine_json(
        jpeg,
        payload,
        system=system,
        page_no=page_no,
        max_tokens=max_tokens,
        user_note=user_note,
    )
    if raw.get("page_no") is None:
        raw["page_no"] = page_no
    result = PageRefineResult.model_validate(raw)
    if result.page_no != page_no:
        raise ValueError(f"refine page_no mismatch: expected {page_no}, got {result.page_no}")
    page_ids = {b.id for b in blocks if b.page_no == page_no}
    valid = [p for p in result.blocks if p.id in page_ids]
    dropped = [p.id for p in result.blocks if p.id not in page_ids]
    if dropped:
        logger.warning(
            "refine_page: dropping %d patch(es) with unknown id on page %s: %s",
            len(dropped),
            page_no,
            dropped[:12],
        )
    return PageRefineResult(page_no=page_no, blocks=valid, chunks=result.chunks)


def refine_one_block(
    pdf_bytes: bytes,
    page_no: int,
    blocks: list[DocumentBlock],
    block_id: str,
    *,
    profile_id: str | None = None,
    render_cache: PdfRenderCache | None = None,
    jpeg_bytes: bytes | None = None,
    user_note: str = "",
) -> PageRefineResult:
    """Re-recognize a single block: previous block JSON + page image → 35B patch."""
    target = next(
        (b for b in blocks if b.page_no == page_no and b.id == block_id),
        None,
    )
    if target is None:
        raise ValueError(f"block {block_id} not found on page {page_no}")

    # Context: focus ± neighbors (keeps prompt small; reduces id hallucination).
    page_blocks = sorted(
        [b for b in blocks if b.page_no == page_no],
        key=lambda b: (b.order, b.id),
    )
    focus_idx = next(i for i, b in enumerate(page_blocks) if b.id == block_id)
    lo = max(0, focus_idx - 2)
    hi = min(len(page_blocks), focus_idx + 3)
    context_blocks = page_blocks[lo:hi]
    payload = blocks_to_refine_payload(context_blocks, page_no, full_text=True)
    for item in payload:
        if item["id"] == block_id:
            item["focus"] = True
            break

    jpeg = jpeg_bytes or _render_page_jpeg(
        pdf_bytes, page_no, render_cache=render_cache
    )
    system = load_refine_prompt(profile_id, mode="rerecognize")
    system += (
        "\n\n## 单块模式\n"
        f"只修正 focus 块 id=`{block_id}`（输入里已标 focus:true）。\n"
        f"**输出里该块的 id 必须一字不差写成 `{block_id}`**，禁止改名或新造 id。\n"
        "其他邻接块可不输出；若输出也必须照抄其原 id。\n"
        "focus 可以是正文、侧栏 aside、页眉/页脚/页码（zone=running）；"
        "对照页图修正，勿编造页上没有的内容。\n"
    )
    raw = vision_page_refine_json(
        jpeg,
        payload,
        system=system,
        page_no=page_no,
        max_tokens=4096,
        user_note=user_note,
    )
    if raw.get("page_no") is None:
        raw["page_no"] = page_no
    result = PageRefineResult.model_validate(raw)
    focused = _coerce_focus_patches(result, block_id=block_id, target=target)
    if not focused:
        got = [p.id for p in result.blocks]
        raise ValueError(
            f"35B did not return a patch for block {block_id} "
            f"(got ids={got[:8]})"
        )
    return PageRefineResult(page_no=page_no, blocks=focused, chunks=[])


def _coerce_focus_patches(
    result: PageRefineResult,
    *,
    block_id: str,
    target: DocumentBlock,
) -> list:
    """Map 35B patches onto the requested focus id (models often rename ids)."""
    exact = [p for p in result.blocks if p.id == block_id]
    if exact:
        return exact

    if not result.blocks:
        return []

    # Single returned patch → force onto focus id (most common failure mode).
    if len(result.blocks) == 1:
        logger.info(
            "refine_one_block: remapping sole patch id %s → %s",
            result.blocks[0].id,
            block_id,
        )
        return [result.blocks[0].model_copy(update={"id": block_id})]

    # Prefer patch whose id contains the focus id (truncation / prefix mess).
    # Require length ≥6 to avoid false positives like id="a" ⊂ focus hex.
    for p in result.blocks:
        pid = str(p.id or "")
        if len(pid) >= 6 and len(block_id) >= 6 and (
            block_id in pid or pid in block_id
        ):
            logger.info(
                "refine_one_block: remapping related id %s → %s",
                p.id,
                block_id,
            )
            return [p.model_copy(update={"id": block_id})]

    # Fallback: sole patch that actually changed text/latex.
    with_text = [p for p in result.blocks if (p.text or p.latex or "").strip()]
    if len(with_text) == 1:
        logger.info(
            "refine_one_block: remapping sole text patch %s → %s",
            with_text[0].id,
            block_id,
        )
        return [with_text[0].model_copy(update={"id": block_id})]

    logger.warning(
        "refine_one_block: cannot coerce patches for %s (target order=%s bbox=%s); got %s",
        block_id,
        target.order,
        target.bbox,
        [p.id for p in result.blocks],
    )
    return []


def refine_all_pages(
    pdf_bytes: bytes,
    blocks: list[DocumentBlock],
    *,
    profile_id: str | None = None,
    progress_cb: Callable[[dict], None] | None = None,
) -> RefineAllPagesResult:
    """Refine every page; retry once per page on failure."""
    if os.environ.get("SKIP_LIVE_REFINE", "").strip() in ("1", "true", "yes"):
        logger.info("SKIP_LIVE_REFINE set — returning empty refine result")
        return RefineAllPagesResult(prompt_version=REFINE_PROMPT_VERSION)

    pages = sorted({b.page_no for b in blocks})
    total = len(pages)
    out = RefineAllPagesResult(prompt_version=REFINE_PROMPT_VERSION)
    for idx, page_no in enumerate(pages, start=1):
        if progress_cb:
            try:
                progress_cb(
                    {
                        "refine_progress": f"{idx}/{total}",
                        "refine_page": idx,
                        "refine_total": total,
                    }
                )
            except Exception:
                pass
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                page_result = refine_page(
                    pdf_bytes, page_no, blocks, profile_id=profile_id
                )
                out.pages.append(page_result)
                break
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "refine page %d attempt %d failed: %s",
                    page_no,
                    attempt + 1,
                    exc,
                )
        else:
            out.errors.append(
                {"page_no": page_no, "error": str(last_err)[:500] if last_err else "unknown"}
            )
    return out


def refine_result_to_meta_chunks(result: RefineAllPagesResult) -> list[dict[str, Any]]:
    """Flatten page chunks for job meta_json."""
    flat: list[dict[str, Any]] = []
    for page in result.pages:
        for ch in page.chunks:
            flat.append(
                {
                    "chunk_id": ch.chunk_id,
                    "label": ch.label,
                    "block_ids": ch.block_ids,
                    "anchor": ch.anchor,
                    "page_no": page.page_no,
                }
            )
    return flat
