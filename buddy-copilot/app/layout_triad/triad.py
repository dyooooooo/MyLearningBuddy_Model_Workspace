"""三角裁决编排：35B 主力 + MinerU/结构锚点 + compare。"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from .anchor_mineru import anchor_pages
from .compare import compare_pages
from .judge_35b import judge_pages
from .pdf_io import download_pdf, parse_pages, render_pages
from .schema import PageCompareResult, PageLayoutSignals


def run_triad(
    settings: Settings,
    *,
    minio_key: str,
    pages: str = "1-6",
    dpi: int = 72,
    skip_35b: bool = False,
) -> dict[str, Any]:
    """对指定页执行三角裁决，返回可 JSON 化结果。"""
    pdf_bytes = download_pdf(settings, minio_key)
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_count = doc.page_count
    finally:
        doc.close()
    wanted = parse_pages(pages, page_count)
    if not wanted:
        return {"error": f"pages 无命中（文档共 {page_count} 页）", "page_count": page_count}

    rendered = render_pages(pdf_bytes, minio_key, wanted, dpi=dpi)
    jpeg_by_page: dict[int, bytes] = rendered["jpeg_bytes"]

    anchors, anchor_source = anchor_pages(settings, pdf_bytes, wanted)

    if skip_35b:
        judges = [
            PageLayoutSignals(
                page=p,
                category="空白/其它",
                confidence=0.0,
                notes="skip_35b",
                source="judge_skipped",
            )
            for p in wanted
        ]
    else:
        judges = judge_pages(settings, jpeg_by_page)

    comparisons = compare_pages(judges, anchors)
    escalate_pages = [c.page for c in comparisons if c.escalate]
    escalate_images = {
        str(p): rendered["images"][p] for p in escalate_pages if p in rendered["images"]
    }

    return {
        "minio_key": minio_key,
        "page_count": page_count,
        "pages": wanted,
        "anchor_source": anchor_source,
        "judge": [j.model_dump() for j in judges],
        "anchor": [a.model_dump() for a in anchors],
        "compare": [_cmp_dump(c) for c in comparisons],
        "escalate_pages": escalate_pages,
        "escalate_images": escalate_images,
        "merged": [c.merged.model_dump() for c in comparisons],
        "hint": (
            "默认采信 merged（35B 主力，锚点校正）。"
            "仅 escalate_pages 需用 Cursor 文件读取打开 escalate_images 路径做视觉裁决，"
            "其余页不要 Read 图片。"
        ),
    }


def _cmp_dump(c: PageCompareResult) -> dict[str, Any]:
    return {
        "page": c.page,
        "agree": c.agree,
        "escalate": c.escalate,
        "diffs": c.diffs,
        "judge_category": c.judge.category,
        "anchor_category": c.anchor.category,
        "merged": c.merged.model_dump(),
    }


def merged_to_page_plan_hints(
    merged: list[dict[str, Any]],
    *,
    edition_id: str = "",
) -> list[dict[str, Any]]:
    """把逐页 merged 信号压成可编辑的 page_plan 草稿（相邻同类合并）。"""
    if not merged:
        return []
    items = sorted(merged, key=lambda x: int(x.get("page") or 0))
    segments: list[dict[str, Any]] = []
    start = int(items[0]["page"])
    prev = items[0]
    for cur in items[1:]:
        same = (
            cur.get("category") == prev.get("category")
            and bool(cur.get("aside")) == bool(prev.get("aside"))
            and int(cur.get("columns") or 1) == int(prev.get("columns") or 1)
        )
        if same and int(cur["page"]) == int(prev["page"]) + 1:
            prev = cur
            continue
        segments.append(_seg(start, int(prev["page"]), prev, edition_id))
        start = int(cur["page"])
        prev = cur
    segments.append(_seg(start, int(prev["page"]), prev, edition_id))
    return segments


def _seg(start: int, end: int, sig: dict[str, Any], edition_id: str) -> dict[str, Any]:
    cat = str(sig.get("category") or "正文单栏")
    action = "mineru"
    if cat in ("封面/扉页", "空白/其它"):
        action = "skip"
    elif cat in ("侧栏", "正文双栏", "正文多栏"):
        action = "reprocess:layout_content"
    pages = f"{start}-{end}" if start != end else str(start)
    return {
        "pages": pages,
        "category": cat,
        "edition_id": edition_id,
        "action": action,
        "notes": str(sig.get("notes") or "")[:200],
    }
