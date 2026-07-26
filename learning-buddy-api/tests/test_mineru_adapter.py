"""Unit tests for MinerU → IR adapter (v1 + content_list_v2)."""
from __future__ import annotations

from app.services.ir.mineru_adapter import mineru_content_list_to_ir
from app.services.parsing.adapters.mineru_cloud import (
    _flatten_content_list_v2,
    _page_limit_to_ranges,
    remap_relative_page_idx,
    resolve_page_ranges,
)


def test_page_limit_to_ranges():
    assert _page_limit_to_ranges(None) is None
    assert _page_limit_to_ranges(0) is None
    assert _page_limit_to_ranges(3) == "1-3"
    assert _page_limit_to_ranges(1) == "1-1"


def test_resolve_page_ranges_prefers_explicit():
    assert resolve_page_ranges("21-40", 10) == "21-40"
    assert resolve_page_ranges(None, 10) == "1-10"
    assert resolve_page_ranges("", None) is None


def test_remap_relative_page_idx():
    blocks = [{"page_idx": 0, "type": "text"}, {"page_idx": 1, "type": "text"}]
    out = remap_relative_page_idx(blocks, "21-40")
    assert out[0]["page_idx"] == 20
    assert out[1]["page_idx"] == 21
    # already absolute
    abs_blocks = [{"page_idx": 20}, {"page_idx": 21}]
    assert remap_relative_page_idx(abs_blocks, "21-40")[0]["page_idx"] == 20


