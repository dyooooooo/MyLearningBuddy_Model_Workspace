"""Batch repair heuristics: caption hoist, figure demote, TOC, aside."""
from __future__ import annotations

from app.import_35b.harness_golden import assert_case_expectations, compile_case
from app.import_35b.normalize import normalize_blueprint
from app.import_35b.page_compiler import compile_page_html
from app.import_35b.payload_heuristics import (
    figure_caption_should_demote,
    should_reroute_aside_left,
    table_looks_like_toc,
)
from app.import_35b.schemas import PageBlueprint
from app.import_35b.validation import run_page_checks


def test_hoist_text_kind_figure_caption_clears_main_empty():
    case = {
        "id": "p3_text_kind_figure_caption",
        "page_no": 3,
        "kind": "text_with_figure_caption",
        "caption": "主编 李大潜 王建磐",
        "expect": {
            "html_must_contain": ["主编"],
            "no_main_empty": True,
            "no_figure_tag": True,
        },
    }
    bp, html = compile_case(case)
    assert_case_expectations(case, bp, html)
    assert bp.blocks[0].kind == "text"
    assert bp.blocks[0].content.text_paragraphs() == ["主编 李大潜 王建磐"]


def test_figure_with_asset_demotes_toc_caption():
    case = {
        "id": "p6_figure_with_asset_toc_caption",
        "page_no": 6,
        "kind": "figure",
        "caption": "1.1 集合初步",
        "asset_url": "/proxy35b/fake.jpg",
        "expect": {"no_figure_tag": True, "html_must_contain": ["集合初步"]},
    }
    bp, html = compile_case(case)
    assert_case_expectations(case, bp, html)
    assert "<img" not in html


def test_toc_table_renders_as_list():
    case = {
        "id": "p7_toc_as_table",
        "page_no": 7,
        "kind": "table",
        "table_rows": [
            ["第 1 章 集合与逻辑", "1"],
            ["1.1 集合初步", "2"],
            ["1.2 常用逻辑用语", "13"],
        ],
        "expect": {"no_table_tag": True, "toc_list": True},
    }
    bp, html = compile_case(case)
    assert_case_expectations(case, bp, html)
    assert bp.blocks[0].role == "toc"


def test_aside_gutter_reroute():
    case = {
        "id": "p9_aside_gutter",
        "page_no": 9,
        "kind": "aside_gutter",
        "bbox": [80, 200, 250, 600],
        "paragraphs": ["康托(G. Cantor, 1845-1918), 德国数学家。"],
        "expect": {"aside_left": True, "html_must_contain": ["康托"]},
    }
    bp, html = compile_case(case)
    assert_case_expectations(case, bp, html)


def test_heuristics_helpers():
    assert figure_caption_should_demote("1.1 集合初步")
    assert not figure_caption_should_demote("图 1-1-1")
    assert table_looks_like_toc([["a", "1"], ["b", "2"], ["c", "3"]])
    assert should_reroute_aside_left([80, 200, 250, 600], zone="main")
    assert not should_reroute_aside_left([100, 100, 900, 400], zone="main")


def test_real_figure_caption_still_renders_figure():
    raw = {
        "page_no": 21,
        "page_size": [1000, 1000],
        "blocks": [
            {
                "id": "f1",
                "kind": "figure",
                "zone": "main",
                "role": "figure",
                "bbox": [120, 120, 900, 320],
                "content": {"figure": {"caption": "图 1-1-1"}, "asset_url": "/x.jpg"},
            }
        ],
    }
    bp = normalize_blueprint(PageBlueprint.model_validate(raw))
    html = compile_page_html(bp)
    assert "<figure" in html
    assert "图 1-1-1" in html
