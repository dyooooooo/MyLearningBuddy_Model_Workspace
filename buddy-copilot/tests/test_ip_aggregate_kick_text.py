"""单簇聚合踢给 Agent 的用户文案必须带簇#标签。"""

from __future__ import annotations

from app.main import _ip_intent_user_text, _normalize_aggregate_payload


def test_normalize_single_cluster_keeps_label() -> None:
    pl = _normalize_aggregate_payload(
        {
            "intent": "aggregate",
            "process_all": False,
            "member_ids": ["a", "b"],
            "aggregate_cluster_index": 1,
            "aggregate_cluster_label": "簇#1",
            "aggregate_group_id": "g1",
        }
    )
    assert pl["process_all"] is False
    assert pl["member_ids"] == ["a", "b"]
    assert pl["aggregate_cluster_label"] == "簇#1"


def test_normalize_all_clears_cluster_fields() -> None:
    pl = _normalize_aggregate_payload(
        {
            "intent": "aggregate",
            "process_all": True,
            "member_ids": ["a", "b"],
            "aggregate_cluster_label": "簇#1",
        }
    )
    assert pl["process_all"] is True
    assert "member_ids" not in pl
    assert "aggregate_cluster_label" not in pl


def test_kick_text_includes_cluster_label() -> None:
    text = _ip_intent_user_text(
        {
            "intent": "aggregate",
            "process_all": False,
            "member_ids": ["id-a", "id-b"],
            "aggregate_cluster_index": 1,
            "aggregate_cluster_label": "簇#1",
            "aggregate_group_id": "gid-x",
        }
    )
    assert "簇#1" in text
    assert "仅簇#1" in text
    assert "id-a" in text
    assert "禁止" in text
    assert "处理全部聚合" not in text


def test_kick_text_all_clusters() -> None:
    text = _ip_intent_user_text({"intent": "aggregate", "process_all": True})
    assert "处理全部聚合" in text
    assert "簇#1" in text  # 说明按序号汇报