def test_flatten_content_list_v2_injects_page_idx():
    pages = [
        [{"type": "title", "content": {"title_content": [{"type": "text", "content": "A"}]}, "bbox": [1, 2, 3, 4]}],
        [{"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "B"}]}, "bbox": [1, 2, 3, 4]}],
    ]
    flat = _flatten_content_list_v2(pages)
    assert len(flat) == 2
    assert flat[0]["page_idx"] == 0
    assert flat[1]["page_idx"] == 1


def test_mineru_sort_and_types():
    blocks = [
        {"page_idx": 0, "type": "text", "text": "1.1 集合初步", "bbox": [0, 200, 100, 220]},
        {"page_idx": 0, "type": "text", "text": "第一章 集合", "bbox": [0, 50, 100, 80]},
        {
            "page_idx": 0,
            "type": "table",
            "table_body": "<table><tr><td>表1-1</td></tr></table>",
            "bbox": [0, 300, 100, 400],
        },
        {
            "page_idx": 0,
            "type": "image",
            "img_path": "/api/v1/studio/proxy/import/asset?key=corpus%2Fimports%2Fx%2Ffig.jpg",
            "bbox": [0, 410, 100, 500],
        },
    ]
    ir = mineru_content_list_to_ir(blocks)
    assert len(ir) == 4
    assert ir[0].type == "title"
    assert ir[1].type == "heading"
    assert ir[2].type == "table"
    assert ir[3].type == "figure"
    assert ir[3].asset_key == "corpus/imports/x/fig.jpg"


def test_mineru_v2_title_paragraph_equation():
    pages = [
        [
            {
                "type": "title",
                "content": {
                    "title_content": [{"type": "text", "content": "第一章 集合"}],
                    "level": 1,
                },
                "bbox": [10, 20, 900, 60],
            },
            {
                "type": "paragraph",
                "content": {
                    "paragraph_content": [{"type": "text", "content": "集合是…"}],
                },
                "bbox": [10, 80, 900, 120],
            },
            {
                "type": "equation_interline",
                "content": {
                    "math_content": [{"type": "text", "content": "A \\cup B"}],
                    "math_type": "latex",
                },
                "bbox": [10, 140, 400, 180],
            },
            {
                "type": "page_number",
                "content": {"page_number_content": [{"type": "text", "content": "12"}]},
                "bbox": [500, 900, 550, 930],
            },
        ]
    ]
    flat = _flatten_content_list_v2(pages)
    ir = mineru_content_list_to_ir(flat)
    assert len(ir) == 4  # title, paragraph, equation, page_number kept
    assert ir[0].type == "title"
    assert ir[0].text == "第一章 集合"
    assert ir[0].level == 1
    assert ir[0].page_no == 1
    assert ir[1].type == "paragraph"
    assert ir[1].text == "集合是…"
    assert ir[2].type == "equation"
    assert "A \\cup B" in (ir[2].text or "")
    assert ir[3].meta.get("mineru_type") == "page_number"
    assert ir[3].meta.get("layout_zone") == "running"
    assert ir[3].meta.get("pedagogy_role") == "page_number"
    assert ir[3].text == "12"


def test_mineru_v2_list_and_image():
    flat = _flatten_content_list_v2(
        [
            [
                {
                    "type": "list",
                    "content": {"list_items": ["项一", "项二"]},
                    "bbox": [0, 10, 100, 50],
                },
                {
                    "type": "image",
                    "sub_type": "seal",
                    "content": {
                        "img_path": "/api/v1/studio/proxy/import/asset?key=corpus%2Fimports%2Fy%2Fseal.jpg",
                        "image_caption": [{"type": "text", "content": "图1"}],
                    },
                    "bbox": [0, 60, 100, 120],
                },
            ]
        ]
    )
    ir = mineru_content_list_to_ir(flat)
    assert len(ir) == 2
    assert ir[0].type == "list"
    assert "项一" in (ir[0].text or "")
    assert ir[1].type == "figure"
    assert ir[1].asset_key == "corpus/imports/y/seal.jpg"
    assert ir[1].meta.get("mineru_sub_type") == "seal"


def test_mineru_v2_index_item_content():
    flat = [
        {
            "page_idx": 5,
            "type": "index",
            "content": {
                "list_type": "text_list",
                "list_items": [
                    {
                        "item_type": "text",
                        "item_content": [{"type": "text", "content": "第1章 集合与逻辑"}],
                    },
                    {
                        "item_type": "text",
                        "item_content": [{"type": "text", "content": "1.1 集合初步 2"}],
                    },
                ],
            },
            "bbox": [100, 100, 800, 400],
        }
    ]
    ir = mineru_content_list_to_ir(flat)
    assert len(ir) == 1
    assert ir[0].type == "list"
    assert ir[0].meta.get("pedagogy_role") == "toc"
    assert ir[0].meta.get("toc_source") == "index"
    entries = ir[0].meta.get("toc_entries") or []
    assert entries[0]["label"] == "第1章 集合与逻辑"
    assert entries[0]["is_chapter"] is True
    assert entries[1]["label"] == "1.1 集合初步"
    assert entries[1]["print_page"] == 2
    assert "1.1 集合初步" in (ir[0].text or "")


def test_mineru_vlm_text_toc_not_truncated_to_chapter():
    """VLM emits TOC as type=text; must not collapse to ``第1章`` only."""
    blob = (
        "第1章 集合与逻辑\n"
        "1.1 集合初步 2\n"
        "1.2 常用逻辑用语 13\n"
        "内容提要 20\n"
        "复习题 20\n"
        "\n"
        "第2章 等式与不等式\n"
        "2.1 等式与不等式的性质 24"
    )
    flat = [
        {"page_idx": 5, "type": "text", "text": "目录", "bbox": [800, 60, 940, 100]},
        {"page_idx": 5, "type": "text", "text": blob, "bbox": [90, 160, 890, 900]},
        {"page_idx": 5, "type": "page_number", "text": "1", "bbox": [890, 960, 902, 984]},
        {
            "page_idx": 6,
            "type": "header",
            "text": "目录",
            "bbox": [115, 35, 152, 51],
        },
        {
            "page_idx": 6,
            "type": "text",
            "text": "4.3 对数函数 93\n内容提要 102\n复习题 102",
            "bbox": [230, 96, 907, 190],
        },
        {
            "page_idx": 6,
            "type": "text",
            "text": "第5章 函数的概念、性质及应用",
            "bbox": [109, 231, 566, 259],
        },
        {
            "page_idx": 6,
            "type": "text",
            "text": "5.1 函数 106\n\\*5.4 反函数 136\n复习题 142",
            "bbox": [230, 275, 909, 448],
        },
    ]
    ir = mineru_content_list_to_ir(flat)
    toc_blocks = [b for b in ir if (b.meta or {}).get("pedagogy_role") == "toc"]
    assert len(toc_blocks) >= 2
    page6 = [b for b in ir if b.page_no == 7]
    # page_idx 5 → page_no 6
    page5_toc = [b for b in ir if b.page_no == 6 and (b.meta or {}).get("pedagogy_role") == "toc"]
    assert page5_toc
    entries = page5_toc[0].meta.get("toc_entries") or []
    labels = [e["label"] for e in entries]
    assert "第1章 集合与逻辑" in labels
    assert "1.1 集合初步" in labels
    assert any(e.get("print_page") == 2 for e in entries)
    assert "1.2 常用逻辑用语" in labels
    assert "第2章 等式与不等式" in labels
    # Must keep full outline — not chapter-title truncation
    assert "1.1 集合初步" in (page5_toc[0].text or "")
    assert page5_toc[0].meta.get("toc_source") == "vlm_text"

    # Continuation page: bare chapter + section lines promoted
    page6_toc = [b for b in page6 if (b.meta or {}).get("pedagogy_role") == "toc"]
    assert page6_toc
    joined = "\n".join(b.text or "" for b in page6_toc)
    assert "第5章 函数的概念、性质及应用" in joined
    assert "5.1 函数" in joined
    assert "*5.4 反函数" in joined or "5.4 反函数" in joined


def test_mineru_v2_image_source_path():
    """Cloud content_list_v2 nests path under content.image_source.path."""
    flat = [
        {
            "page_idx": 11,
            "type": "image",
            "content": {
                "image_source": {
                    "path": "images/abc123.jpg",
                },
                "image_caption": [
                    {"type": "text", "content": "(a,b)"},
                    {"type": "text", "content": "图 1-1-1"},
                ],
                "image_footnote": [],
            },
            "bbox": [100, 200, 300, 400],
        }
    ]
    prefix = "corpus/imports/job/assets"
    ir = mineru_content_list_to_ir(flat, asset_prefix=prefix)
    assert len(ir) == 1
    assert ir[0].type == "figure"
    assert ir[0].page_no == 12
    assert ir[0].asset_key == f"{prefix}/mineru/abc123.jpg"
    assert ir[0].caption == "图 1-1-1"


def test_mineru_v2_image_footnote_as_caption():
    """Empty image_caption + image_footnote → figcaption text."""
    flat = [
        {
            "page_idx": 8,
            "type": "image",
            "content": {
                "image_source": {
                    "path": "images/cantor.jpg",
                },
                "image_caption": [],
                "image_footnote": [
                    {
                        "type": "text",
                        "content": "康托(G. Cantor，1845—1918)，德国数学家，集合论创始人.",
                    }
                ],
            },
            "bbox": [161, 302, 260, 412],
        }
    ]
    ir = mineru_content_list_to_ir(flat, asset_prefix="corpus/imports/job/assets")
    assert len(ir) == 1
    assert ir[0].type == "figure"
    assert "康托" in (ir[0].caption or "")
    assert "集合论创始人" in (ir[0].caption or "")

