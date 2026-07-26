"""Tests for Cloud content_list / layout page slicing in corpus debug bundle."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.corpus_debug_bundle import (
    slice_cloud_layout_page,
    slice_cloud_page_blocks,
)


def test_slice_flat_content_list():
    job = SimpleNamespace(meta_json="{}", minio_key="corpus/imports/x/a.pdf")
    raw = [
        {"page_idx": 0, "type": "text", "text": "p1"},
        {"page_idx": 1, "type": "text", "text": "p2a"},
        {"page_idx": 1, "type": "text", "text": "p2b"},
    ]
    assert len(slice_cloud_page_blocks(job, 1, raw_blocks=raw)) == 1
    assert len(slice_cloud_page_blocks(job, 2, raw_blocks=raw)) == 2
    assert slice_cloud_page_blocks(job, 3, raw_blocks=raw) == []


def test_slice_v2_grouped():
    job = SimpleNamespace(meta_json="{}", minio_key="corpus/imports/x/a.pdf")
    raw = [
        [{"type": "title", "content": {"title_content": "A"}}],
        [{"type": "paragraph", "content": {"paragraph_content": "B"}}],
    ]
    p1 = slice_cloud_page_blocks(job, 1, raw_blocks=raw)
    p2 = slice_cloud_page_blocks(job, 2, raw_blocks=raw)
    assert p1 and p1[0]["type"] == "title"
    assert p2 and p2[0]["type"] == "paragraph"


def test_slice_layout_pdf_info():
    parsed = {
        "_backend": "pipeline",
        "_version_name": "2.5",
        "pdf_info": [
            {"page_idx": 0, "page_size": [612, 792], "para_blocks": [{"type": "text"}]},
            {"page_idx": 1, "page_size": [612, 792], "para_blocks": [{"type": "title"}]},
        ],
    }
    out = slice_cloud_layout_page(parsed, 2)
    assert out is not None
    assert out["_slice"]["found"] is True
    assert out["_slice"]["total_pages"] == 2
    assert len(out["pdf_info"]) == 1
    assert out["pdf_info"][0]["page_idx"] == 1
    assert out["_backend"] == "pipeline"


def test_slice_layout_missing_page():
    parsed = {"pdf_info": [{"page_idx": 0, "para_blocks": []}]}
    out = slice_cloud_layout_page(parsed, 9)
    assert out is not None
    assert out["_slice"]["found"] is False
    assert out["pdf_info"] == []
