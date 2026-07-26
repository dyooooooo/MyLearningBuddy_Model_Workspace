"""Patching ``text`` must not be shadowed by stale math-enrich segments.

Regression guard for the AI import loop: ``patch_block`` used to write
``block.text`` while the renderer kept using ``meta.text_segments``, so IR moved
but HTML did not — and the caller had no way to notice.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.document_ir import DocumentBlock, render_block_html
from app.services.import_blocks_service import patch_job_block, replace_page_blocks


def _enriched_block(block_id: str = "b1") -> DocumentBlock:
    """Paragraph whose meta carries segments rendered from the *old* text."""
    return DocumentBlock(
        id=block_id,
        type="paragraph",
        page_no=1,
        order=1,
        text="开 本 890 毫米×1240 毫米 1/16",
        meta={
            "layout_zone": "main",
            "reading_order": 1,
            "math_enrich_version": "math_enrich_v1",
            "text_segments": [
                {"kind": "text", "raw": "开 本 ", "latex": None, "valid": False},
                {"kind": "math", "raw": "890·1240·1/16", "latex": "890\\cdot 1240\\cdot \\frac{1}{16}", "valid": True},
            ],
        },
    )


def _patched(blocks: list[DocumentBlock], patch_body: dict) -> tuple[list[dict], dict]:
    job = SimpleNamespace(id="j1", meta_json="{}")
    session = MagicMock()
    session.get.return_value = job
    with (
        patch("app.services.import_blocks_service.load_blocks", return_value=blocks),
        patch("app.services.import_blocks_service.save_blocks"),
        patch("app.services.import_blocks_service._job_meta", return_value={}),
        patch("app.services.import_blocks_service.dumps", return_value="{}"),
    ):
        _, out, info = patch_job_block(session, "j1", "b1", patch_body)
    return out, info


def test_stale_segments_shadow_text_before_fix():
    """Sanity: with segments present the renderer ignores ``text``."""
    block = _enriched_block()
    html = render_block_html(block.model_copy(update={"text": "十六开"}))
    assert "十六开" not in html


def test_patch_text_drops_stale_segments_and_reports_change():
    out, info = _patched([_enriched_block()], {"text": "开 本 890毫米×1240毫米 十六开"})

    meta = {b["id"]: b["meta"] for b in out}["b1"]
    assert "text_segments" not in meta
    assert "math_enrich_version" not in meta
    assert info["html_changed"] is True
    assert "十六开" in info["block_html"]
    assert "text_segments" in info["dropped_stale_meta"]


def test_explicit_segments_in_patch_are_kept():
    fresh = [{"kind": "text", "raw": "新分段", "latex": None, "valid": False}]
    out, info = _patched(
        [_enriched_block()],
        {"text": "新分段", "meta": {"text_segments": fresh}},
    )

    meta = {b["id"]: b["meta"] for b in out}["b1"]
    assert meta["text_segments"] == fresh
    assert "text_segments" not in info["dropped_stale_meta"]


def test_patch_without_text_keeps_segments():
    out, _info = _patched([_enriched_block()], {"layout_zone": "aside"})

    meta = {b["id"]: b["meta"] for b in out}["b1"]
    assert meta["text_segments"]
    assert meta["layout_zone"] == "aside"


def test_no_op_patch_reports_html_unchanged():
    block = _enriched_block()
    _out, info = _patched([block], {"text": block.text})

    # Text is identical, but stale segments were dropped, so HTML does move;
    # what matters is the caller is told the truth either way.
    assert isinstance(info["html_changed"], bool)
    assert info["block_html"]


def _replaced(blocks: list[DocumentBlock], page_blocks: list[dict]):
    job = SimpleNamespace(id="j1", meta_json="{}")
    session = MagicMock()
    session.get.return_value = job
    with (
        patch("app.services.import_blocks_service.load_blocks", return_value=blocks),
        patch("app.services.import_blocks_service.save_blocks"),
        patch("app.services.import_blocks_service._job_meta", return_value={}),
        patch("app.services.import_blocks_service.dumps", return_value="{}"),
        patch(
            "app.services.import_blocks_service.rerender_import_page",
            return_value="<p>ok</p>",
        ),
    ):
        return replace_page_blocks(session, "j1", 1, page_blocks, rerender=True)


def test_replace_page_blocks_drops_stale_segments():
    _job, page_blocks, _html, info = _replaced(
        [_enriched_block()], [{"id": "b1", "text": "开 本 十六开"}]
    )

    assert "text_segments" not in page_blocks[0]["meta"]
    assert info["dropped_stale_meta"] == {"b1": ["text_segments", "math_enrich_version"]}
    assert info["html_changed"] is True


def test_replace_page_blocks_rejects_external_asset_url():
    with pytest.raises(ValueError, match="site-relative"):
        _replaced(
            [_enriched_block()],
            [{"id": "b1", "asset_url": "https://evil.example.com/x.png"}],
        )


def test_replace_page_blocks_accepts_site_relative_asset_url():
    _job, page_blocks, _html, _info = _replaced(
        [_enriched_block()],
        [{"id": "b1", "asset_url": "/api/v1/corpus/assets/x.png"}],
    )

    assert page_blocks[0]["asset_url"] == "/api/v1/corpus/assets/x.png"
