"""预览工具插件：把已预上传（MinIO staging）的 PDF 渲染成本地页面图片。

页级排版方案的前置能力：Agent 拿到本地 PNG 路径后，用自己的文件读取能力
逐页查看版面（分栏 / 侧栏 / 公式密度 / 目录页…），再给出针对每一页的方案。
下载走 c2-gateway `/api/v1/studio/import/object`（只放行两条导入线前缀）。
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx

from ...config import SERVICE_ROOT
from ..registry import ToolContext, ToolSpec, register

# 单次调用上限；Agent 应分批渲染（每批 3–6 页），避免一次读入过多大图把 SDK 桥接冲垮
_MAX_PAGES = 12


def _parse_pages(spec: str, page_count: int) -> list[int]:
    """解析页码表达式："1-8" / "1,3,5-8" / "all"（1 基）。"""
    spec = (spec or "").strip().lower()
    if spec in ("", "all"):
        return list(range(1, page_count + 1))[:_MAX_PAGES]
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    uniq = sorted({p for p in out if 1 <= p <= page_count})
    return uniq[:_MAX_PAGES]


@register
def build(ctx: ToolContext) -> list[ToolSpec]:
    def corpus_render_preview(minio_key: str, pages: str = "1-6", dpi: int = 72) -> Any:
        """把已预上传（staging）的 PDF 指定页渲染成本地 JPEG 预览图，返回图片路径列表。
        随后你必须用自己的文件读取能力**分批**打开查看版面（每批最多 3 张，看完再开下一批），
        据此给出**页级**排版方案。
        pages 形如 "1-6" / "1,3,5-8" / "all"（单次上限 12 页）；dpi 默认 72（版面归类够用，过大易冲垮桥接）。"""
        if not (minio_key.startswith("corpus/imports/") or minio_key.startswith("import35b/")):
            return {"error": "minio_key 仅支持 corpus/imports/ 或 import35b/ 前缀"}
        import fitz  # PyMuPDF（延迟导入：MCP 冷启动不付这个成本）

        url = f"{ctx.settings.gateway_base.rstrip('/')}/api/v1/studio/import/object"
        headers = ctx.settings.studio_auth_headers()
        try:
            r = httpx.get(url, params={"key": minio_key}, headers=headers, timeout=120)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else 0
            if code in (401, 403):
                return {
                    "error": "下载 PDF 失败：Studio 令牌无效或缺失（网关 401/403）",
                    "hint": "预览不可用时改用 page_html / get_blocks，不要停下来问用户。",
                    "checked": False,
                }
            return {"error": f"下载 PDF 失败: {e}", "hint": "先用 minio.stat 确认对象存在"}
        except httpx.HTTPError as e:
            return {"error": f"下载 PDF 失败: {e}", "hint": "先用 minio.stat 确认对象存在"}

        doc = fitz.open(stream=r.content, filetype="pdf")
        try:
            page_count = doc.page_count
            wanted = _parse_pages(pages, page_count)
            if not wanted:
                return {"error": f"pages 表达式没有命中任何页（文档共 {page_count} 页）"}
            out_dir = (
                SERVICE_ROOT / "data" / "preview" / hashlib.sha1(minio_key.encode()).hexdigest()[:12]
            )
            out_dir.mkdir(parents=True, exist_ok=True)
            # 上限 110 dpi：再高对版面归类增益很小，却易把 Cursor SDK 桥接冲垮
            zoom = max(0.5, min(float(dpi), 110.0) / 72.0)
            images: list[str] = []
            for pn in wanted:
                dest = out_dir / f"page-{pn:03d}.jpg"
                if not dest.exists():
                    pix = doc[pn - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                    # JPEG 体积约为同分辨率 PNG 的 1/5～1/10，显著降低桥接读图压力
                    pix.save(str(dest), jpg_quality=70)
                images.append(str(dest))
        finally:
            doc.close()
        return {
            "page_count": page_count,
            "rendered_pages": wanted,
            "images": images,
            "hint": (
                "请用文件读取能力分批查看这些 JPEG（本地绝对路径）："
                "每批最多打开 3 张，记录版式特征后再开下一批；"
                "禁止一次并行打开超过 3 张大图（会冲垮桥接连接）。"
                "记录分栏/侧栏/目录/习题/图版/公式密度后，按页段给出页级方案。"
            ),
        }

    return [ToolSpec("corpus.render_preview", "渲染 PDF 页面预览图", corpus_render_preview)]
