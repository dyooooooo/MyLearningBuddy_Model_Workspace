"""「继续」意图识别 + 未完成断点判定。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.continue_intent import is_continue_request  # noqa: E402
from app.loop import LoopEngine  # noqa: E402


def test_continue_phrases() -> None:
    assert is_continue_request("继续")
    assert is_continue_request("接着做")
    assert is_continue_request("重连")
    assert is_continue_request("再试一次")
    assert is_continue_request("continue")
    assert is_continue_request("从断点继续")
    assert is_continue_request("继续预览")


def test_not_continue() -> None:
    assert not is_continue_request("")
    assert not is_continue_request("为什么失败了？")
    assert not is_continue_request("解释一下三角裁决是什么意思")
    assert not is_continue_request("用方案 A")
    # 过长不当口令
    assert not is_continue_request("继续" + ("啊" * 100))


def test_unfinished_pipeline_segment() -> None:
    eng = LoopEngine.__new__(LoopEngine)
    session = {
        "kind": "import_advice",
        "payload": {
            "minio_key": "corpus/x.pdf",
            "page_count": 178,
            "segment_next_page": 85,
        },
    }
    assert eng._unfinished_pipeline(session) == "segment"


def test_unfinished_pipeline_open_issues() -> None:
    eng = LoopEngine.__new__(LoopEngine)
    session = {
        "kind": "import_advice",
        "payload": {
            "minio_key": "corpus/x.pdf",
            "page_count": 40,
            "segment_next_page": 21,
            "open_issues": [{"page": 3, "kind": "layout", "status": "open"}],
        },
    }
    assert eng._unfinished_pipeline(session) == "segment_issues"


def test_unfinished_pipeline_none() -> None:
    eng = LoopEngine.__new__(LoopEngine)
    assert eng._unfinished_pipeline({"kind": "other", "payload": {}}) is None
    assert (
        eng._unfinished_pipeline(
            {
                "kind": "import_advice",
                "payload": {
                    "minio_key": "k",
                    "page_count": 10,
                    "segment_next_page": 11,
                    "import_job_id": "job-1",
                },
            }
        )
        is None
    )
