"""Automatic, zero-label quality metrics for the 35B import pipeline.

These run on every import so structure↔frontend drift is measurable without a
hand-labeled golden set.  ``html_block_coverage`` is the key signal: the share
of renderable blueprint blocks whose ``data-block-id`` actually shows up in the
compiled HTML.  A value < 1.0 means some structured blocks were dropped by the
renderer (the exact class of bug this pipeline work targets).
"""
from __future__ import annotations

import re

from app.import_35b.render_zone import html_to_plain_text
from app.import_35b.schemas import PageBlueprint

_BLOCK_ID_RE = re.compile(r'data-block-id="([^"]+)"')
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9]+|[∞∪∩≤≥≠πθαβγλμσφω√]|[=<>+\-*/(){}\[\],.;:，。；：]")
_P_TAG_RE = re.compile(r"<p[ >]")
_INLINE_MATH_RE = re.compile(r'math--inline[^"]*" data-latex(?:-src)?="([^"]*)"')
# An inline math span longer than this is almost always several sub-items that
# got merged into one expression — the p10/p11 "stretched formula" failure.
MAX_INLINE_MATH_LEN = 80


def _renderable_block_ids(blueprint: PageBlueprint) -> list[str]:
    ids: list[str] = []
    for b in blueprint.blocks:
        if b.kind == "running" or b.zone == "running" or b.role == "running_header":
            continue
        ids.append(b.id)
    return ids


def html_block_coverage(blueprint: PageBlueprint, html: str) -> dict:
    """Return coverage stats for one page.

    ``coverage`` = rendered_renderable_blocks / total_renderable_blocks.
    ``missing`` lists blueprint block ids that never reached the HTML.
    """
    expected = _renderable_block_ids(blueprint)
    present = set(_BLOCK_ID_RE.findall(html or ""))
    missing = [bid for bid in expected if bid not in present]
    total = len(expected)
    rendered = total - len(missing)
    return {
        "page_no": blueprint.page_no,
        "total_blocks": total,
        "rendered_blocks": rendered,
        "coverage": round(rendered / total, 4) if total else 1.0,
        "missing_block_ids": missing,
    }


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _token_recall(source: str, candidate: str) -> float:
    src = _tokens(source)
    if not src:
        return 1.0
    cand = set(_tokens(candidate))
    return round(sum(1 for t in src if t in cand) / len(src), 4)


def _primary_content_text(blueprint: PageBlueprint) -> str:
    parts: list[str] = []
    for block in blueprint.blocks:
        if block.kind == "running" or block.zone == "running":
            continue
        content = block.content
        if block.kind == "text":
            parts.append(content.text_plain())
        elif block.kind == "table" and content.table:
            parts.append(content.table.plain_text())
        elif block.kind == "equation" and content.equation:
            parts.append(content.equation.plain_text())
        elif block.kind == "figure":
            parts.append(content.figure_caption())
    return "\n".join(p for p in parts if p)


def content_payload_consistency(blueprint: PageBlueprint) -> dict:
    errors: list[dict] = []
    polluted = 0
    for block in blueprint.blocks:
        content = block.content
        if block.content_type and block.kind != block.content_type:
            errors.append({"block_id": block.id, "error": "kind_content_type_mismatch"})

        active = content.active_payloads()
        if block.kind in ("text", "table", "equation", "figure"):
            if not active:
                errors.append({"block_id": block.id, "error": "empty_primary_payload"})
            if active and block.kind not in active:
                errors.append({"block_id": block.id, "error": f"missing_{block.kind}_payload"})
            extra = [p for p in active if p != block.kind]
            if extra:
                polluted += len(extra)
                errors.append({"block_id": block.id, "error": "extra_payloads", "extra": extra})

        if block.kind != "table" and (content.table or content.table_html):
            polluted += 1
            errors.append({"block_id": block.id, "error": "table_payload_on_non_table"})
        if block.kind != "equation" and (content.equation or content.latex):
            polluted += 1
            errors.append({"block_id": block.id, "error": "equation_payload_on_non_equation"})
        if block.kind != "figure" and (content.figure or content.caption):
            polluted += 1
            errors.append({"block_id": block.id, "error": "figure_payload_on_non_figure"})

    return {
        "consistent": not errors,
        "error_count": len(errors),
        "polluted_payload_count": polluted,
        "errors": errors[:50],
    }


def rendered_text_fidelity(blueprint: PageBlueprint, html: str) -> dict:
    expected = _primary_content_text(blueprint)
    rendered = html_to_plain_text(html or "")
    return {
        "json_primary_chars": len(expected),
        "html_plain_chars": len(rendered),
        "json_to_html_token_recall": _token_recall(expected, rendered),
    }


