"""全书预览分批编排（离线单元测试）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.preview_batch import (  # noqa: E402
    PREVIEW_PAGES_PER_RUN,
    batch_instruction,
    detect_page_limit_from_messages,
    extract_page_plan_partial,
    merge_page_plans,
    page_batches,
    user_wants_full_preview,
)
from app.schemas import PagePlanEntry  # noqa: E402


def test_page_batches_covers_all():
    batches = page_batches(178)
    assert batches[0] == (1, PREVIEW_PAGES_PER_RUN)
    assert batches[-1][1] == 178
    # 无重叠、无缺口
    flat = []
    for a, b in batches:
        flat.extend(range(a, b + 1))
    assert flat == list(range(1, 179))


def test_merge_page_plans_override():
    existing = [{"pages": "1-3", "category": "正文", "edition_id": "a", "action": "mineru"}]
    partial = [
        PagePlanEntry(pages="1-3", category="目录", edition_id="a", action="mineru"),
        PagePlanEntry(pages="4-6", category="正文", edition_id="a", action="mineru"),
    ]
    merged = merge_page_plans(existing, partial)
    by = {m["pages"]: m["category"] for m in merged}
    assert by["1-3"] == "目录"
    assert by["4-6"] == "正文"


def test_extract_page_plan_partial():
    text = """本轮完成。
```json
{"pages_done": "1-6", "page_plan_partial": [{"pages": "1-2", "category": "目录", "edition_id": "x", "action": "mineru"}]}
```"""
    r = extract_page_plan_partial(text)
    assert r is not None
    assert r.pages_done == "1-6"
    assert len(r.page_plan_partial) == 1


def test_user_wants_full_preview():
    msgs = [{"role": "user", "content": {"text": "对全部的页面进行预览"}}]
    assert user_wants_full_preview(msgs)
    assert not user_wants_full_preview(
        [{"role": "user", "content": {"text": "先导入前 12 页看看"}}]
    )


def test_detect_page_limit():
    msgs = [
        {"role": "user", "content": {"text": "导入 20 页做预览版"}},
    ]
    assert detect_page_limit_from_messages(msgs) == 20


def test_batch_instruction_no_sample_ban():
    instr = batch_instruction(
        minio_key="corpus/imports/x.pdf",
        start=7,
        end=12,
        page_count=178,
        run_index=2,
        run_total=30,
        already=[],
    )
    assert "7–12" in instr or "7-12" in instr
    assert "≤18" not in instr
    assert "抽样" not in instr
    assert "禁止开始导入" in instr
