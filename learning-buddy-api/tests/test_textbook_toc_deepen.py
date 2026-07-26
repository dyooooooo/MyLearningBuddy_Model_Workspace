"""Unit tests for PDF-import TOC deepen (no live vLLM)."""
from __future__ import annotations

from unittest.mock import patch

from app.services.corpus_tree_from_ir import IrSection, build_sections_from_ir
from app.services.document_ir import DocumentBlock
from app.services.textbook_toc_deepen import (
    _fill_remainder,
    _ground_label,
    _heading_first_specs,
    _ranges_to_children_specs,
    _validate_partition,
    deepen_leaf_section,
    deepen_textbook_sections,
    is_kp_fine_label,
    leaf_needs_deepen,
)


def test_kp_fine_label():
    assert is_kp_fine_label("1.1.1 集合的含义")
    assert not is_kp_fine_label("1.1 集合初步")


def test_ranges_to_children_specs_grounds_label():
    parent = IrSection(
        section_id="sec:ls",
        label="1.1 集合初步",
        summary="",
        text="",
        level=2,
        blocks=[
            DocumentBlock(id="a", type="heading", page_no=1, order=1, text="集合与元素的概念"),
            DocumentBlock(id="b", type="paragraph", page_no=1, order=2, text="元素是确定的"),
            DocumentBlock(id="c", type="paragraph", page_no=2, order=3, text="有限集无限集"),
        ],
        meta={"section_kind": "lesson"},
    )
    catalog = [
        {"i": 0, "id": "a", "page": 1, "type": "heading", "text": "集合与元素的概念"},
        {"i": 1, "id": "b", "page": 1, "type": "paragraph", "text": "元素是确定的"},
        {"i": 2, "id": "c", "page": 2, "type": "paragraph", "text": "有限集无限集"},
    ]
    # Invented AI label must be replaced by real heading text in range
    specs = _ranges_to_children_specs(
        [
            {"label": "瞎编的标题XYZ", "i_start": 0, "i_end": 1},
            {"label": "有限集无限集", "i_start": 2, "i_end": 2},
        ],
        catalog,
        parent,
    )
    assert specs[0]["block_ids"] == ["a", "b"]
    assert "瞎编" not in specs[0]["label"]
    assert "集合与元素" in specs[0]["label"]
    assert specs[1]["block_ids"] == ["c"]


def test_validate_partition_rejects_gap():
    parent = IrSection(
        section_id="s",
        label="1.1",
        summary="",
        text="",
        level=2,
        blocks=[
            DocumentBlock(id="a", type="paragraph", page_no=1, order=1, text="a"),
            DocumentBlock(id="b", type="paragraph", page_no=1, order=2, text="b"),
        ],
    )
    ok, reason = _validate_partition(
        parent, [{"label": "x", "block_ids": ["a"]}]
    )
    assert ok is False


def test_heading_first_uses_real_headings():
    parent = IrSection(
        section_id="sec:ls",
        label="1.1 集合初步",
        summary="",
        text="",
        level=2,
        blocks=[
            DocumentBlock(id="h1", type="heading", page_no=1, order=1, text="集合与元素的概念"),
            DocumentBlock(id="p1", type="paragraph", page_no=1, order=2, text="正文甲"),
            DocumentBlock(id="h2", type="heading", page_no=2, order=3, text="元素的三个特性"),
            DocumentBlock(id="p2", type="paragraph", page_no=2, order=4, text="正文乙"),
        ],
        meta={"section_kind": "lesson"},
    )
    specs = _heading_first_specs(parent)
    assert specs is not None
    assert len(specs) >= 2
    assert specs[0]["label"] == "集合与元素的概念"
    assert "h1" in specs[0]["block_ids"]


def test_heading_first_absorbs_preamble_into_first_child():
    """Blocks before the first anchor stay with the first child, not the last."""
    parent = IrSection(
        section_id="sec:ls",
        label="1.1 集合初步",
        summary="",
        text="",
        level=2,
        blocks=[
            DocumentBlock(id="pre_fig", type="figure", page_no=8, order=1, text=""),
            DocumentBlock(id="pre_p", type="paragraph", page_no=8, order=2, text="章前导语"),
            DocumentBlock(id="h1", type="heading", page_no=9, order=3, text="1 集合"),
            DocumentBlock(id="p1", type="paragraph", page_no=9, order=4, text="正文"),
            DocumentBlock(id="hb", type="heading", page_no=19, order=5, text="B组"),
            DocumentBlock(id="pb", type="paragraph", page_no=19, order=6, text="B组习题"),
        ],
        meta={"section_kind": "lesson"},
    )
    specs = _heading_first_specs(parent)
    assert specs is not None
    assert "pre_fig" in specs[0]["block_ids"]
    assert "pre_p" in specs[0]["block_ids"]
    last = specs[-1]
    assert last["label"] == "B组" or "B组" in last["label"]
    assert "pre_fig" not in last["block_ids"]
    assert "pre_p" not in last["block_ids"]


