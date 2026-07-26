from __future__ import annotations

from app.services.pdf_extract import PdfPageText
from app.services.pdf_trim import detect_trim_from_page, trim_extract_result
from app.services.pdf_extract import PdfExtractResult


def test_detect_trim_from_chapter_page() -> None:
    pages = [
        PdfPageText(1, "普通高中教科书\n定价：11.90元"),
        PdfPageText(2, "目录\n第1章 集合"),
        PdfPageText(3, "第1章 集合与逻辑\n1.1 集合初步\n正文开始"),
    ]
    assert detect_trim_from_page(pages) == 3

def test_trim_drops_front_matter() -> None:
    result = PdfExtractResult(
        pages=[
            PdfPageText(1, "封面"),
            PdfPageText(2, "第1章 集合与逻辑\n1.1 集合初步\n正文段落开始。"),
        ],
        full_text="",
        meta={"page_count": 2},
    )
    text, meta = trim_extract_result(result)
    assert "封面" not in text
    assert "集合与逻辑" in text
    assert meta["trim_from_page"] == 2
