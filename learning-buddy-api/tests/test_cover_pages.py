"""Cover-page detection and collapse."""
from __future__ import annotations

from app.services.document_ir import DocumentBlock
from app.services.ir.cover_pages import (
    cover_pages_from_profile,
    looks_like_cover_page,
)


def test_cover_pages_from_profile_default():
    assert cover_pages_from_profile({}) == [1, 2]
    assert cover_pages_from_profile({"features": {}}) == [1, 2]


def test_cover_pages_from_profile_custom():
    assert cover_pages_from_profile({"features": {"cover_pages": [1]}}) == [1]
    assert cover_pages_from_profile({"features": {"cover_pages": [1, 2, 3]}}) == [1, 2, 3]


def test_looks_like_cover_with_figure():
    blocks = [
        DocumentBlock(
            id="f1",
            type="figure",
            page_no=1,
            order=1,
            bbox=[50, 100, 950, 800],
            asset_url="/api/v1/studio/proxy/import/asset?key=x",
            meta={},
        ),
        DocumentBlock(
            id="t1",
            type="heading",
            page_no=1,
            order=2,
            text="必修 第二册",
            meta={},
        ),
    ]
    assert looks_like_cover_page(1, blocks, profile={"features": {"cover_pages": [1, 2]}})
    assert not looks_like_cover_page(3, blocks, profile={"features": {"cover_pages": [1, 2]}})


def test_looks_like_cover_rejects_body_page_without_figure():
    blocks = [
        DocumentBlock(
            id="p1",
            type="paragraph",
            page_no=2,
            order=1,
            text="例1 已知角α…",
            bbox=[100, 100, 600, 200],
            meta={},
        )
    ]
    assert not looks_like_cover_page(
        2, blocks, profile={"features": {"cover_pages": [1, 2]}}
    )