def test_fill_remainder_attaches_to_nearest_previous_not_last():
    parent = IrSection(
        section_id="sec:ls",
        label="1.1",
        summary="",
        text="",
        level=2,
        blocks=[
            DocumentBlock(id="early", type="paragraph", page_no=8, order=1, text="导语"),
            DocumentBlock(id="a", type="heading", page_no=9, order=2, text="1 集合"),
            DocumentBlock(id="b", type="paragraph", page_no=9, order=3, text="正文"),
            DocumentBlock(id="c", type="heading", page_no=19, order=4, text="B组"),
            DocumentBlock(id="d", type="paragraph", page_no=19, order=5, text="B题"),
        ],
    )
    # Simulate AI/heading specs that skipped the early preamble block
    specs = _fill_remainder(
        parent,
        [
            {"label": "1 集合", "block_ids": ["a", "b"]},
            {"label": "B组", "block_ids": ["c", "d"]},
        ],
    )
    assert "early" in specs[0]["block_ids"]
    assert "early" not in specs[1]["block_ids"]
    assert specs[1]["label"] == "B组" or specs[1]["label"].startswith("B组")


def test_ground_label_rejects_invention():
    blocks = [
        DocumentBlock(id="a", type="paragraph", page_no=1, order=1, text="集合的元素是确定的"),
    ]
    lab = _ground_label("完全虚构的标题", blocks, parent_label="1.1 集合初步", child_index=0)
    assert "虚构" not in lab
    assert "确定" in lab or lab.startswith("1.1.1")


def test_leaf_needs_deepen_coarse_lesson():
    sec = IrSection(
        section_id="sec:1",
        label="1.1 集合初步",
        summary="",
        text="例1 aaa 例2 bbb " + ("内容" * 200),
        level=2,
        blocks=[
            DocumentBlock(id="a", type="paragraph", page_no=1, order=1, text="例1 aaa"),
            DocumentBlock(id="b", type="paragraph", page_no=1, order=2, text="例2 bbb"),
        ],
        meta={"section_kind": "lesson"},
    )
    assert leaf_needs_deepen(sec)


def test_deepen_leaf_applies_children():
    parent = IrSection(
        section_id="sec:ls",
        label="1.1 集合初步",
        summary="",
        text="",
        level=2,
        blocks=[
            DocumentBlock(id="a", type="paragraph", page_no=1, order=1, text="集合概念"),
            DocumentBlock(id="b", type="paragraph", page_no=1, order=2, text="集合表示"),
        ],
        meta={"section_kind": "lesson"},
    )
    fake = {
        "children": [
            {"label": "集合概念", "primary_kp": "kp:set", "block_ids": ["a"]},
            {"label": "集合表示", "primary_kp": "kp:set-repr", "block_ids": ["b"]},
        ]
    }
    with patch(
        "app.services.textbook_toc_deepen.ai_unify_outline_for_leaf",
        return_value=fake,
    ):
        pieces, meta = deepen_leaf_section(
            parent, prefix="tb:t", path_labels=["书", "1.1 集合初步"]
        )
    assert meta["deepened"] is True
    assert len(pieces) == 3  # parent container + 2 children
    leaves = [p for p in pieces if p.chunks]
    assert len(leaves) == 2
    assert all(c["meta"]["chunk_kind"] == "toc_leaf" for p in leaves for c in p.chunks)
    assert leaves[0].meta.get("grounded") is True
    assert leaves[0].meta.get("start_page") == 1


def test_deepen_failure_marks_review():
    parent = IrSection(
        section_id="sec:ls",
        label="1.1 集合初步",
        summary="",
        text="例1 x 例2 y " + ("z" * 500),
        level=2,
        blocks=[
            DocumentBlock(id="a", type="paragraph", page_no=1, order=1, text="例1 x"),
            DocumentBlock(id="b", type="paragraph", page_no=1, order=2, text="例2 y"),
        ],
        meta={"section_kind": "lesson"},
    )
    with patch(
        "app.services.textbook_toc_deepen.ai_unify_outline_for_leaf",
        return_value=None,
    ), patch(
        "app.services.textbook_toc_deepen._heading_first_specs",
        return_value=None,
    ):
        pieces, meta = deepen_leaf_section(
            parent, prefix="tb:t", path_labels=["书", "1.1"]
        )
    assert meta["needs_structure_review"] is True
    assert pieces[0].meta.get("needs_toc_deepen") is True
    assert len(pieces[0].chunks) == 1


def test_build_then_deepen_preserves_chapter():
    blocks = [
        DocumentBlock(id="ch", type="title", page_no=1, order=1, text="第一章 集合", level=1),
        DocumentBlock(id="ls", type="heading", page_no=1, order=2, text="1.1 集合初步", level=2),
        DocumentBlock(id="p", type="paragraph", page_no=1, order=3, text="正文" * 100),
    ]
    _, sections = build_sections_from_ir(
        blocks, textbook_id="tb:t", subject="math", grade_band="hs"
    )
    with patch(
        "app.services.textbook_toc_deepen.leaf_needs_deepen",
        return_value=False,
    ):
        out, report = deepen_textbook_sections(sections, prefix="tb:t", textbook_label="书")
    assert report["structure_status"] == "structure_ready"
    assert any(s.meta.get("section_kind") == "chapter" for s in out)
