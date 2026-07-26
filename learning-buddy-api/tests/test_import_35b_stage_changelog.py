"""Tests for stage changelog diff."""
from __future__ import annotations

from app.import_35b.stage_changelog import build_stage_changelog


def test_changelog_detects_boundary_repair_transition():
    raw = {
        "page_no": 1,
        "blocks": [
            {
                "id": "p1_b1",
                "kind": "equation",
                "content": {"equation": {"latex": "\x08eta"}},
            }
        ],
    }
    repaired = {
        "page_no": 1,
        "blocks": [
            {
                "id": "p1_b1",
                "kind": "equation",
                "content": {"equation": {"latex": "\\beta"}},
            }
        ],
    }
    changelog = build_stage_changelog(
        1,
        {
            "raw_model": raw,
            "repaired_model": repaired,
            "post_repair": repaired,
            "post_mfr": repaired,
            "post_crop": repaired,
            "blueprint": repaired,
        },
        boundary_repairs=[{"rule": "ctrl_b", "command": "\\beta", "damaged": "'\\x08eta'"}],
    )
    assert changelog["total_changes"] >= 1
    tr = changelog["by_transition"]["raw_model→repaired_model"]
    assert tr["count"] >= 1
    assert "后处理" in changelog["summary_zh"] or changelog["total_changes"] > 0


def test_changelog_no_changes_summary():
    bp = {"page_no": 2, "blocks": [{"id": "p2_b1", "kind": "text", "content": {"text": {"paragraphs": ["hi"]}}}]}
    changelog = build_stage_changelog(
        2,
        {
            "raw_model": bp,
            "repaired_model": bp,
            "post_repair": bp,
            "post_mfr": bp,
            "post_crop": bp,
            "blueprint": bp,
        },
    )
    assert changelog["total_changes"] == 0
    assert "未被后处理修改" in changelog["summary_zh"]


def test_changelog_normalize_segment_change():
    before = {
        "page_no": 3,
        "blocks": [
            {
                "id": "p3_b1",
                "kind": "text",
                "content": {"text": {"paragraphs": ["$x$"]}},
            }
        ],
    }
    after = {
        "page_no": 3,
        "blocks": [
            {
                "id": "p3_b1",
                "kind": "text",
                "content": {
                    "text": {
                        "paragraphs": ["$x$"],
                        "segments": [{"kind": "math", "raw": "$x$", "latex": "x", "valid": True}],
                    }
                },
            }
        ],
    }
    changelog = build_stage_changelog(
        3,
        {
            "raw_model": before,
            "repaired_model": before,
            "post_repair": before,
            "post_mfr": before,
            "post_crop": before,
            "blueprint": after,
        },
    )
    crop_to_final = changelog["by_transition"]["post_crop→blueprint"]
    assert crop_to_final["count"] >= 1
