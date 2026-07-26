"""Import vs textbook tree separation."""

from unittest.mock import MagicMock

from app.schemas.corpus import TreeNodeOut
from app.services.corpus_tree import (
    _hide_from_textbook_tree,
    _tree_meta_for_sidebar,
    _tree_sort_key,
)


def test_hide_toc_leaf_chunk_from_textbook_tree():
    """Same-label toc_leaf under a section must not appear in textbook TOC."""
    chunk = MagicMock()
    chunk.node_type = "chunk"
    chunk.id = "chunk:tb:1-集合:0"
    chunk.label = "1 集合"
    chunk.meta_json = '{"chunk_kind":"toc_leaf","start_page":9}'
    assert _hide_from_textbook_tree(chunk) is True

    section = MagicMock()
    section.node_type = "section"
    section.id = "sec:tb:kp:1-集合"
    section.label = "1 集合"
    section.meta_json = '{"start_page":9,"section_kind":"lesson"}'
    assert _hide_from_textbook_tree(section) is False


def test_tree_meta_includes_block_ids_for_locate():
    node = MagicMock()
    node.meta_json = '{"start_page":10,"block_ids":["p10_b1","p10_b2"],"chunk_kind":"toc_leaf"}'
    meta = _tree_meta_for_sidebar(node)
    assert meta["block_ids"] == ["p10_b1", "p10_b2"]
    assert meta["start_page"] == 10


def test_tree_sort_uses_start_page():
    a = TreeNodeOut(id="s1", label="第一章", node_type="section", meta={"start_page": 10})
    b = TreeNodeOut(id="s2", label="前言", node_type="section", meta={"start_page": 1})
    c = TreeNodeOut(id="s3", label="第二章", node_type="section", meta={"start_page": 20})
    ordered = sorted([a, b, c], key=_tree_sort_key)
    assert [n.label for n in ordered] == ["前言", "第一章", "第二章"]


def test_page_labels_numeric_order():
    pages = [
        TreeNodeOut(id="chunk:tb:p0010", label="10", node_type="chunk", meta={"source_page": 10}),
        TreeNodeOut(id="chunk:tb:p0002", label="2", node_type="chunk", meta={"source_page": 2}),
        TreeNodeOut(id="chunk:tb:p0001", label="1", node_type="chunk", meta={"source_page": 1}),
    ]
    ordered = sorted(pages, key=_tree_sort_key)
    assert [n.label for n in ordered] == ["1", "2", "10"]
