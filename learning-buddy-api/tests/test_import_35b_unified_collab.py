"""Unit tests for MinerU+35B unified collab (profile, layout hints, triage)."""
from __future__ import annotations

from app.import_35b.cursor_pack.triage import triage_feedback
from app.import_35b.mineru_layout import LayoutIndex, _group_content_list
from app.import_35b.profile_features import aggregate_features, merge_runtime_profile
from app.import_35b.vision_layout import PROMPT_VERSION, _user_payload


def test_prompt_version_is_v6():
    assert PROMPT_VERSION == "layout_blueprint_v6"


def test_aggregate_features_aside_and_running():
    stats = {
        "type_histogram": {
            "aside_text": 3,
            "header": 2,
            "footer": 1,
            "equation": 10,
            "text": 20,
        },
        "has_aside": True,
        "has_running_header": True,
        "has_footer": True,
    }
    feats = aggregate_features(stats)
    assert feats["aside"] == "both"
    assert feats["running_header_footer"] is True
    assert feats["math_density"] == "high"


def test_aggregate_features_no_aside():
    feats = aggregate_features({"type_histogram": {"text": 5}, "has_aside": False})
    assert feats["aside"] == "none"


def test_merge_runtime_profile_defaults():
    profile = merge_runtime_profile(
        "hujiao_math_hs_v1",
        {"type_histogram": {"aside_text": 1}, "has_aside": True},
    )
    assert profile["features"]["aside"] == "both"
    assert "zones" in profile


def test_group_content_list_by_page():
    content = [
        {"page_idx": 0, "type": "text", "text": "hello", "bbox": [10, 10, 100, 50]},
        {"page_idx": 0, "type": "aside_text", "text": "note", "bbox": [0, 100, 80, 400]},
        {"page_idx": 1, "type": "header", "text": "hdr", "bbox": [10, 5, 900, 40]},
    ]
    by_page, stats = _group_content_list(content)
    assert 1 in by_page and 2 in by_page
    assert len(by_page[1]) == 2
    assert stats["has_aside"] is True
    assert stats["has_running_header"] is True
    assert by_page[1][0]["bbox"] is not None


def test_user_payload_includes_layout_hints():
    text = _user_payload(
        3,
        layout_hints=[{"id": "p3_m0", "type": "paragraph", "bbox": [1, 2, 3, 4]}],
        profile={"profile_id": "hujiao_math_hs_v1", "zones": {"aside_left_x1_max": 280}},
    )
    assert "MinerU layout JSON" in text
    assert "p3_m0" in text
    assert "Profile hints" in text
    assert "aside_left_x1_max" in text


def test_user_payload_without_hints():
    text = _user_payload(1)
    assert "MinerU layout JSON" not in text
    assert '"page_no": 1' in text


def test_triage_layout_tag():
    r = triage_feedback(t1_snapshot=[], user_body="栏乱了", tags=["layout"])
    assert r.layer == "layout"
    assert any("mineru_layout" in f for f in r.suggested_files)


def test_triage_aside_in_main():
    r = triage_feedback(
        t1_snapshot=[{"suspect_type": "ASIDE_IN_MAIN"}],
        user_body="",
    )
    assert r.layer == "layout"
    assert r.confidence == "high"


def test_layout_index_default_empty():
    idx = LayoutIndex()
    assert idx.by_page == {}
    assert idx.stats == {}
