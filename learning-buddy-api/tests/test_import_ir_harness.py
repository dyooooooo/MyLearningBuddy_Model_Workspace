"""Harness tests for IR block conservation."""
from __future__ import annotations

from app.services.corpus_tree_from_ir import build_sections_from_ir
from app.services.document_ir import DocumentBlock, render_page_markdown
from app.services.import_ir_harness import assert_ir_block_conservation, count_ir_assets
from tests.test_corpus_tree_from_ir import _sample_blocks


def test_count_ir_assets():
    blocks = _sample_blocks()
    tables, figures = count_ir_assets(blocks)
    assert tables >= 1
    assert figures >= 1


def test_ir_block_conservation_synthetic():
    blocks = _sample_blocks()
    _, sections = build_sections_from_ir(
        blocks,
        textbook_id="tb:test",
        subject="math",
        grade_band="hs",
    )
    page_texts = [render_page_markdown(blocks, p) for p in {b.page_no for b in blocks}]
    semantic_texts = [c["text"] for s in sections for c in s.chunks]
    assert_ir_block_conservation(
        blocks,
        page_chunk_texts=page_texts,
        semantic_chunk_texts=semantic_texts,
    )
