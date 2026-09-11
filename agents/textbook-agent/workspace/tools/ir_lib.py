"""Canonical IR library: six-source ingestion for the IR line.

Library only — not in manifest.yaml. Self-contained: no ``app.*`` imports,
no ``official_*``/``v2md_*`` imports. Reads MinerU artifacts under
``textbooks/<id>/mineru/`` and produces ``textbooks/<id>/ir/pages/NNNN.json``
plus ``ir/remap.json``. Never touches ``pages/`` / ``mid/`` / ``v2md/``.

Design doc: ``workspace/ir/schema.md``. Box vocabulary: ``workspace/ir/box_kinds.yaml``.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_CAPTION_TYPES = {
    "image_caption", "image_footnote", "table_caption", "table_footnote",
    "chart_caption", "chart_footnote", "code_caption", "code_footnote",
}
_CHROME_TYPES = {
    "header": "headers", "page_header": "headers",
    "footer": "footers", "page_footer": "footers",
    "page_number": "page_numbers", "page-number": "page_numbers",
    "page_footnote": "footnotes",
}
_FIGURE_TYPES = {"image", "chart"}
_ROW_Y_TOL = 15.0
_INLINE_IMG_HFRAC = 0.08
_ASIDE_X0 = 0.62
_ASIDE_MAX_W = 0.38
_BOX_MAX_W = 0.35

_CACHE: "dict[str, tuple[float, Any]]" = {}


def safe_book_id(book_id: str) -> str:
    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    return sid


def book_dir(book_id: str, root: str = "") -> Path:
    sid = safe_book_id(book_id)
    base = Path(root).resolve() if root else Path.cwd()
    if (base / "textbooks").is_dir() and not (base / sid).is_dir():
        return base / "textbooks" / sid
    return base / sid


def sha16(data: Any) -> str:
    if not isinstance(data, (str, bytes)):
        data = json.dumps(data, sort_keys=True, ensure_ascii=False)
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


def _load_json_cached(path: Path) -> Any:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = str(path)
    hit = _CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    _CACHE[key] = (mtime, data)
    return data


def load_layout(book: Path) -> dict[str, Any] | None:
    data = _load_json_cached(book / "mineru" / "layout.json")
    return data if isinstance(data, dict) else None


def load_model(book: Path) -> list[list[dict[str, Any]]]:
    for path in sorted((book / "mineru").glob("*_model.json")):
        data = _load_json_cached(path)
        if isinstance(data, list):
            return [pg if isinstance(pg, list) else [] for pg in data]
    return []


def load_v1(book: Path) -> list[dict[str, Any]]:
    for path in sorted((book / "mineru").glob("*_content_list.json")):
        if path.name.endswith("_content_list_v2.json"):
            continue
        data = _load_json_cached(path)
        if isinstance(data, list):
            return [b for b in data if isinstance(b, dict)]
    return []


def default_box_prefixes() -> list[str]:
    path = Path(__file__).resolve().parent.parent / "ir" / "box_kinds.yaml"
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return [str(x) for x in (raw or {}).get("prefixes", []) if str(x).strip()]
    except Exception:
        return ["聚焦", "思考与交流", "探究", "拓展延伸", "拓展视野", "练习巩固"]


def _norm_text(lines: Any) -> str:
    """去全部空白（含全角空格）的口径：给 ID 种子和文本匹配用。"""
    out = []
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        for span in line.get("spans") or []:
            if isinstance(span, dict):
                out.append(str(span.get("content") or ""))
    return re.sub(r"\s+", "", "".join(out))


def _join_text(lines: Any) -> str:
    """展示口径：span 内容去首尾后拼接，保留内部全角空格（题注用）。"""
    out = []
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        for span in line.get("spans") or []:
            if isinstance(span, dict):
                out.append(str(span.get("content") or "").strip())
    return "".join(out).strip()


def _is_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def render_lines(lines: Any) -> tuple[list[list[dict[str, str]]], str]:
    """行结构 + 纯文本（CJK 不加空格；inline_equation 包 $…$，行间公式块包 $$…$$）。"""
    structured: list[list[dict[str, str]]] = []
    parts: list[str] = []
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        spans = [s for s in (line.get("spans") or []) if isinstance(s, dict)]
        blob = "".join(str(s.get("content") or "") for s in spans)
        cjk = _is_cjk(blob)
        row: list[dict[str, str]] = []
        for span in spans:
            kind = str(span.get("type") or "text")
            content = str(span.get("content") or "").strip()
            img = _img_name(span.get("image_path"))
            if kind in {"inline_equation", "equation"}:
                if content:
                    row.append({"t": "eq", "c": content})
                    parts.append(f"${content}$")
                elif img:
                    row.append({"t": "img", "c": img})
                    parts.append(f"![](images/{img})")
            elif kind == "interline_equation":
                if content:
                    row.append({"t": "ieq", "c": content})
                    parts.append(f"$$\n{content}\n$$")
                elif img:
                    row.append({"t": "img", "c": img})
                    parts.append(f"![](images/{img})")
            elif img and not content:
                row.append({"t": "img", "c": img})
                parts.append(f"![](images/{img})")
            elif content:
                row.append({"t": "text", "c": content})
                parts.append(content)
                if not cjk:
                    parts.append(" ")
        if row:
            structured.append(row)
    text = "".join(parts).strip()
    return structured, text


def _img_name(path: object) -> str:
    raw = str(path or "").replace("\\", "/").strip()
    if not raw or raw.startswith("data:"):
        return ""
    return raw.rsplit("/", 1)[-1]


def _block_bbox(block: dict[str, Any]) -> list[float] | None:
    bb = block.get("bbox")
    if isinstance(bb, list) and len(bb) >= 4:
        try:
            return [float(x) for x in bb[:4]]
        except (TypeError, ValueError):
            return None
    return None


def _grid_salt(bb: list[float] | None) -> str:
    if not bb:
        return ""
    return ",".join(str(int(round(x / 10.0))) for x in bb)


def block_id(block: dict[str, Any], text: str, asset: str) -> str:
    seed = re.sub(r"\s+", "", text) or f"asset:{asset}"
    return "kb-" + sha16(f"{seed}|{_grid_salt(_block_bbox(block))}")


def _child_caption(block: dict[str, Any]) -> str:
    bits = []
    for child in block.get("blocks") or []:
        if not isinstance(child, dict):
            continue
        if str(child.get("type") or "") in _CAPTION_TYPES:
            text = _join_text(child.get("lines"))
            if text:
                bits.append(text)
    return "\n".join(bits)


def _min_score(lines: Any) -> float | None:
    scores = []
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        for span in line.get("spans") or []:
            if isinstance(span, dict):
                try:
                    scores.append(float(span.get("score")))
                except (TypeError, ValueError):
                    continue
    return round(min(scores), 4) if scores else None


def _page_v1_captions(v1: list[dict[str, Any]], page_idx: int) -> list[str]:
    caps = []
    for block in v1:
        if int(block.get("page_idx") or 0) != page_idx:
            continue
        if str(block.get("type") or "") in _FIGURE_TYPES:
            cap = block.get("image_caption") or block.get("chart_caption") or []
            caps.append("".join(str(x) for x in cap).strip() if isinstance(cap, list) else str(cap).strip())
    return caps


def _join_latex(model_page: list[dict[str, Any]], bb: list[float] | None,
                page_size: list[float], kinds: set[str]) -> str | None:
    best, best_overlap = _best_det(model_page, bb, page_size, kinds)
    if best:
        latex = str(best.get("latex") or "").strip()
        return latex or None
    return None


def _best_det(model_page: list[dict[str, Any]], bb: list[float] | None,
              page_size: list[float], kinds: set[str],
              min_overlap: float = 0.3) -> tuple[dict[str, Any] | None, float]:
    if not model_page or not bb or not page_size:
        return None, min_overlap
    pw, ph = float(page_size[0]), float(page_size[1])
    best, best_overlap = None, min_overlap
    for det in model_page:
        if str(det.get("type") or "") not in kinds:
            continue
        db = det.get("bbox")
        if not isinstance(db, list) or len(db) < 4:
            continue
        dx0, dy0, dx1, dy1 = float(db[0]) * pw, float(db[1]) * ph, float(db[2]) * pw, float(db[3]) * ph
        ix0, iy0 = max(bb[0], dx0), max(bb[1], dy0)
        ix1, iy1 = min(bb[2], dx1), min(bb[3], dy1)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        inter = (ix1 - ix0) * (iy1 - iy0)
        mine = max((bb[2] - bb[0]) * (bb[3] - bb[1]), 1.0)
        det_area = max((dx1 - dx0) * (dy1 - dy0), 1.0)
        overlap = inter / min(mine, det_area)
        if overlap > best_overlap:
            best, best_overlap = det, overlap
    return best, best_overlap


def _eq_span_latex(model_page: list[dict[str, Any]], raw_lines: Any,
                   page_size: list[float]) -> list[str | None]:
    """按出现顺序给行内公式 span 配 model.json 的 latex（page+bbox 邻近）。"""
    out: list[str | None] = []
    for line in raw_lines or []:
        if not isinstance(line, dict):
            continue
        for span in line.get("spans") or []:
            if not isinstance(span, dict):
                continue
            if str(span.get("type") or "") not in {"inline_equation", "equation"}:
                continue
            bb = _block_bbox(span)
            best, _ = _best_det(model_page, bb, page_size, {"inline_formula"}, min_overlap=0.2)
            latex = str(best.get("latex") or "").strip() if best else ""
            out.append(latex or None)
    return out


def _detect_columns(body: list[dict[str, Any]], page_w: float) -> bool:
    """正文 text 块 x0 是否呈明显双栏分布。"""
    xs = []
    for b in body:
        if b["type"] == "text":
            bb = b.get("_bb")
            if bb:
                xs.append(bb[0] / page_w)
    if len(xs) < 6:
        return False
    left = sum(1 for x in xs if x < 0.5)
    right = len(xs) - left
    return right >= max(3, 0.3 * len(xs)) and left >= max(3, 0.3 * len(xs))


def _match_box_kind(text: str, prefixes: list[str]) -> str:
    head = text[:12]
    hit = ""
    for prefix in prefixes:
        if head.startswith(prefix) and len(prefix) > len(hit):
            hit = prefix
    return hit


def ingest_page(book: Path, page_no: int, box_prefixes: list[str] | None = None,
                model: list[list[dict[str, Any]]] | None = None,
                v1: list[dict[str, Any]] | None = None,
                layout: dict[str, Any] | None = None) -> dict[str, Any]:
    box_prefixes = box_prefixes if box_prefixes is not None else default_box_prefixes()
    layout = layout if layout is not None else load_layout(book)
    if not layout or not isinstance(layout.get("pdf_info"), list):
        raise FileNotFoundError(f"missing layout.json under {book / 'mineru'}")
    model = model if model is not None else load_model(book)
    v1 = v1 if v1 is not None else load_v1(book)
    page = None
    for cand in layout["pdf_info"]:
        if isinstance(cand, dict) and int(cand.get("page_idx", -1)) == page_no - 1:
            page = cand
            break
    if page is None:
        raise FileNotFoundError(f"page {page_no} not in layout")
    page_size = page.get("page_size") or [595, 841]
    pw = float(page_size[0])

    body = [b for b in (page.get("para_blocks") or []) if isinstance(b, dict)]
    chrome_blocks = [b for b in (page.get("discarded_blocks") or []) if isinstance(b, dict)]
    model_page = model[page_no - 1] if 0 < page_no <= len(model) else []

    blocks: list[dict[str, Any]] = []
    for b in body:
        bb = _block_bbox(b)
        structured, text = render_lines(b.get("lines"))
        if model_page:
            eq_latex = _eq_span_latex(model_page, b.get("lines"), page_size)
            if eq_latex:
                idx = 0
                for row in structured:
                    for span in row:
                        if span.get("t") == "eq" and idx < len(eq_latex):
                            if eq_latex[idx]:
                                span["latex"] = eq_latex[idx]
                            idx += 1
        asset = ""
        if str(b.get("type") or "") in _FIGURE_TYPES | {"table", "interline_equation"}:
            for line in b.get("lines") or []:
                if not isinstance(line, dict):
                    continue
                for span in line.get("spans") or []:
                    if isinstance(span, dict):
                        asset = asset or _img_name(span.get("image_path"))
        if not asset:
            for child in b.get("blocks") or []:
                if isinstance(child, dict):
                    for line in child.get("lines") or []:
                        if isinstance(line, dict):
                            for span in line.get("spans") or []:
                                if isinstance(span, dict):
                                    asset = asset or _img_name(span.get("image_path"))
        row = {
            "_bb": bb,
            "id": block_id(b, text, asset),
            "type": str(b.get("type") or "text"),
            "order": int(b.get("index") if isinstance(b.get("index"), int) else len(blocks)),
            "lines": structured,
            "text": text,
        }
        level = b.get("level")
        if isinstance(level, (int, float)):
            row["level"] = max(0, min(6, int(level)))
        if asset:
            row["asset"] = asset
        cap = _child_caption(b)
        if cap:
            row["caption"] = cap
        if str(row["type"]) == "table":
            html = ""
            for line in b.get("lines") or []:
                if isinstance(line, dict):
                    for span in line.get("spans") or []:
                        if isinstance(span, dict):
                            html = html or str(span.get("html") or "").strip()
            if html:
                row["table_html"] = html
        score = _min_score(b.get("lines"))
        if score is not None:
            row["score"] = score
        cross = any(
            isinstance(span, dict) and span.get("cross_page")
            for line in (b.get("lines") or [])
            if isinstance(line, dict)
            for span in (line.get("spans") or [])
        )
        if cross:
            row["cross_page"] = True
        if bb:
            row["width_frac"] = round((bb[2] - bb[0]) / pw, 3)
        blocks.append(row)

    # 表格 HTML 从 v1 补（layout span html 缺时）
    v1_tables = [
        (int(b.get("page_idx") or 0), str(b.get("table_body") or ""))
        for b in v1
        if str(b.get("type") or "") == "table" and str(b.get("table_body") or "").strip()
    ]
    tables_here = [t for t in v1_tables if t[0] == page_no - 1]
    need = [blk for blk in blocks if blk["type"] == "table" and not blk.get("table_html")]
    if need and len(tables_here) == len([b for b in blocks if b["type"] == "table"]):
        for blk, (_, html) in zip([b for b in blocks if b["type"] == "table"], tables_here):
            if not blk.get("table_html"):
                blk["table_html"] = html

    # figure 题注 v1 兜底（整页数量相等才按序配对，防错配）
    figs = [blk for blk in blocks if blk["type"] in _FIGURE_TYPES]
    caps_here = _page_v1_captions(v1, page_no - 1)
    if caps_here and len(caps_here) == len(figs):
        for blk, cap in zip(figs, caps_here):
            if cap and not blk.get("caption"):
                blk["caption"] = cap

    # kind / zone 判定
    two_col = _detect_columns(blocks, pw)
    for blk, raw in zip(blocks, body):
        bb = blk.pop("_bb")
        if bb:
            blk["col"] = 1 if bb[0] / pw > 0.5 else 0
        wf = blk.get("width_frac") or 0.0
        kind = "body"
        btype = blk["type"]
        if btype == "ref_text":
            kind = "ref"
        elif btype in _FIGURE_TYPES:
            kind = "figure"
        elif btype == "title" and wf < _BOX_MAX_W:
            hit = _match_box_kind(blk["text"], box_prefixes)
            if hit:
                kind = "box"
                blk["box_kind"] = hit
        elif (btype == "text" and not two_col and bb and bb[0] / pw > _ASIDE_X0
              and 0 < wf < _ASIDE_MAX_W):
            kind = "ref"
        blk["kind"] = kind

    # 行内小图：图块高度 < 8% 页高且与文本块阅读序相邻
    page_h = float(page_size[1])
    for i, (blk, raw) in enumerate(zip(blocks, body)):
        if blk["type"] not in _FIGURE_TYPES:
            continue
        bb = _block_bbox(raw)
        hfrac = ((bb[3] - bb[1]) / page_h) if bb else 1.0
        if hfrac < _INLINE_IMG_HFRAC:
            neighbor_text = any(
                0 <= j < len(blocks) and blocks[j]["type"] == "text"
                for j in (i - 1, i + 1)
            )
            if neighbor_text:
                blk["kind"] = "inline_image"

    # 并排图行组（y 重叠聚类，仅 figure 参与；raw 按阅读序 index 对应，
    # 不能 zip——inline_image 已从 figure 里剔除，zip 会错位）
    fig_rows = [blk for blk in blocks if blk["kind"] == "figure"]
    raw_by_index = {}
    for raw in body:
        try:
            raw_by_index[int(raw.get("index") or 0)] = raw
        except (TypeError, ValueError):
            continue
    fig_bboxes = {}
    for blk in fig_rows:
        raw = raw_by_index.get(blk["order"])
        if raw is not None:
            fig_bboxes[blk["id"]] = _block_bbox(raw)
    row_of: dict[str, int] = {}
    clusters: list[list[dict[str, Any]]] = []
    for blk in fig_rows:
        bb = fig_bboxes.get(blk["id"])
        if not bb:
            continue
        placed = False
        for cluster in clusters:
            cbb = fig_bboxes.get(cluster[0]["id"])
            if not cbb:
                continue
            overlap = min(bb[3], cbb[3]) - max(bb[1], cbb[1])
            if overlap > 0.5 * min(bb[3] - bb[1], cbb[3] - cbb[1]):
                cluster.append(blk)
                placed = True
                break
        if not placed:
            clusters.append([blk])
    for n, cluster in enumerate(clusters):
        if len(cluster) > 1:
            for blk in cluster:
                row_of[blk["id"]] = n
    for blk in fig_rows:
        if blk["id"] in row_of:
            blk["row"] = row_of[blk["id"]]

    # model.json 联结：块级 latex（行间公式；这些书多为截图型，留空是常态）
    if model_page:
        for blk in blocks:
            if blk["type"] != "interline_equation":
                continue
            raw = raw_by_index.get(blk["order"])
            latex = _join_latex(model_page, _block_bbox(raw) if raw else None, page_size, {"equation"})
            if latex:
                blk["latex"] = latex

    # chrome：discarded 按 order 插回阅读序
    chrome: dict[str, Any] = {"headers": [], "page_numbers": [], "footers": [], "footnotes": []}
    for b in sorted(chrome_blocks, key=lambda x: int(x.get("index") or 0)):
        bucket = _CHROME_TYPES.get(str(b.get("type") or ""))
        if not bucket:
            continue
        text = _join_text(b.get("lines"))
        if bucket == "footnotes":
            chrome["footnotes"].append({"order": int(b.get("index") or 0), "text": text})
        elif text:
            chrome[bucket].append(text)

    source = {"schema": 1}
    lay_path = book / "mineru" / "layout.json"
    if lay_path.is_file():
        source["layout"] = sha16(lay_path.read_bytes())
    ver = layout.get("_version_name")
    if ver:
        source["mineru"] = str(ver)

    return {
        "page": page_no,
        "book": book.name,
        "page_size": page_size,
        "source": source,
        "blocks": blocks,
        "chrome": chrome,
    }


def compute_remap(old_pages: dict[int, list[dict[str, Any]]],
                  new_pages: dict[int, dict[str, Any]]) -> dict[str, str]:
    """旧 IR 的块 ID → 新 IR 的块 ID。先按规范化文本精确配，再按同 type 最近 order 配。"""
    remap: dict[str, str] = {}
    used: set[str] = set()
    new_by_text: dict[str, list[dict[str, Any]]] = {}
    for page_no, page in new_pages.items():
        for blk in page.get("blocks", []):
            key = re.sub(r"\s+", "", str(blk.get("text") or ""))
            new_by_text.setdefault(key, []).append(blk)

    def _candidates(old: dict[str, Any], page_no: int) -> dict[str, Any] | None:
        key = re.sub(r"\s+", "", str(old.get("text") or ""))
        for cand in new_by_text.get(key, []):
            if cand["id"] not in used and cand.get("type") == old.get("type"):
                used.add(cand["id"])
                return cand
        page = new_pages.get(page_no)
        if page:
            same = [b for b in page.get("blocks", []) if b.get("type") == old.get("type") and b["id"] not in used]
            if same:
                target = min(same, key=lambda b: abs(int(b.get("order", 0)) - int(old.get("order", 0))))
                used.add(target["id"])
                return target
        return None

    for page_no, old_blocks in old_pages.items():
        for old in old_blocks:
            if old.get("id") in used:
                continue
            cand = _candidates(old, page_no)
            if cand is not None and cand["id"] != old.get("id"):
                remap[str(old.get("id"))] = cand["id"]
    return remap


def _figure_md(blk: dict[str, Any]) -> str:
    asset = blk.get("asset") or ""
    caption = (blk.get("caption") or "").strip()
    alt = caption or "插图"
    if not asset:
        return caption
    if caption:
        return f"![{alt}](images/{asset})\n\n*{caption}*"
    return f"![{alt}](images/{asset})"


def _block_md(blk: dict[str, Any]) -> str:
    btype = blk.get("type")
    text = (blk.get("text") or "").strip()
    caption = (blk.get("caption") or "").strip()
    if btype == "title":
        if blk.get("kind") == "box":
            kind = blk.get("box_kind") or ""
            body = text if text != kind else ""
            return f"**【{kind}】** {body}".rstrip()
        level = max(1, min(6, int(blk.get("level") or 2)))
        return f"{'#' * level} {text}".rstrip()
    if btype in _FIGURE_TYPES:
        return _figure_md(blk)
    if btype == "table":
        chunk = []
        if caption:
            chunk.append(f"*{caption}*")
        html = (blk.get("table_html") or "").strip()
        if html:
            chunk.append(html)
        elif blk.get("asset"):
            chunk.append(f"![{caption or '表格'}](images/{blk['asset']})")
        return "\n\n".join(chunk)
    if btype == "interline_equation":
        latex = (blk.get("latex") or "").strip()
        if latex:
            return f"$$\n{latex}\n$$"
        if blk.get("asset"):
            return f"![公式](images/{blk['asset']})"
        return text
    return text


def render_page_md(page: dict[str, Any], notes: list | None = None) -> str:
    """Canonical IR 一页 → Markdown（新库正文预览/对拍用）。

    - 行内公式/小图已在线结构里拼好（text 字段即成品）；
    - 截图型行间公式输出 ![](images/…)，有 latex 输出 $$…$$；
    - 并排图行组连续输出（surface 按 row 字段排成行）；
    - 页注收在页尾分隔线后（chrome 区块）。
    - 传 notes 时先叠加注解（应用失败的部分不进正文，由调用方看报告）。
    """
    if notes:
        page, _rejected = apply_notes(page, notes)
    parts: list[str] = []
    fig_rows: dict[int, list[dict[str, Any]]] = {}
    for blk in page.get("blocks", []):
        row = blk.get("row")
        if row is not None:
            fig_rows.setdefault(int(row), []).append(blk)
    emitted: set[int] = set()

    for blk in page.get("blocks", []):
        row = blk.get("row")
        if row is not None:
            rid = int(row)
            if rid in emitted:
                continue
            emitted.add(rid)
            cells = "\n\n".join(c for c in (_figure_md(c) for c in fig_rows[rid]) if c)
            if cells:
                parts.append(cells)
            continue
        md = _block_md(blk)
        if md:
            parts.append(md)

    chrome = page.get("chrome") or {}
    footnotes = [f.get("text", "") for f in chrome.get("footnotes", []) if f.get("text")]
    if footnotes:
        parts.append("---\n\n" + "\n\n".join(footnotes))
    return "\n\n".join(p for p in parts if p) + "\n"


def apply_notes(page: dict[str, Any], notes: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """把注解叠到一页 IR 上（纯函数，不改输入）。返回 (新页, 拒绝报告)。

    支持的 op：
    - replace: new 替换块文本里第一个 old（old 必须存在，否则拒绝）；
    - delete:  整块移除；
    - retitle: 改 title 块的 text（old 为原标题全文，精确匹配）。
    按 notes 顺序应用；前面成功的 replace 会影响后面的 old 匹配。
    """
    import copy

    out = copy.deepcopy(page)
    by_id = {b["id"]: b for b in out.get("blocks", [])}
    rejected: list[dict[str, Any]] = []

    def _reject(note: dict[str, Any], reason: str) -> None:
        rejected.append({"note": note.get("id", "?"), "block": note.get("block", "?"), "reason": reason})

    for note in notes or []:
        if not isinstance(note, dict):
            continue
        bid = str(note.get("block") or "")
        blk = by_id.get(bid)
        if blk is None:
            _reject(note, "block not found")
            continue
        op = str(note.get("op") or "replace")
        if op == "delete":
            out["blocks"] = [b for b in out["blocks"] if b["id"] != bid]
            by_id.pop(bid, None)
            continue
        old = str(note.get("old") or "")
        new = str(note.get("new") or "")
        text = str(blk.get("text") or "")
        if op == "replace":
            if not old or old not in text:
                _reject(note, "old not found")
                continue
            blk["text"] = text.replace(old, new, 1)
            for row in blk.get("lines", []):
                for span in row:
                    if span.get("t") in ("text", "eq") and old in str(span.get("c", "")):
                        span["c"] = str(span["c"]).replace(old, new, 1)
                        break
                else:
                    continue
                break
        elif op == "retitle":
            if blk.get("type") != "title":
                _reject(note, "not a title block")
                continue
            if old and old != text:
                _reject(note, "old not found")
                continue
            blk["text"] = new
            for row in blk.get("lines", []):
                for span in row:
                    if span.get("t") == "text":
                        span["c"] = new
        else:
            _reject(note, f"unknown op {op}")
    return out, rejected
