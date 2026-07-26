"""MinerU 辅助锚点：优先 Cloud 解析摘要；失败则用 PyMuPDF 结构代理（低幻觉启发式）。"""

from __future__ import annotations

import io
import json
import logging
import time
import zipfile
from collections import Counter
from typing import Any

import httpx

from ..config import Settings
from .schema import PageLayoutSignals

log = logging.getLogger("copilot.layout_triad.anchor")


def _infer_from_types(page: int, type_counts: Counter[str], texts: list[str]) -> PageLayoutSignals:
    joined = "\n".join(texts[:12])
    n_img = type_counts.get("image", 0) + type_counts.get("chart", 0)
    n_eq = type_counts.get("equation", 0) + type_counts.get("embed_equation", 0)
    n_table = type_counts.get("table", 0)
    n_title = type_counts.get("title", 0) + type_counts.get("header", 0)
    aside = type_counts.get("aside_text", 0) + type_counts.get("page_aside_text", 0) > 0
    toc_like = ("目录" in joined) or (n_title >= 3 and "……" in joined)
    exercise_like = any(k in joined for k in ("习题", "练习", "复习题", "思考题"))
    figure_heavy = n_img >= 3 and sum(type_counts.values()) <= n_img + 4
    math_dense = n_eq >= 4

    # 粗分栏：侧栏优先；否则按块数启发式
    columns = 1
    if aside:
        columns = 2
        category = "侧栏"
    elif toc_like:
        category = "目录"
    elif exercise_like:
        category = "习题"
    elif figure_heavy:
        category = "图版"
    elif math_dense:
        category = "公式密集"
    elif sum(type_counts.values()) >= 25:
        columns = 2
        category = "正文双栏"
    elif sum(type_counts.values()) <= 2 and not texts:
        category = "空白/其它"
    else:
        category = "正文单栏"

    return PageLayoutSignals(
        page=page,
        category=category,
        columns=columns,
        aside=aside,
        toc_like=toc_like,
        exercise_like=exercise_like,
        figure_heavy=figure_heavy,
        math_dense=math_dense,
        confidence=0.7 if type_counts else 0.4,
        notes=f"types={dict(type_counts)}",
        source="",
    )


