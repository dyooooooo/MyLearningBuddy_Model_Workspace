"""ensure_blank_pages: extend-range gap fill + missing-content reporting."""
from __future__ import annotations

import fitz

from app.services.corpus_blank_page import ensure_blank_pages, parse_page_ranges
from app.services.document_ir import DocumentBlock, stable_block_id


def _make_pdf(page_kinds: list[str]) -> bytes:
    """Build a PDF: 'text' = dense text page, 'blank' = empty page."""
    doc = fitz.open()
    for kind in page_kinds:
        page = doc.new_page()
        if kind == "text":
            # Dense ink so the 72dpi JPEG clearly exceeds the blank threshold.
            for i in range(40):
                page.insert_text(
                    (40, 20 + i * 18),
                    "三角函数练习内容 dense line %02d ABCDEFGHIJKLMNOPQRSTUVWXYZ" % i,
                    fontsize=10,
                )
    return doc.tobytes()


def _block(page_no: int, text: str = "内容") -> DocumentBlock:
    return DocumentBlock(
        id=stable_block_id("mineru_cloud", str(page_no), "t"),
        type="paragraph",
        page_no=page_no,
        order=1,
        text=text,
        meta={},
    )


def test_parse_page_ranges():
    assert parse_page_ranges("61-80") == set(range(61, 81))
    assert parse_page_ranges("1-3,63") == {1, 2, 3, 63}
    assert parse_page_ranges(" 5 ") == {5}
    assert parse_page_ranges("") == set()
    assert parse_page_ranges(None) == set()
    assert parse_page_ranges("garbage") == set()


def test_expected_pages_fills_blank_gap_in_extend_range():
    # 61-80 段：by_page 只有 61、63（62 被 MinerU 漏掉，PDF 62 是真空白页）。
    pdf = _make_pdf(["text"] * 61 + ["blank"] + ["text"] * 18)
    blocks = [_block(61), _block(63)]
    out = ensure_blank_pages(
        blocks,
        pdf_bytes=pdf,
        total_pages=2,  # 旧语义的"有块页数"，不应再驱动 range(1, 3)
        expected_pages=set(range(61, 81)),
    )
    pages = {b.page_no for b in out}
    assert 62 in pages  # 缺口页补齐占位
    blank62 = [b for b in out if b.page_no == 62][0]
    assert blank62.meta.get("blank") is True
    # 不应对段外的 1-60 页做任何事
    assert all(b.page_no >= 61 for b in out)


def test_missing_content_page_reported_not_placeholdered():
    # PDF 第 2 页有内容但 IR 完全缺失 → 记入 missing_pages，不插占位。
    pdf = _make_pdf(["text", "text", "blank"])
    blocks = [_block(1)]
    missing: list[int] = []
    out = ensure_blank_pages(
        blocks,
        pdf_bytes=pdf,
        expected_pages={1, 2, 3},
        missing_pages=missing,
    )
    pages = {b.page_no for b in out}
    assert missing == [2]
    assert 2 not in pages  # 不插占位
    assert 3 in pages  # 真空白页仍有占位
    assert any(b.page_no == 3 and b.meta.get("blank") for b in out)


def test_total_pages_legacy_behavior_unchanged():
    pdf = _make_pdf(["text", "blank", "text", "blank"])
    blocks = [_block(1), _block(3)]
    out = ensure_blank_pages(blocks, pdf_bytes=pdf, total_pages=4)
    pages = {b.page_no for b in out}
    assert pages == {1, 2, 3, 4}
