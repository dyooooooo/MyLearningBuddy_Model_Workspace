"""改动权限类：非越权（edition）vs 越权（pipeline）。"""

from __future__ import annotations

from app.services.editions.authority import (
    classify_authority,
    normalize_candidate_authority,
)
from app.services.editions.schema import PolicyEntry


def test_classify_edition_by_default():
    assert classify_authority(mode="cursor_ir") == "edition"
    assert classify_authority(mode="rebuild_ir") == "edition"


def test_classify_pipeline_by_path():
    assert (
        classify_authority(
            paths=["learning-buddy-api/app/services/ir/layout/features.py"]
        )
        == "pipeline"
    )


def test_classify_edition_data_path_not_pipeline():
    assert (
        classify_authority(
            paths=[
                "learning-buddy-api/configs/editions/hujiao_math_hs_v1/layout_rules.yaml"
            ]
        )
        == "edition"
    )


def test_normalize_candidate_stamps_tags():
    cand = normalize_candidate_authority(
        {
            "id": "ai.1",
            "facet": "layout_rebuild",
            "severity": "should",
            "statement": "旁注误入主栏",
        }
    )
    assert cand["authority"] == "edition"
    assert "authority:edition" in cand["tags"]
    assert "非越权" in cand["tags"]
    PolicyEntry.model_validate(cand)


def test_normalize_pipeline_explicit():
    cand = normalize_candidate_authority(
        {
            "id": "ai.2",
            "facet": "content_correct",
            "statement": "特征量测错",
            "authority": "pipeline",
        }
    )
    assert cand["authority"] == "pipeline"
    assert "越权" in cand["tags"]
