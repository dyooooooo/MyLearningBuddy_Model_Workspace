"""C2 ocr_refine — print-only correction with Buddy RAG context."""

from __future__ import annotations

import json
import logging

from app.buddy_client import rag_search
from app.config import settings
from app.prompts import OCR_REFINE_PROMPT
from app.schemas import PagePart
from app.vllm_client import vllm_client, _normalize_parts

logger = logging.getLogger(__name__)


def _parts_to_payload(parts: list[PagePart]) -> list[dict]:
    out: list[dict] = []
    for p in parts:
        out.append(
            {
                "kind": p.kind,
                "id": p.id,
                "print": p.print_text,
                "handwriting": p.handwriting,
                "bbox_print": p.bbox_print,
                "bbox_handwriting": p.bbox_handwriting,
            }
        )
    return out


def _count_changes(before: list[PagePart], after: list[PagePart]) -> int:
    changes = 0
    by_id = {(p.kind, p.id): p for p in before}
    for p in after:
        key = (p.kind, p.id)
        prev = by_id.get(key)
        if prev is None:
            changes += 1
            continue
        if prev.print_text != p.print_text or prev.handwriting != p.handwriting:
            changes += 1
    return changes


def _preserve_handwriting_from_draft(
    draft: list[PagePart], refined: list[PagePart]
) -> list[PagePart]:
    """Force handwriting + bbox_handwriting to match vision draft for all questions."""
    draft_map = {(p.kind, p.id): p for p in draft}
    out: list[PagePart] = []
    for p in refined:
        cp = p.model_copy(deep=True)
        prev = draft_map.get((p.kind, p.id))
        if prev and p.kind == "question":
            if (cp.handwriting or "") != (prev.handwriting or ""):
                logger.warning(
                    "ocr_refine changed handwriting on Q%s; restoring vision draft",
                    p.id,
                )
            cp.handwriting = prev.handwriting or ""
            cp.bbox_handwriting = list(prev.bbox_handwriting or [])
        out.append(cp)
    return out


def _raw_to_parts(raw: dict, draft: list[PagePart]) -> list[PagePart]:
    refined: list[PagePart] = []
    for p in _normalize_parts(raw):
        refined.append(
            PagePart(
                kind=p["kind"],
                id=str(p["id"]) if p.get("id") is not None else None,
                print_text=str(p.get("print", "")),
                handwriting=str(p.get("handwriting", "")),
                bbox_print=list(p.get("bbox_print") or []),
                bbox_handwriting=list(p.get("bbox_handwriting") or []),
            )
        )
    if not refined:
        return draft
    return _preserve_handwriting_from_draft(draft, refined)


async def _rag_context_for_parts(
    parts: list[PagePart],
    *,
    student_id: str,
    subject: str,
    grade_band: str,
) -> str:
    lines: list[str] = []
    for p in parts:
        if p.kind != "question" or not (p.print_text or "").strip():
            continue
        hits = await rag_search(
            p.print_text,
            student_id=student_id,
            subject=subject,
            grade_band=grade_band,
            top_k=3,
        )
        if not hits:
            continue
        lines.append(f"题目 {p.id} RAG:")
        for h in hits[:3]:
            lines.append(
                f"  - kp={h.get('kp_id')} label={h.get('label')} score={h.get('score')} text={str(h.get('text',''))[:80]}"
            )
    return "\n".join(lines)


async def refine_parts(
    parts: list[PagePart],
    *,
    student_id: str,
    subject: str = "math",
    grade_band: str = "hs",
) -> tuple[list[PagePart], int]:
    """RAG-assisted print-only OCR refine; handwriting always preserved from vision."""
    if not settings.refine_enabled or not parts:
        return parts, 0

    draft = _parts_to_payload(parts)
    rag_ctx = await _rag_context_for_parts(
        parts,
        student_id=student_id,
        subject=subject,
        grade_band=grade_band,
    )
    user_block = json.dumps({"parts": draft}, ensure_ascii=False, indent=2)
    prompt = (
        f"{OCR_REFINE_PROMPT}\n\n"
        f"RAG 参考（题干校对）:\n{rag_ctx or '(无)'}\n\nOCR 草稿:\n{user_block}"
    )

    try:
        raw = await vllm_client.refine_parts_json(prompt)
    except Exception as e:
        logger.warning("ocr_refine failed, using draft: %s", e)
        return parts, 0

    refined = _raw_to_parts(raw, parts)
    changes = _count_changes(parts, refined)
    logger.info("ocr_refine: %d parts, %d print changes", len(refined), changes)
    return refined, changes


async def refine_parts_handwriting_only(
    parts: list[PagePart],
    *,
    student_id: str,
    subject: str = "math",
    grade_band: str = "hs",
) -> tuple[list[PagePart], int]:
    """Alias for refine_parts (kept for diagnostic scripts)."""
    return await refine_parts(parts, student_id=student_id, subject=subject, grade_band=grade_band)