def anchor_structural(pdf_bytes: bytes, pages: list[int]) -> list[PageLayoutSignals]:
    """PyMuPDF 块/图结构 → 与 MinerU 同构的信号（Cloud 不可用时的辅助锚）。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out: list[PageLayoutSignals] = []
    try:
        for pn in pages:
            if pn < 1 or pn > doc.page_count:
                continue
            page = doc[pn - 1]
            d = page.get_text("dict")
            type_counts: Counter[str] = Counter()
            texts: list[str] = []
            blocks = d.get("blocks") or []
            x_centers: list[float] = []
            w = float(page.rect.width) or 1.0
            for b in blocks:
                if b.get("type") == 1:
                    type_counts["image"] += 1
                    continue
                type_counts["text"] += 1
                bbox = b.get("bbox") or [0, 0, 0, 0]
                x_centers.append((float(bbox[0]) + float(bbox[2])) / 2.0 / w)
                for line in b.get("lines") or []:
                    for span in line.get("spans") or []:
                        t = str(span.get("text") or "").strip()
                        if t:
                            texts.append(t)
            # 左右块分布粗判双栏
            left = sum(1 for x in x_centers if x < 0.45)
            right = sum(1 for x in x_centers if x > 0.55)
            sig = _infer_from_types(pn, type_counts, texts)
            if left >= 3 and right >= 3 and not sig.toc_like:
                sig.columns = 2
                if sig.category == "正文单栏":
                    sig.category = "正文双栏"
            # 右侧窄条文本块 → 侧栏
            right_narrow = 0
            for b in blocks:
                if b.get("type") != 0:
                    continue
                bbox = b.get("bbox") or [0, 0, 0, 0]
                x0, x1 = float(bbox[0]) / w, float(bbox[2]) / w
                if x0 > 0.68 and (x1 - x0) < 0.35:
                    right_narrow += 1
            if right_narrow >= 2:
                sig.aside = True
                sig.category = "侧栏"
                sig.columns = max(sig.columns, 2)
            sig.source = "structural"
            sig.confidence = min(sig.confidence, 0.65)
            out.append(sig)
    finally:
        doc.close()
    return out


def _content_list_from_zip(zip_bytes: bytes) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        # prefer *_content_list.json / content_list.json
        candidates = [
            n
            for n in names
            if n.endswith("content_list.json") or n.endswith("content_list_v2.json")
        ]
        if not candidates:
            raise RuntimeError("MinerU zip 中无 content_list")
        # prefer v1 flat list name without v2 if both
        candidates.sort(key=lambda n: (0 if n.endswith("content_list.json") else 1, len(n)))
        raw = json.loads(zf.read(candidates[0]))
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        if isinstance(raw, dict):
            # v2 nested
            pages = raw.get("pdf_info") or raw.get("pages") or []
            flat: list[dict[str, Any]] = []
            if isinstance(pages, list):
                for i, p in enumerate(pages):
                    if not isinstance(p, dict):
                        continue
                    for blk in p.get("para_blocks") or p.get("content") or []:
                        if isinstance(blk, dict):
                            blk = dict(blk)
                            blk.setdefault("page_idx", i)
                            flat.append(blk)
            return flat
        raise RuntimeError("无法解析 content_list")


def _signals_from_content_list(
    content_list: list[dict[str, Any]], pages: list[int]
) -> list[PageLayoutSignals]:
    by_page: dict[int, list[dict[str, Any]]] = {p: [] for p in pages}
    for blk in content_list:
        # MinerU page_idx 常 0-based
        idx = blk.get("page_idx")
        if idx is None:
            continue
        page_1 = int(idx) + 1
        if page_1 in by_page:
            by_page[page_1].append(blk)
    out: list[PageLayoutSignals] = []
    for pn in pages:
        blks = by_page.get(pn) or []
        type_counts: Counter[str] = Counter()
        texts: list[str] = []
        for b in blks:
            t = str(b.get("type") or "text")
            type_counts[t] += 1
            tx = str(b.get("text") or b.get("content") or "").strip()
            if tx:
                texts.append(tx[:200])
        sig = _infer_from_types(pn, type_counts, texts)
        sig.source = "mineru_cloud"
        sig.confidence = 0.85 if blks else 0.3
        out.append(sig)
    return out


def anchor_mineru_cloud(
    settings: Settings,
    pdf_bytes: bytes,
    pages: list[int],
    *,
    timeout_s: float | None = None,
) -> list[PageLayoutSignals]:
    """调用 MinerU Cloud（page_ranges），返回页信号。失败抛异常由上层回退。"""
    token = settings.resolve_mineru_token()
    if not token:
        raise RuntimeError("未配置 MinerU API Token")
    base = settings.mineru_api_base_url.rstrip("/")
    timeout_s = timeout_s or float(settings.mineru_timeout_s)
    page_ranges = f"{pages[0]}-{pages[-1]}" if pages else "1-1"
    # 上传整本但限定 page_ranges（官方支持）
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "files": [
            {
                "name": "preview.pdf",
                "is_ocr": settings.mineru_is_ocr,
                "page_ranges": page_ranges,
            }
        ],
        "model_version": settings.mineru_model_version,
        "enable_formula": settings.mineru_enable_formula,
        "enable_table": settings.mineru_enable_table,
        "language": settings.mineru_language,
    }
    with httpx.Client(base_url=base, headers=headers, timeout=60.0) as client:
        r = client.post("/api/v4/file-urls/batch", json=body)
        r.raise_for_status()
        data = (r.json() or {}).get("data") or {}
        batch_id = str(data.get("batch_id") or "")
        urls = data.get("file_urls") or []
        if not batch_id or not urls:
            raise RuntimeError("MinerU 未返回 batch_id/file_urls")
        up = httpx.put(str(urls[0]), content=pdf_bytes, timeout=180.0)
        up.raise_for_status()

        deadline = time.monotonic() + timeout_s
        zip_url = ""
        while time.monotonic() < deadline:
            pr = client.get(f"/api/v4/extract-results/batch/{batch_id}")
            pr.raise_for_status()
            results = ((pr.json() or {}).get("data") or {}).get("extract_result") or []
            entry = None
            for e in results:
                if isinstance(e, dict) and str(e.get("file_name") or "") in (
                    "preview.pdf",
                    "",
                ):
                    entry = e
                    break
            if entry is None and results:
                entry = results[0] if isinstance(results[0], dict) else None
            if entry:
                state = str(entry.get("state") or "").lower()
                if state == "done":
                    zip_url = str(entry.get("full_zip_url") or "")
                    break
                if state == "failed":
                    raise RuntimeError(str(entry.get("err_msg") or "MinerU failed"))
            time.sleep(2.0)
        if not zip_url:
            raise RuntimeError(f"MinerU 超时（{timeout_s}s）未完成 page_ranges={page_ranges}")
        zr = httpx.get(zip_url, timeout=180.0)
        zr.raise_for_status()
        content_list = _content_list_from_zip(zr.content)
        # Cloud 的 page_idx 相对整书；page_ranges 时通常仍用原书页码 0-based
        return _signals_from_content_list(content_list, pages)


def anchor_pages(
    settings: Settings,
    pdf_bytes: bytes,
    pages: list[int],
) -> tuple[list[PageLayoutSignals], str]:
    """返回 (信号列表, 实际 source 说明)。"""
    mode = (settings.layout_anchor_mode or "auto").strip().lower()
    if mode in ("cloud", "auto") and settings.resolve_mineru_token():
        try:
            sigs = anchor_mineru_cloud(settings, pdf_bytes, pages)
            return sigs, "mineru_cloud"
        except Exception as e:  # noqa: BLE001
            log.warning("MinerU cloud anchor failed, fallback structural: %s", e)
            if mode == "cloud":
                # 仍回退，但标记
                sigs = anchor_structural(pdf_bytes, pages)
                for s in sigs:
                    s.notes = (s.notes + f" | cloud_fail:{e}")[:300]
                return sigs, "structural_fallback"
    sigs = anchor_structural(pdf_bytes, pages)
    return sigs, "structural"
