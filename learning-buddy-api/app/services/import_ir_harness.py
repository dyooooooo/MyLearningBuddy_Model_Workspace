"""Harness helpers for import IR block conservation."""
from __future__ import annotations

from app.services.document_ir import DocumentBlock, render_block_markdown, render_page_markdown


def count_ir_assets(blocks: list[DocumentBlock]) -> tuple[int, int]:
    """Return (table_count, figure_count) from IR blocks."""
    tables = sum(1 for b in blocks if b.type == "table" or (b.table_html or "").strip())
    figures = sum(
        1
        for b in blocks
        if b.type in ("figure", "equation") or (b.asset_url or b.asset_key)
    )
    return tables, figures


def block_text_present_in_chunks(block: DocumentBlock, chunk_texts: list[str]) -> bool:
    """Check block content appears in at least one semantic or page chunk."""
    rendered = render_block_markdown(block).strip()
    if not rendered:
        return True
    needle = rendered[:80] if len(rendered) > 80 else rendered
    for text in chunk_texts:
        if needle in text or rendered in text:
            return True
    if block.table_html and any(block.table_html[:40] in t for t in chunk_texts):
        return True
    if block.asset_url and any(block.asset_url in t for t in chunk_texts):
        return True
    return False


def assert_ir_block_conservation(
    blocks: list[DocumentBlock],
    *,
    page_chunk_texts: list[str],
    semantic_chunk_texts: list[str],
) -> None:
    """Raise AssertionError if IR content is missing from page or semantic chunks."""
    all_chunks = page_chunk_texts + semantic_chunk_texts
    missing: list[str] = []
    for block in blocks:
        if block.type in ("page_break",):
            continue
        page_md = render_page_markdown(blocks, block.page_no)
        in_page = block_text_present_in_chunks(block, [page_md])
        in_any = block_text_present_in_chunks(block, all_chunks)
        if not in_page and not in_any:
            missing.append(f"{block.id}:{block.type}:p{block.page_no}")
    if missing:
        raise AssertionError(f"IR blocks missing from chunks: {missing[:10]}")