def paragraph_fidelity(blueprint: PageBlueprint, html: str) -> dict:
    """Did source paragraphs survive into distinct <p> elements?

    olmOCR-bench-style structural check: a text block with N source paragraphs
    should not collapse into a single <p> (the "no segmentation" bug).  We
    compare expected paragraph count against rendered <p> count.
    """
    expected = 0
    for block in blueprint.blocks:
        if block.kind == "running" or block.zone == "running":
            continue
        if block.kind == "text":
            expected += len(block.content.text_paragraphs())
    rendered = len(_P_TAG_RE.findall(html or ""))
    ratio = round(min(rendered, expected) / expected, 4) if expected else 1.0
    return {
        "expected_paragraphs": expected,
        "rendered_paragraphs": rendered,
        "paragraph_ratio": ratio,
    }


def inline_math_span_health(html: str) -> dict:
    """Flag oversized inline-math spans that indicate merged sub-items."""
    lengths = [len(m) for m in _INLINE_MATH_RE.findall(html or "")]
    longest = max(lengths) if lengths else 0
    return {
        "inline_math_spans": len(lengths),
        "max_inline_math_len": longest,
        "oversized_inline_math": longest > MAX_INLINE_MATH_LEN,
    }


def page_quality_metrics(blueprint: PageBlueprint, html: str) -> dict:
    return {
        "page_no": blueprint.page_no,
        "html_block_coverage": html_block_coverage(blueprint, html),
        "content_payload_consistency": content_payload_consistency(blueprint),
        "rendered_text_fidelity": rendered_text_fidelity(blueprint, html),
        "paragraph_fidelity": paragraph_fidelity(blueprint, html),
        "inline_math_span_health": inline_math_span_health(html),
    }


def aggregate_coverage(per_page: list[dict]) -> dict:
    total = sum(p["total_blocks"] for p in per_page)
    rendered = sum(p["rendered_blocks"] for p in per_page)
    low = [p["page_no"] for p in per_page if p["coverage"] < 1.0]
    return {
        "total_blocks": total,
        "rendered_blocks": rendered,
        "coverage": round(rendered / total, 4) if total else 1.0,
        "pages_with_drops": low,
    }


def aggregate_quality(per_page: list[dict]) -> dict:
    coverages = [p["html_block_coverage"] for p in per_page]
    consistency = [p["content_payload_consistency"] for p in per_page]
    fidelity = [p["rendered_text_fidelity"] for p in per_page]
    paragraphs = [p.get("paragraph_fidelity", {}) for p in per_page]
    inline_math = [p.get("inline_math_span_health", {}) for p in per_page]
    total_blocks = sum(p["total_blocks"] for p in coverages)
    rendered_blocks = sum(p["rendered_blocks"] for p in coverages)
    polluted = sum(p["polluted_payload_count"] for p in consistency)
    fidelity_scores = [p["json_to_html_token_recall"] for p in fidelity if p["json_primary_chars"]]
    para_ratios = [p.get("paragraph_ratio", 1.0) for p in paragraphs]
    return {
        "html_block_coverage": {
            "total_blocks": total_blocks,
            "rendered_blocks": rendered_blocks,
            "coverage": round(rendered_blocks / total_blocks, 4) if total_blocks else 1.0,
            "pages_with_drops": [
                p["page_no"] for p in coverages if p["coverage"] < 1.0
            ],
        },
        "polluted_payload_count": polluted,
        "pages_with_payload_errors": [
            per_page[idx]["page_no"]
            for idx, p in enumerate(consistency)
            if p["error_count"] > 0
        ],
        "avg_json_to_html_token_recall": (
            round(sum(fidelity_scores) / len(fidelity_scores), 4) if fidelity_scores else 1.0
        ),
        "pages_with_low_fidelity": [
            per_page[idx]["page_no"]
            for idx, p in enumerate(fidelity)
            if p["json_primary_chars"] and p["json_to_html_token_recall"] < 0.85
        ],
        "avg_paragraph_ratio": (
            round(sum(para_ratios) / len(para_ratios), 4) if para_ratios else 1.0
        ),
        "pages_with_paragraph_loss": [
            per_page[idx]["page_no"]
            for idx, p in enumerate(paragraphs)
            if p.get("expected_paragraphs", 0) > 1 and p.get("paragraph_ratio", 1.0) < 0.9
        ],
        "pages_with_oversized_inline_math": [
            per_page[idx]["page_no"]
            for idx, p in enumerate(inline_math)
            if p.get("oversized_inline_math")
        ],
    }
