"""三角裁决离线测试（不调用 vLLM / MinerU Cloud）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.layout_triad.compare import compare_page, compare_pages  # noqa: E402
from app.layout_triad.schema import PageLayoutSignals  # noqa: E402
from app.layout_triad.triad import merged_to_page_plan_hints  # noqa: E402
from app.preview_batch import batch_instruction  # noqa: E402


def test_compare_agree_no_escalate():
    j = PageLayoutSignals(page=1, category="正文单栏", columns=1, source="judge_35b", confidence=0.9)
    a = PageLayoutSignals(page=1, category="正文单栏", columns=1, source="structural", confidence=0.8)
    c = compare_page(j, a)
    assert c.agree and not c.escalate


def test_compare_category_hard_escalate():
    j = PageLayoutSignals(page=2, category="侧栏", columns=2, aside=True, source="judge_35b")
    a = PageLayoutSignals(page=2, category="目录", columns=1, toc_like=True, source="mineru_cloud")
    c = compare_page(j, a)
    assert not c.agree and c.escalate
    assert any("category" in d for d in c.diffs)


def test_compare_soft_merge_no_escalate():
    j = PageLayoutSignals(
        page=3, category="正文单栏", columns=1, math_dense=True, confidence=0.9, source="judge_35b"
    )
    a = PageLayoutSignals(
        page=3, category="正文单栏", columns=1, math_dense=False, confidence=0.9, source="structural"
    )
    c = compare_page(j, a)
    assert not c.agree
    assert not c.escalate  # 软分歧 + 高置信
    assert c.merged.math_dense is True


def test_merged_to_page_plan_hints_merge_adjacent():
    merged = [
        {"page": 1, "category": "目录", "columns": 1, "aside": False, "notes": ""},
        {"page": 2, "category": "目录", "columns": 1, "aside": False, "notes": ""},
        {"page": 3, "category": "正文双栏", "columns": 2, "aside": False, "notes": ""},
    ]
    plan = merged_to_page_plan_hints(merged, edition_id="e1")
    assert plan[0]["pages"] == "1-2"
    assert plan[0]["category"] == "目录"
    assert plan[1]["pages"] == "3"
    assert plan[1]["action"] == "reprocess:layout_content"


def test_batch_instruction_triad_no_read_when_no_escalate():
    instr = batch_instruction(
        minio_key="corpus/imports/x.pdf",
        start=1,
        end=6,
        page_count=100,
        run_index=1,
        run_total=10,
        already=[],
        triad={
            "anchor_source": "structural",
            "escalate_pages": [],
            "escalate_images": {},
            "compare": [],
            "merged": [{"page": 1, "category": "正文单栏"}],
            "page_plan_draft": [{"pages": "1-6", "category": "正文单栏"}],
        },
    )
    assert "不要用文件读取打开任何预览图" in instr
    assert "layout.triad" in instr
    assert "≤18" not in instr


def test_compare_pages_missing_anchor():
    judges = [PageLayoutSignals(page=5, category="习题", exercise_like=True)]
    cmps = compare_pages(judges, [])
    assert len(cmps) == 1
    assert cmps[0].escalate or not cmps[0].agree
