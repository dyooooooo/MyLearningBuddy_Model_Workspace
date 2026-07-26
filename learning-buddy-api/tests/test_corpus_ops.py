"""Unit tests for corpus IR T1 + blank placeholder helpers."""
from __future__ import annotations

from app.services.corpus_blank_page import blank_placeholder_block, mark_blank_html
from app.services.document_ir import DocumentBlock
from app.services.import_ir_validation import run_page_checks


def test_t1_page_empty():
    findings = run_page_checks([], "<article class='lb-page'><section class='lb-main'></section></article>", page_no=1)
    assert any(f.suspect_type == "PAGE_EMPTY" for f in findings)


def test_t1_main_empty_with_blocks():
    blocks = [
        DocumentBlock(id="a", type="paragraph", page_no=1, order=1, text=""),
    ]
    html = '<article class="lb-page"><section class="lb-main"></section></article>'
    findings = run_page_checks(blocks, html, page_no=1)
    assert any(f.suspect_type == "MAIN_EMPTY" for f in findings)


def test_t1_skips_blank_meta():
    blocks = [blank_placeholder_block(2)]
    findings = run_page_checks(blocks, "<article></article>", page_no=2)
    assert findings == []


def test_mark_blank_html():
    html = '<article class="lb-page" data-page="1"></article>'
    out = mark_blank_html(html)
    assert 'lb-page--blank' in out
    assert 'data-blank="true"' in out
