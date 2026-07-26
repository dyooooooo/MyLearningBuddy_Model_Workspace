"""Tests for layout_enrich (slim, post-refine)."""
import json
from pathlib import Path

from app.schemas.mineru_refine import PageRefineResult
from app.services.document_ir import DocumentBlock
from app.services.ir.apply_refine import apply_page_refine
from app.services.ir.layout_enrich import enrich_blocks, normalize_bbox_to_1000


def _fixture_page12() -> PageRefineResult:
    path = Path(__file__).resolve().parents[1] / "data/eval/mineru-refine-page12.json"
    return PageRefineResult.model_validate(json.loads(path.read_text(encoding="utf-8")))


def test_normalize_bbox_pdf_points():
    nb = normalize_bbox_to_1000([59.0, 100.0, 300.0, 120.0], 596.0, 843.0)
    assert nb is not None
    assert nb[0] < 150
    assert nb[2] > nb[0]


def test_enrich_assigns_zones():
    blocks = [
        DocumentBlock(
            id="a",
            type="paragraph",
            page_no=12,
            order=1,
            bbox=[710, 700, 860, 730],
            text='符号"∞"读作"无穷大"',
            meta={"engine": "mineru_cloud"},
        ),
        DocumentBlock(
            id="b",
            type="paragraph",
            page_no=12,
            order=2,
            bbox=[129, 100, 500, 130],
            text="例4 选择适当的方法",
            meta={"engine": "mineru_cloud"},
        ),
    ]
    blocks = apply_page_refine(blocks, _fixture_page12())
    enriched = enrich_blocks(blocks, pdf_bytes=None)
    zones = {b.id: b.meta.get("layout_zone") for b in enriched}
    assert zones["a"] == "aside_right"
    assert zones["b"] == "main"
    aside = next(b for b in enriched if b.id == "a")
    assert aside.meta.get("anchor_block_id") == "b"


def test_figure_group_meta():
    blocks = [
        DocumentBlock(
            id="f1",
            type="figure",
            page_no=12,
            order=1,
            bbox=[166, 565, 342, 620],
            asset_url="/x/a.jpg",
            meta={"engine": "mineru_cloud", "layout_zone": "main"},
        ),
        DocumentBlock(
            id="f2",
            type="figure",
            page_no=12,
            order=2,
            bbox=[415, 565, 589, 620],
            caption="图1-1-1",
            asset_url="/x/b.jpg",
            meta={"engine": "mineru_cloud", "layout_zone": "main"},
        ),
    ]
    enriched = enrich_blocks(blocks, pdf_bytes=None)
    gids = {b.meta.get("figure_group_id") for b in enriched if b.type == "figure"}
    assert len(gids) == 1
    assert None not in gids
