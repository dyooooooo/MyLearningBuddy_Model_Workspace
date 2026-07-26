"""35B 主力 vs MinerU 锚点：差异大则 escalate 给 Cursor 视觉。"""

from __future__ import annotations

from .schema import PageCompareResult, PageLayoutSignals

# category 粗等价（避免「正文双栏」vs「侧栏」误判为完全一致）
_CAT_GROUPS = {
    "正文单栏": "body",
    "正文双栏": "body_multi",
    "正文多栏": "body_multi",
    "侧栏": "aside",
    "目录": "toc",
    "习题": "exercise",
    "图版": "figure",
    "公式密集": "math",
    "封面/扉页": "front",
    "空白/其它": "other",
}


def _cat_group(c: str) -> str:
    return _CAT_GROUPS.get(c, c or "other")


def compare_page(judge: PageLayoutSignals, anchor: PageLayoutSignals) -> PageCompareResult:
    diffs: list[str] = []
    if _cat_group(judge.category) != _cat_group(anchor.category):
        diffs.append(f"category:{judge.category}≠{anchor.category}")
    j_cols = 3 if judge.columns >= 3 else max(1, judge.columns)
    a_cols = 3 if anchor.columns >= 3 else max(1, anchor.columns)
    if j_cols != a_cols:
        diffs.append(f"columns:{j_cols}≠{a_cols}")
    for name in ("aside", "toc_like", "exercise_like", "figure_heavy", "math_dense"):
        if bool(getattr(judge, name)) != bool(getattr(anchor, name)):
            diffs.append(name)

    # 关键字段分歧（category/columns/aside/toc）→ 必须 escalate
    hard = any(
        d.startswith("category:") or d.startswith("columns:") or d in ("aside", "toc_like")
        for d in diffs
    )
    soft_only = bool(diffs) and not hard
    agree = not diffs
    # 软分歧（仅习题/图版/公式）且双方 confidence 都高 → 不强制看图，采信 35B
    escalate = hard or (soft_only and min(judge.confidence, anchor.confidence) < 0.55)

    merged = judge.model_copy(deep=True)
    if not agree and not escalate:
        # 软分歧：布尔信号取 OR，category 仍用 35B
        merged.aside = judge.aside or anchor.aside
        merged.toc_like = judge.toc_like or anchor.toc_like
        merged.exercise_like = judge.exercise_like or anchor.exercise_like
        merged.figure_heavy = judge.figure_heavy or anchor.figure_heavy
        merged.math_dense = judge.math_dense or anchor.math_dense
        merged.notes = (judge.notes + " | soft-merge " + ",".join(diffs)).strip(" |")
    elif agree:
        merged.confidence = max(judge.confidence, anchor.confidence)
        merged.notes = (judge.notes or anchor.notes).strip()
    else:
        merged.notes = (
            f"分歧待 Cursor 视觉: {','.join(diffs)} | 35B={judge.category} 锚={anchor.category}"
        )[:300]
        merged.confidence = min(judge.confidence, 0.4)

    return PageCompareResult(
        page=judge.page,
        agree=agree,
        escalate=escalate,
        judge=judge,
        anchor=anchor,
        diffs=diffs,
        merged=merged,
    )


def compare_pages(
    judges: list[PageLayoutSignals],
    anchors: list[PageLayoutSignals],
) -> list[PageCompareResult]:
    by_a = {a.page: a for a in anchors}
    out: list[PageCompareResult] = []
    for j in judges:
        a = by_a.get(j.page) or PageLayoutSignals(page=j.page, source="missing_anchor", confidence=0.0)
        out.append(compare_page(j, a))
    return out
