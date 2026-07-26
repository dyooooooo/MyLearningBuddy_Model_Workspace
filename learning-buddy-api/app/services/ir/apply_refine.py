"""Apply 35B page refine patches to DocumentBlock IR (deterministic)."""
from __future__ import annotations

import logging

from app.math.normalize import normalize_plain
from app.schemas.mineru_refine import PageRefineResult, RefineAllPagesResult
from app.services.document_ir import DocumentBlock

logger = logging.getLogger(__name__)


def apply_page_refine(
    blocks: list[DocumentBlock],
    result: PageRefineResult,
    *,
    content_only: bool = False,
) -> list[DocumentBlock]:
    """Merge refine patches into blocks for one page.

    ``content_only=True`` freezes zone/role/anchors (topology locked); only
    text/latex updates apply.
    """
    patch_by_id = {p.id: p for p in result.blocks}
    out: list[DocumentBlock] = []
    for block in blocks:
        if block.page_no != result.page_no:
            out.append(block)
            continue
        patch = patch_by_id.get(block.id)
        if patch is None:
            out.append(block)
            continue
        meta = dict(block.meta or {})
        # Preserve adapter TOC: 35B refine often demotes index/text TOC to body.
        keep_toc = meta.get("pedagogy_role") == "toc" or bool(meta.get("toc_entries"))
        if not content_only:
            meta["layout_zone"] = patch.zone
            if keep_toc:
                meta["pedagogy_role"] = "toc"
            else:
                meta["pedagogy_role"] = patch.pedagogy_role
            if patch.anchor_block_id:
                meta["anchor_block_id"] = patch.anchor_block_id
            if patch.figure_group_id:
                meta["figure_group_id"] = patch.figure_group_id
        meta["layout_confidence"] = "high"
        meta["refined_by"] = "35b"
        text = block.text
        # Page/block re-recognize may correct OCR text (not only layout).
        if patch.text is not None and str(patch.text).strip():
            text = str(patch.text).strip()
            meta["text_refined"] = True
        if block.type == "equation" and patch.latex:
            text = patch.latex.strip()
            meta["latex_refined"] = True
        out.append(block.model_copy(update={"text": text, "meta": meta}))
    return out


def count_effective_changes(
    before: list[DocumentBlock],
    after: list[DocumentBlock],
) -> int:
    """Count blocks whose text/zone/role/type changed between before and after."""
    bmap = {b.id: b for b in before}
    n = 0
    for a in after:
        b = bmap.get(a.id)
        if b is None:
            n += 1
            continue
        bm = b.meta or {}
        am = a.meta or {}
        if (b.text or "").strip() != (a.text or "").strip():
            n += 1
            continue
        if bm.get("layout_zone") != am.get("layout_zone"):
            n += 1
            continue
        if bm.get("pedagogy_role") != am.get("pedagogy_role"):
            n += 1
            continue
        if b.type != a.type:
            n += 1
            continue
    if len(before) != len(after):
        # deleted or added ids
        aids = {a.id for a in after}
        for b in before:
            if b.id not in aids:
                n += 1
    return n


def apply_all_refine(
    blocks: list[DocumentBlock],
    refine: RefineAllPagesResult,
) -> list[DocumentBlock]:
    current = blocks
    for page_result in refine.pages:
        current = apply_page_refine(current, page_result)
    current.sort(key=lambda b: (b.page_no, b.order))
    return current


def normalize_equation_blocks(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """Run math_normalize on equation blocks; write validated latex to text."""
    out: list[DocumentBlock] = []
    for block in blocks:
        if block.type != "equation" or not (block.text or "").strip():
            out.append(block)
            continue
        raw = (block.text or "").strip()
        rich = normalize_plain(raw)
        math_segs = [s for s in rich.segments if s.kind == "math" and s.valid and s.latex]
        if math_segs:
            latex = " ".join(s.latex for s in math_segs if s.latex)
            meta = dict(block.meta or {})
            meta["latex_validated"] = True
            out.append(block.model_copy(update={"text": latex, "meta": meta}))
        else:
            meta = dict(block.meta or {})
            meta["latex_validated"] = False
            out.append(block.model_copy(update={"meta": meta}))
    return out
