"""三角裁决 MCP：layout.judge_35b / layout.anchor_mineru / layout.compare（+ triad 一键）。"""

from __future__ import annotations

import json
from typing import Any

from ...layout_triad.anchor_mineru import anchor_pages
from ...layout_triad.compare import compare_pages
from ...layout_triad.judge_35b import judge_pages
from ...layout_triad.pdf_io import download_pdf, parse_pages, render_pages
from ...layout_triad.schema import PageLayoutSignals
from ...layout_triad.triad import run_triad
from ..registry import ToolContext, ToolSpec, register


@register
def build(ctx: ToolContext) -> list[ToolSpec]:
    settings = ctx.settings

    def layout_judge_35b(minio_key: str, pages: str = "1-6", dpi: int = 72) -> Any:
        """35B 主力：对指定页渲图并用本地 vLLM 视觉输出结构化版面 JSON（category/columns/aside…）。
        不经过 Cursor Read，省 token。pages 如 \"1-6\"，单次最多 12 页。"""
        try:
            pdf = download_pdf(settings, minio_key)
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        import fitz

        doc = fitz.open(stream=pdf, filetype="pdf")
        try:
            pc = doc.page_count
        finally:
            doc.close()
        wanted = parse_pages(pages, pc)
        if not wanted:
            return {"error": "pages 无命中"}
        rendered = render_pages(pdf, minio_key, wanted, dpi=dpi)
        judges = judge_pages(settings, rendered["jpeg_bytes"])
        return {
            "pages": wanted,
            "judge": [j.model_dump() for j in judges],
            "images": {str(k): v for k, v in rendered["images"].items()},
        }

    def layout_anchor_mineru(minio_key: str, pages: str = "1-6") -> Any:
        """MinerU 辅助锚点：有 Token 时走 MinerU Cloud（page_ranges）；否则/失败用 PyMuPDF 结构代理。
        输出与 35B 同构的版面信号，用于对比校正（低幻觉）。不写导入队列。"""
        try:
            pdf = download_pdf(settings, minio_key)
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        import fitz

        doc = fitz.open(stream=pdf, filetype="pdf")
        try:
            pc = doc.page_count
        finally:
            doc.close()
        wanted = parse_pages(pages, pc)
        if not wanted:
            return {"error": "pages 无命中"}
        sigs, source = anchor_pages(settings, pdf, wanted)
        return {
            "pages": wanted,
            "anchor_source": source,
            "anchor": [s.model_dump() for s in sigs],
        }

    def layout_compare(judge_json: str, anchor_json: str) -> Any:
        """对比 35B judge 与 MinerU/结构 anchor（传入 JSON 数组字符串或对象列表的 JSON）。
        返回逐页 agree/escalate/diffs/merged；escalate=true 的页才建议 Cursor Read 原图。"""
        try:
            j_raw = json.loads(judge_json) if isinstance(judge_json, str) else judge_json
            a_raw = json.loads(anchor_json) if isinstance(anchor_json, str) else anchor_json
        except json.JSONDecodeError as e:
            return {"error": f"JSON 解析失败: {e}"}
        if isinstance(j_raw, dict) and "judge" in j_raw:
            j_raw = j_raw["judge"]
        if isinstance(a_raw, dict) and "anchor" in a_raw:
            a_raw = a_raw["anchor"]
        if not isinstance(j_raw, list) or not isinstance(a_raw, list):
            return {"error": "judge_json/anchor_json 须为数组（或含 judge/anchor 键的对象）"}
        try:
            judges = [PageLayoutSignals.model_validate(x) for x in j_raw]
            anchors = [PageLayoutSignals.model_validate(x) for x in a_raw]
        except Exception as e:  # noqa: BLE001
            return {"error": f"信号校验失败: {e}"}
        cmps = compare_pages(judges, anchors)
        return {
            "compare": [
                {
                    "page": c.page,
                    "agree": c.agree,
                    "escalate": c.escalate,
                    "diffs": c.diffs,
                    "merged": c.merged.model_dump(),
                }
                for c in cmps
            ],
            "escalate_pages": [c.page for c in cmps if c.escalate],
            "hint": "仅对 escalate_pages 用文件读取打开预览图；其余采信 merged。",
        }

    def layout_triad(minio_key: str, pages: str = "1-6", dpi: int = 72) -> Any:
        """一键三角裁决：内部依次 35B judge → MinerU/结构 anchor → compare。
        返回 escalate_pages / escalate_images / merged；全书预览编排优先用此工具。"""
        try:
            return run_triad(settings, minio_key=minio_key, pages=pages, dpi=dpi)
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    return [
        ToolSpec("layout.judge_35b", "35B 版面主力判定", layout_judge_35b),
        ToolSpec("layout.anchor_mineru", "MinerU/结构版面锚点", layout_anchor_mineru),
        ToolSpec("layout.compare", "三角对比与 escalate", layout_compare),
        ToolSpec("layout.triad", "一键三角裁决", layout_triad),
    ]
