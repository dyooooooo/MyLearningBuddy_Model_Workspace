"""Canonical IR library: five-source ingestion for the IR line.

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
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import quote

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_MD_IMG = re.compile(r"(!\[[^\]]*\]\()(images/[^)]+)(\))")
_HTML_IMG = re.compile(r"""(\bsrc=["'])images/([^"']+)(["'])""", re.I)
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
_INLINE_IMG_HFRAC = 0.08
_INLINE_IMG_WFRAC = 0.12
_BOX_MAX_W = 0.35
IR_SCHEMA = 9
FRONT_PAGES = 24
_OCR_LINE_TYPES = {"text", "list", "title", "ref_text"}
_OCR_PAD = 6.0
_OCR_MIN_INDENT = 8.0

_CACHE_MAX = 12
_CACHE: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()


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
        _CACHE.move_to_end(key)
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    _CACHE[key] = (mtime, data)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
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


def _v1_path(book: Path) -> Path | None:
    folder = book / "mineru"
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*_content_list.json")):
        if path.name.endswith("_content_list_v2.json"):
            continue
        return path
    return None


def _model_path(book: Path) -> Path | None:
    folder = book / "mineru"
    if not folder.is_dir():
        return None
    hits = sorted(folder.glob("*_model.json"))
    return hits[0] if hits else None


def _v2_path(book: Path) -> Path | None:
    folder = book / "mineru"
    if not folder.is_dir():
        return None
    hits = sorted(folder.glob("*_content_list_v2.json"))
    return hits[0] if hits else None


def load_v1(book: Path) -> list[dict[str, Any]]:
    path = _v1_path(book)
    if path is None:
        return []
    data = _load_json_cached(path)
    return [b for b in data if isinstance(b, dict)] if isinstance(data, list) else []


def load_v2(book: Path) -> list[list[dict[str, Any]]]:
    """V2 内容清单：按页的块列表，只给标题 level 互证，不当骨架。"""
    path = _v2_path(book)
    if path is None:
        return []
    data = _load_json_cached(path)
    if not isinstance(data, list) or not data:
        return []
    if isinstance(data[0], list):
        return [[b for b in page if isinstance(b, dict)] for page in data]
    pages: dict[int, list[dict[str, Any]]] = {}
    for block in data:
        if not isinstance(block, dict):
            continue
        pages.setdefault(int(block.get("page_idx") or 0), []).append(block)
    if not pages:
        return []
    last = max(pages)
    return [pages.get(i, []) for i in range(last + 1)]


def page_json_path(book: Path, page_no: int) -> Path:
    return book / "ir" / "pages" / f"{int(page_no):04d}.json"


def load_page_json(book: Path, page_no: int) -> dict[str, Any] | None:
    path = page_json_path(book, page_no)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def save_page_json(book: Path, page: dict[str, Any]) -> Path:
    n = int(page.get("page") or 0)
    path = page_json_path(book, n)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: v for k, v in page.items() if k not in {
        "fonts", "leads", "printed_lines", "printed_row_widths",
        "font_samples", "lead_samples",
    }}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def layout_sha(book: Path) -> str:
    path = book / "mineru" / "layout.json"
    if not path.is_file():
        return ""
    return sha16(path.read_bytes())


def load_or_ingest_page(
    book: Path,
    page_no: int,
    box_prefixes: list[str] | None = None,
    *,
    write: bool = True,
) -> tuple[dict[str, Any], bool]:
    """读 ir/pages；缺页、layout 哈希变了或 schema 过期再摄取。返回 (页, 是否新写)。"""
    want = layout_sha(book)
    cached = load_page_json(book, page_no)
    cached_schema = 0
    if cached is not None:
        try:
            cached_schema = int((cached.get("source") or {}).get("schema") or 0)
        except (TypeError, ValueError):
            cached_schema = 0
    if (
        cached is not None
        and want
        and cached.get("source", {}).get("layout") == want
        and cached_schema == IR_SCHEMA
    ):
        return cached, False
    page = ingest_page(book, page_no, box_prefixes)
    if write:
        save_page_json(book, page)
    return page, True


def asset_url(book_id: str, path: str) -> str:
    raw = (path or "").replace("\\", "/").strip()
    if not raw:
        return ""
    if raw.startswith("/api/") or raw.startswith("http://") or raw.startswith("https://"):
        return raw
    name = raw.rsplit("/", 1)[-1]
    if not name:
        return ""
    key = f"kb/pdfs/{safe_book_id(book_id)}/assets/mineru/{name}"
    return f"/api/v1/kb/proxy/asset?key={quote(key, safe='')}"


def rewrite_image_urls(markdown: str, book_id: str) -> str:
    if not markdown:
        return markdown

    def _md(match: re.Match[str]) -> str:
        url = asset_url(book_id, match.group(2))
        return match.group(0) if not url else f"{match.group(1)}{url}{match.group(3)}"

    def _html(match: re.Match[str]) -> str:
        url = asset_url(book_id, match.group(2))
        return match.group(0) if not url else f"{match.group(1)}{url}{match.group(3)}"

    return _HTML_IMG.sub(_html, _MD_IMG.sub(_md, markdown))


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


_SUB_SUP = re.compile(r"<(sub|sup)>(.*?)</\1>", re.I | re.S)


def _split_sub_sup(content: str) -> list[dict[str, str]]:
    """扫描稿里的 <sub>/<sup> 切成独立 span。没有标记就空列表，调用方按原文走。"""
    text = str(content or "")
    if "<sub" not in text.lower() and "<sup" not in text.lower():
        return []
    out: list[dict[str, str]] = []
    pos = 0
    for match in _SUB_SUP.finditer(text):
        if match.start() > pos:
            out.append({"t": "text", "c": text[pos:match.start()]})
        out.append({"t": match.group(1).lower(), "c": match.group(2)})
        pos = match.end()
    if pos < len(text):
        out.append({"t": "text", "c": text[pos:]})
    return out


def ir_line_rows(lines: Any) -> list[dict[str, Any]]:
    """中间表示的行：新格式 `{bbox, spans}`，旧格式仍是 span 数组。"""
    out: list[dict[str, Any]] = []
    for line in lines or []:
        if isinstance(line, dict) and isinstance(line.get("spans"), list):
            out.append({
                "bbox": line.get("bbox"),
                "spans": [s for s in line["spans"] if isinstance(s, dict)],
            })
        elif isinstance(line, list):
            out.append({"bbox": None, "spans": [s for s in line if isinstance(s, dict)]})
    return out


def ir_spans(lines: Any) -> list[dict[str, Any]]:
    return [span for row in ir_line_rows(lines) for span in row["spans"]]


def _keep_geom(src: dict[str, Any], dest: dict[str, Any]) -> dict[str, Any]:
    bb = _block_bbox(src)
    if bb:
        dest["bbox"] = [round(float(x), 2) for x in bb]
    if src.get("cross_page"):
        dest["cross_page"] = True
    return dest


_CROSS_BB_TOL = 1.5


def _bbox_close(a: list[float] | None, b: list[float] | None,
                tol: float = _CROSS_BB_TOL) -> bool:
    if not a or not b or len(a) < 4 or len(b) < 4:
        return False
    return all(abs(float(a[i]) - float(b[i])) <= tol for i in range(4))


def _line_goes_next_page(line: dict[str, Any]) -> bool:
    """行属于下一页：只认 span 上的 cross_page。行框比块框大出一圈不算。"""
    if not isinstance(line, dict):
        return False
    spans = [s for s in (line.get("spans") or []) if isinstance(s, dict)]
    return any(bool(s.get("cross_page")) for s in spans)


def _strip_cross_page_flag(line: dict[str, Any]) -> dict[str, Any]:
    cloned = json.loads(json.dumps(line, ensure_ascii=False))
    cloned.pop("cross_page", None)
    for span in cloned.get("spans") or []:
        if isinstance(span, dict):
            span.pop("cross_page", None)
    return cloned


def _on_page_lines(block: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        line
        for line in (block.get("lines") or [])
        if isinstance(line, dict) and not _line_goes_next_page(line)
    ]


def _outgoing_lines(page: dict[str, Any] | None) -> list[dict[str, Any]]:
    """上一页里被拼进来、实际属于下一页的行（去旗后的副本）。"""
    if not page:
        return []
    out: list[dict[str, Any]] = []
    for block in page.get("para_blocks") or []:
        if not isinstance(block, dict):
            continue
        for line in block.get("lines") or []:
            if isinstance(line, dict) and _line_goes_next_page(line):
                out.append(_strip_cross_page_flag(line))
    return out


def _partition_outgoing(
    outgoing: list[dict[str, Any]],
    bb: list[float] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not bb:
        return [], outgoing
    hit: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for line in outgoing:
        line_bb = _block_bbox(line)
        if line_bb and _bbox_close(line_bb, bb):
            hit.append(line)
        else:
            rest.append(line)
    return hit, rest


def _line_has_text(lines: list[dict[str, Any]]) -> bool:
    for line in lines:
        for span in line.get("spans") or []:
            if isinstance(span, dict) and str(span.get("content") or "").strip():
                return True
    return False


def render_lines(lines: Any) -> tuple[list[dict[str, Any]], str]:
    """行结构 + 纯文本（CJK 不加空格；inline_equation 包 $…$，行间公式块包 $$…$$）。

    行、span 的 bbox / cross_page 原样留下，后面贴字要靠它们定字高和缩进。
    """
    structured: list[dict[str, Any]] = []
    parts: list[str] = []
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        spans = [s for s in (line.get("spans") or []) if isinstance(s, dict)]
        blob = "".join(str(s.get("content") or "") for s in spans)
        cjk = _is_cjk(blob)
        row: list[dict[str, Any]] = []
        for span in spans:
            kind = str(span.get("type") or "text")
            content = str(span.get("content") or "").strip()
            img = _img_name(span.get("image_path"))
            items: list[dict[str, Any]] = []
            if kind in {"inline_equation", "equation"}:
                if content:
                    items.append(_keep_geom(span, {"t": "eq", "c": content}))
                    parts.append(f"${content}$")
                elif img:
                    items.append(_keep_geom(span, {"t": "img", "c": img}))
                    parts.append(f"![](images/{img})")
            elif kind == "interline_equation":
                if content:
                    items.append(_keep_geom(span, {"t": "ieq", "c": content}))
                    parts.append(f"$$\n{content}\n$$")
                elif img:
                    items.append(_keep_geom(span, {"t": "img", "c": img}))
                    parts.append(f"![](images/{img})")
            elif img and not content:
                items.append(_keep_geom(span, {"t": "img", "c": img}))
                parts.append(f"![](images/{img})")
            elif content:
                marks = _split_sub_sup(content)
                if marks:
                    items.extend(_keep_geom(span, dict(m)) for m in marks)
                else:
                    items.append(_keep_geom(span, {"t": "text", "c": content}))
                parts.append(content)
                if not cjk:
                    parts.append(" ")
            row.extend(items)
        if row:
            entry: dict[str, Any] = {"spans": row}
            line_bb = _block_bbox(line)
            if line_bb:
                entry["bbox"] = [round(float(x), 2) for x in line_bb]
            structured.append(entry)
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


def _child_captions(block: dict[str, Any]) -> list[dict[str, Any]]:
    """图/表子块题注：字 + 原始框。框没有就只留字。"""
    out: list[dict[str, Any]] = []
    for child in block.get("blocks") or []:
        if not isinstance(child, dict):
            continue
        typ = str(child.get("type") or "")
        if typ not in _CAPTION_TYPES:
            continue
        text = _join_text(child.get("lines"))
        if not text:
            continue
        item: dict[str, Any] = {"type": typ, "text": text}
        bb = _block_bbox(child)
        if bb:
            item["bbox"] = bb
        out.append(item)
    return out


def _child_caption(block: dict[str, Any]) -> str:
    return "\n".join(item["text"] for item in _child_captions(block))


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


def _bbox_center(bb: list[float]) -> tuple[float, float]:
    return ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)


def _bbox_area(bb: list[float] | None) -> float:
    if not (isinstance(bb, list) and len(bb) >= 4):
        return 0.0
    return max(0.0, float(bb[2]) - float(bb[0])) * max(0.0, float(bb[3]) - float(bb[1]))


def _strip_box_punct(text: str) -> str:
    return str(text or "").strip().rstrip("：:﹕")


def _pair_by_center(
    dests: list[dict[str, Any]],
    cands: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """按 bbox 中心距一对一配对；无框的候选不参与，宁缺不 zip。"""
    used: set[int] = set()
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for dest in dests:
        dbb = dest.get("_bb")
        if not dbb:
            continue
        dc = _bbox_center(dbb)
        best_i, best_d = -1, None
        for i, cand in enumerate(cands):
            if i in used:
                continue
            sbb = cand.get("bbox")
            if not (isinstance(sbb, list) and len(sbb) >= 4):
                continue
            try:
                sc = _bbox_center([float(x) for x in sbb[:4]])
            except (TypeError, ValueError):
                continue
            dist = (dc[0] - sc[0]) ** 2 + (dc[1] - sc[1]) ** 2
            if best_d is None or dist < best_d:
                best_i, best_d = i, dist
        if best_i >= 0:
            used.add(best_i)
            pairs.append((dest, cands[best_i]))
    return pairs


def _page_v1_captions(v1: list[dict[str, Any]], page_idx: int) -> list[dict[str, Any]]:
    caps = []
    for block in v1:
        if int(block.get("page_idx") or 0) != page_idx:
            continue
        if str(block.get("type") or "") not in _FIGURE_TYPES:
            continue
        raw = block.get("image_caption") or block.get("chart_caption") or []
        text = "".join(str(x) for x in raw).strip() if isinstance(raw, list) else str(raw).strip()
        if not text:
            continue
        caps.append({"text": text, "bbox": block.get("bbox")})
    return caps


def _v2_titles(v2_page: list[dict[str, Any]]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for block in v2_page:
        if str(block.get("type") or "") != "title":
            continue
        content = block.get("content") if isinstance(block.get("content"), dict) else {}
        bits = []
        for span in content.get("title_content") or []:
            if isinstance(span, dict):
                bits.append(str(span.get("content") or ""))
        text = re.sub(r"\s+", "", "".join(bits))
        level = content.get("level")
        if text and isinstance(level, (int, float)):
            out.append((text, int(level)))
    return out


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
    """按出现顺序给行内公式 span 配 model.json 的 latex（page+bbox 邻近）。

    只产有 content 的 span 的槽位：图片型/空公式 span 在 render_lines 里
    生成 img 行而非 eq 行，多产槽位会让后续 latex 全体错位。
    """
    out: list[str | None] = []
    for line in raw_lines or []:
        if not isinstance(line, dict):
            continue
        for span in line.get("spans") or []:
            if not isinstance(span, dict):
                continue
            if str(span.get("type") or "") not in {"inline_equation", "equation"}:
                continue
            if not str(span.get("content") or "").strip():
                continue
            bb = _block_bbox(span)
            best, _ = _best_det(model_page, bb, page_size, {"inline_formula"}, min_overlap=0.2)
            latex = str(best.get("latex") or "").strip() if best else ""
            out.append(latex or None)
    return out


def _match_box_kind(text: str, prefixes: list[str]) -> str:
    """去掉末尾冒号后必须整名等于词表。前缀命中会把「习题3.1」当成「习题」。"""
    head = _strip_box_punct(text)
    hit = ""
    for prefix in prefixes:
        if head == prefix and len(prefix) > len(hit):
            hit = prefix
    return hit


def _mode_x(xs: list[float], tol: float = 6.0) -> float:
    """左缘聚簇：人数最多的一簇；打平取更靠左的。"""
    if not xs:
        return 0.0
    ordered = sorted(xs)
    clusters: list[list[float]] = [[ordered[0]]]
    for x in ordered[1:]:
        if x - clusters[-1][-1] <= tol:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    best = max(clusters, key=lambda c: (len(c), -c[0]))
    return sum(best) / len(best)


def _ocr_boxes_in(
    model_page: list[dict[str, Any]],
    bb: list[float] | None,
    page_size: list[float],
    pad: float = _OCR_PAD,
) -> list[dict[str, Any]]:
    """段框里的 ocr_text，归一化框乘页宽页高变成点。"""
    if not model_page or not bb or not page_size:
        return []
    try:
        pw, ph = float(page_size[0] or 0), float(page_size[1] or 0)
    except (TypeError, ValueError):
        return []
    if pw <= 0 or ph <= 0:
        return []
    out: list[dict[str, Any]] = []
    for det in model_page:
        if str(det.get("type") or "") != "ocr_text":
            continue
        db = det.get("bbox")
        if not (isinstance(db, list) and len(db) >= 4):
            continue
        try:
            x0 = float(db[0]) * pw
            y0 = float(db[1]) * ph
            x1 = float(db[2]) * pw
            y1 = float(db[3]) * ph
        except (TypeError, ValueError):
            continue
        if x1 <= x0 or y1 <= y0:
            continue
        if x0 < bb[0] - pad or x1 > bb[2] + pad or y0 < bb[1] - pad or y1 > bb[3] + pad:
            continue
        item: dict[str, Any] = {"bbox": [x0, y0, x1, y1]}
        try:
            item["score"] = round(float(det["score"]), 4)
        except (TypeError, ValueError, KeyError):
            pass
        out.append(item)
    return out


def _cluster_ocr_lines(boxes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一视觉行上的多个光学框收成一簇，取并框。"""
    if not boxes:
        return []
    ordered = sorted(boxes, key=lambda b: (b["bbox"][1], b["bbox"][0]))
    hs = sorted(b["bbox"][3] - b["bbox"][1] for b in ordered)
    med_h = hs[len(hs) // 2]
    gap = max(3.0, med_h * 0.45)
    clusters: list[list[dict[str, Any]]] = [[ordered[0]]]
    for box in ordered[1:]:
        prev = clusters[-1][-1]["bbox"]
        cur = box["bbox"]
        overlap = min(prev[3], cur[3]) - max(prev[1], cur[1])
        if overlap > 0 or (cur[1] - prev[3]) <= gap:
            clusters[-1].append(box)
        else:
            clusters.append([box])
    lines: list[dict[str, Any]] = []
    for group in clusters:
        x0 = min(g["bbox"][0] for g in group)
        y0 = min(g["bbox"][1] for g in group)
        x1 = max(g["bbox"][2] for g in group)
        y1 = max(g["bbox"][3] for g in group)
        entry: dict[str, Any] = {
            "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
        }
        scores = [g["score"] for g in group if g.get("score") is not None]
        if scores:
            entry["score"] = min(scores)
        lines.append(entry)
    return lines


def attach_ocr_lines(
    structured: list[dict[str, Any]],
    model_page: list[dict[str, Any]],
    bb: list[float] | None,
    page_size: list[float],
) -> tuple[list[dict[str, Any]], float | None, float | None]:
    """用检测结果里的光学行框替换 layout 合成的一行。

    ``ocr_text`` 没有字。layout 的 spans 整段落在第一行；其余行只有框。
    首行缩进 = 最上簇最左 x − 后簇最左 x 的众数；行高 = 各簇高度中位数。
    """
    optical = _cluster_ocr_lines(_ocr_boxes_in(model_page, bb, page_size))
    if not optical:
        return structured, None, None
    spans: list[dict[str, Any]] = []
    for row in structured:
        spans.extend(row.get("spans") or [])
    lines: list[dict[str, Any]] = []
    for i, geo in enumerate(optical):
        entry = dict(geo)
        entry["spans"] = spans if i == 0 else []
        lines.append(entry)
    first_indent = None
    if len(optical) >= 2:
        first_x = float(optical[0]["bbox"][0])
        later = _mode_x([float(g["bbox"][0]) for g in optical[1:]])
        dx = first_x - later
        if dx >= _OCR_MIN_INDENT:
            first_indent = round(dx, 2)
    hs = [float(g["bbox"][3]) - float(g["bbox"][1]) for g in optical]
    line_height = round(sorted(hs)[len(hs) // 2], 2) if hs else None
    return lines, first_indent, line_height


def ingest_page(book: Path, page_no: int, box_prefixes: list[str] | None = None,
                model: list[list[dict[str, Any]]] | None = None,
                v1: list[dict[str, Any]] | None = None,
                v2: list[list[dict[str, Any]]] | None = None,
                layout: dict[str, Any] | None = None) -> dict[str, Any]:
    box_prefixes = box_prefixes if box_prefixes is not None else default_box_prefixes()
    layout = layout if layout is not None else load_layout(book)
    if not layout or not isinstance(layout.get("pdf_info"), list):
        raise FileNotFoundError(f"missing layout.json under {book / 'mineru'}")
    model = model if model is not None else load_model(book)
    v1 = v1 if v1 is not None else load_v1(book)
    v2 = v2 if v2 is not None else load_v2(book)
    page = None
    for cand in layout["pdf_info"]:
        if not isinstance(cand, dict):
            continue
        try:
            idx = int(cand.get("page_idx") or 0)
        except (TypeError, ValueError):
            idx = 0
        if idx == page_no - 1:
            page = cand
            break
    if page is None:
        raise FileNotFoundError(f"page {page_no} not in layout")
    raw_size = page.get("page_size")
    pw = 0.0
    ph = 0.0
    if isinstance(raw_size, (list, tuple)) and len(raw_size) >= 2:
        try:
            pw = float(raw_size[0] or 0)
            ph = float(raw_size[1] or 0)
        except (TypeError, ValueError):
            pw, ph = 0.0, 0.0
    if pw <= 0 or ph <= 0:
        xs: list[float] = []
        ys: list[float] = []
        for cand_b in list(page.get("para_blocks") or []) + list(page.get("discarded_blocks") or []):
            if not isinstance(cand_b, dict):
                continue
            bb0 = _block_bbox(cand_b)
            if not bb0:
                continue
            xs.extend((bb0[0], bb0[2]))
            ys.extend((bb0[1], bb0[3]))
        pw = max(xs) if xs else 0.0
        ph = max(ys) if ys else 0.0
    page_size = [pw, ph]

    body = [b for b in (page.get("para_blocks") or []) if isinstance(b, dict)]
    chrome_blocks = [b for b in (page.get("discarded_blocks") or []) if isinstance(b, dict)]
    model_page = model[page_no - 1] if 0 < page_no <= len(model) else []
    prev_page = None
    if page_no > 1:
        for cand in layout["pdf_info"]:
            if not isinstance(cand, dict):
                continue
            try:
                prev_idx = int(cand.get("page_idx") or 0)
            except (TypeError, ValueError):
                prev_idx = 0
            if prev_idx == page_no - 2:
                prev_page = cand
                break
    leftover = _outgoing_lines(prev_page)

    blocks: list[dict[str, Any]] = []
    for b in body:
        bb = _block_bbox(b)
        raw_lines = _on_page_lines(b)
        restored = False
        if not _line_has_text(raw_lines):
            hit, leftover = _partition_outgoing(leftover, bb)
            if hit:
                raw_lines = hit
                restored = True
        structured, text = render_lines(raw_lines)
        if model_page:
            eq_latex = _eq_span_latex(model_page, raw_lines, page_size)
            if eq_latex:
                idx = 0
                for span in ir_spans(structured):
                    if span.get("t") == "eq" and idx < len(eq_latex):
                        if eq_latex[idx]:
                            span["latex"] = eq_latex[idx]
                        idx += 1
        first_ind = None
        line_h = None
        if model_page and bb and str(b.get("type") or "text") in _OCR_LINE_TYPES:
            structured, first_ind, line_h = attach_ocr_lines(
                structured, model_page, bb, page_size,
            )
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
        if first_ind is not None:
            row["first_indent"] = first_ind
        if line_h is not None:
            row["line_height"] = line_h
        level = b.get("level")
        if isinstance(level, (int, float)):
            row["level"] = max(0, min(6, int(level)))
        if asset:
            row["asset"] = asset
        caps = _child_captions(b)
        if caps:
            row["captions"] = caps
            row["caption"] = "\n".join(item["text"] for item in caps)
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
        for key in ("angle", "merge_prev", "lines_deleted"):
            if restored and key == "lines_deleted":
                continue
            if key in b and b[key] is not None:
                row[key] = b[key]
        cross = any(
            isinstance(span, dict) and span.get("cross_page")
            for line in raw_lines
            for span in (line.get("spans") or [])
        )
        if cross:
            row["cross_page"] = True
        if bb and pw > 0:
            row["width_frac"] = round((bb[2] - bb[0]) / pw, 3)
        blocks.append(row)

    for line in leftover:
        line_bb = _block_bbox(line)
        extra, extra_text = render_lines([line])
        if not extra_text and not extra:
            continue
        extra_ind = None
        extra_lh = None
        if model_page and line_bb:
            extra, extra_ind, extra_lh = attach_ocr_lines(
                extra, model_page, line_bb, page_size,
            )
        extra_row: dict[str, Any] = {
            "_bb": line_bb,
            "id": block_id({"type": "text", "bbox": line_bb}, extra_text, ""),
            "type": "text",
            "order": len(blocks),
            "lines": extra,
            "text": extra_text,
        }
        if extra_ind is not None:
            extra_row["first_indent"] = extra_ind
        if extra_lh is not None:
            extra_row["line_height"] = extra_lh
        if line_bb and pw > 0:
            extra_row["width_frac"] = round((line_bb[2] - line_bb[0]) / pw, 3)
        blocks.append(extra_row)

    # 表格 HTML：layout span 缺时按 bbox 中心距配 v1 table_body
    v1_tables = [
        {"html": str(b.get("table_body") or "").strip(), "bbox": b.get("bbox")}
        for b in v1
        if int(b.get("page_idx") or 0) == page_no - 1
        and str(b.get("type") or "") == "table"
        and str(b.get("table_body") or "").strip()
    ]
    need_tables = [blk for blk in blocks if blk["type"] == "table" and not blk.get("table_html")]
    for blk, src in _pair_by_center(need_tables, v1_tables):
        if src.get("html"):
            blk["table_html"] = src["html"]

    # figure 题注：子块已优先；缺则按面积大的先配，同一句不复用
    taken_caps = {
        str(blk.get("caption") or "").strip()
        for blk in blocks
        if str(blk.get("caption") or "").strip()
    }
    need_caps = [
        blk for blk in blocks
        if blk["type"] in _FIGURE_TYPES and not blk.get("caption")
    ]
    need_caps.sort(key=lambda b: _bbox_area(b.get("_bb")), reverse=True)
    for blk, src in _pair_by_center(need_caps, _page_v1_captions(v1, page_no - 1)):
        text = str(src.get("text") or "").strip()
        if not text or text in taken_caps:
            continue
        blk["caption"] = text
        taken_caps.add(text)

    # v2 标题 level 互证（骨架仍用 layout；不一致只打标）
    v2_page = v2[page_no - 1] if 0 < page_no <= len(v2) else []
    v2_by_text: dict[str, list[int]] = {}
    for text, level in _v2_titles(v2_page):
        v2_by_text.setdefault(text, []).append(level)
    for blk in blocks:
        if blk["type"] != "title" or blk.get("level") is None:
            continue
        hits = v2_by_text.get(re.sub(r"\s+", "", str(blk.get("text") or ""))) or []
        if len(hits) == 1 and hits[0] != blk["level"]:
            blk["level_conflict"] = hits[0]

    # 废弃块里的栏目条（想一想 / 学习提示）钉回版面，不当页眉
    promoted: list[dict[str, Any]] = []
    for raw in chrome_blocks:
        typ = str(raw.get("type") or "")
        if typ not in {"header", "page_header"}:
            continue
        text = _join_text(raw.get("lines"))
        hit = _match_box_kind(text, box_prefixes)
        if not hit:
            continue
        bb = _block_bbox(raw)
        if not bb:
            continue
        overlap = False
        for blk in blocks:
            ob = blk.get("_bb")
            if not ob:
                continue
            ix0, iy0 = max(bb[0], ob[0]), max(bb[1], ob[1])
            ix1, iy1 = min(bb[2], ob[2]), min(bb[3], ob[3])
            if ix1 > ix0 and iy1 > iy0:
                overlap = True
                break
        if overlap:
            continue
        promoted.append(raw)
        line = {"spans": [{"t": "text", "c": text}], "bbox": [round(float(x), 2) for x in bb]}
        blocks.append(
            {
                "_bb": bb,
                "id": block_id(raw, text, ""),
                "type": "title",
                "order": int(raw.get("index") if isinstance(raw.get("index"), int) else len(blocks)),
                "lines": [line],
                "text": text,
                "kind": "box",
                "box_kind": hit,
                **({"width_frac": round((bb[2] - bb[0]) / pw, 3)} if pw > 0 else {}),
            }
        )

    page_h = float(page_size[1] or 0)

    # kind：MinerU 类型 + 栏目词表。位置归块上的 slot，不再写 zone/col/place。
    for blk in blocks:
        bb = blk.pop("_bb", None)
        if bb:
            blk["bbox"] = [round(float(x), 2) for x in bb]
        if blk.get("kind"):
            continue
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
        blk["kind"] = kind

    # 行内小图：高 < 8% 页高、宽 < 12% 页宽，且与文本块阅读序相邻
    for i, (blk, raw) in enumerate(zip(blocks, body)):
        if blk["type"] not in _FIGURE_TYPES:
            continue
        bb = _block_bbox(raw)
        hfrac = ((bb[3] - bb[1]) / page_h) if bb and page_h > 0 else 1.0
        wf = float(blk.get("width_frac") or 0.0)
        if hfrac < _INLINE_IMG_HFRAC and wf < _INLINE_IMG_WFRAC:
            neighbor_text = any(
                0 <= j < len(blocks) and blocks[j]["type"] == "text"
                for j in (i - 1, i + 1)
            )
            if neighbor_text:
                blk["kind"] = "inline_image"

    # 并排图行组（y 重叠聚类，仅 figure 参与）。blocks 与 body 按构造
    # 一一对应，用 id→raw 映射取 bbox——不依赖 index 字段（无 index 的
    # 块不会塌缩到 key 0），也不 zip（inline_image 已剔除会错位）。
    fig_rows = [blk for blk in blocks if blk["kind"] == "figure"]
    raw_by_id = {blk["id"]: raw for blk, raw in zip(blocks, body)}
    fig_bboxes = {}
    for blk in fig_rows:
        raw = raw_by_id.get(blk["id"])
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
            for member in cluster:
                cbb = fig_bboxes.get(member["id"])
                if not cbb:
                    continue
                overlap = min(bb[3], cbb[3]) - max(bb[1], cbb[1])
                if overlap > 0.5 * min(bb[3] - bb[1], cbb[3] - cbb[1]):
                    cluster.append(blk)
                    placed = True
                    break
            if placed:
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
            raw = raw_by_id.get(blk["id"])
            latex = _join_latex(model_page, _block_bbox(raw) if raw else None, page_size, {"equation"})
            if latex:
                blk["latex"] = latex

    # chrome：discarded 按 order 插回阅读序；框留下给页码左右
    chrome: dict[str, Any] = {"headers": [], "page_numbers": [], "footers": [], "footnotes": []}
    for b in sorted(chrome_blocks, key=lambda x: int(x.get("index") or 0)):
        bucket = _CHROME_TYPES.get(str(b.get("type") or ""))
        if not bucket:
            continue
        text = _join_text(b.get("lines"))
        bb = _block_bbox(b)
        boxed = [round(float(x), 2) for x in bb] if bb else None
        if bucket == "footnotes":
            row: dict[str, Any] = {"order": int(b.get("index") or 0), "text": text}
            if boxed:
                row["bbox"] = boxed
            chrome["footnotes"].append(row)
        elif text:
            if bucket == "headers" and _match_box_kind(text, box_prefixes):
                continue
            chrome[bucket].append({"text": text, "bbox": boxed} if boxed else text)

    source = {"schema": IR_SCHEMA}
    lay_path = book / "mineru" / "layout.json"
    if lay_path.is_file():
        source["layout"] = sha16(lay_path.read_bytes())
    model_path = _model_path(book)
    if model_path is not None:
        source["model"] = sha16(model_path.read_bytes())
    v1_path = _v1_path(book)
    if v1_path is not None:
        source["v1"] = sha16(v1_path.read_bytes())
    v2_path = _v2_path(book)
    if v2_path is not None:
        source["v2"] = sha16(v2_path.read_bytes())
    ver = layout.get("_version_name")
    if ver:
        source["mineru"] = str(ver)

    from ir_bands import assemble_page, attach_slots, folio_side

    assembled = assemble_page(
        page,
        book.name,
        page_no,
        extra_blocks=promoted,
    )
    attach_slots(assembled, blocks)
    folio = folio_side(
        [_block_bbox(b) for b in chrome_blocks],
        [str(b.get("type") or "") for b in chrome_blocks],
        pw,
    )

    out = {
        "page": page_no,
        "book": book.name,
        "page_size": page_size,
        "source": source,
        "blocks": blocks,
        "chrome": chrome,
    }
    if folio:
        out["folio"] = folio
    return out


def compute_remap(old_pages: dict[int, list[dict[str, Any]]],
                  new_pages: dict[int, dict[str, Any]]) -> dict[str, str]:
    """旧 IR 的块 ID → 新 IR 的块 ID。先按规范化文本精确配，再按同 type 最近 order 配。

    收口块（kb-close-*）是派生物，ID 按位置生成、每次摄取重算，不进 remap。
    """
    remap: dict[str, str] = {}
    used: set[str] = set()
    new_by_text: dict[str, list[dict[str, Any]]] = {}
    for page_no, page in new_pages.items():
        for blk in page.get("blocks", []):
            if is_close_block(blk):
                continue
            key = re.sub(r"\s+", "", str(blk.get("text") or ""))
            new_by_text.setdefault(key, []).append(blk)

    def _candidates(old: dict[str, Any], page_no: int) -> dict[str, Any] | None:
        if is_close_block(old):
            return None
        key = re.sub(r"\s+", "", str(old.get("text") or ""))

        def _claim(cand: dict[str, Any]) -> dict[str, Any]:
            used.add(cand["id"])
            return cand

        # 1) 页内同文精确配（最安全）
        page = new_pages.get(page_no)
        if page:
            for cand in new_by_text.get(key, []):
                if cand["id"] in used or cand.get("type") != old.get("type"):
                    continue
                if any(b["id"] == cand["id"] for b in page.get("blocks", [])):
                    return _claim(cand)
        # 2) 全局同文配，仅当全文唯一（教材重复例题常见，
        #    不唯一的跨页同文无法判归属，宁缺勿错）
        pool = [c for c in new_by_text.get(key, [])
                if c["id"] not in used and c.get("type") == old.get("type")]
        if len(pool) == 1 and len(new_by_text.get(key, [])) == 1:
            return _claim(pool[0])
        # 3) 页内同 type 最近 order
        if page:
            same = [b for b in page.get("blocks", [])
                    if b.get("type") == old.get("type") and b["id"] not in used
                    and not is_close_block(b)]
            if same:
                target = min(same, key=lambda b: abs(int(b.get("order", 0)) - int(old.get("order", 0))))
                return _claim(target)
        return None

    for page_no, old_blocks in old_pages.items():
        for old in old_blocks:
            cand = _candidates(old, page_no)
            if cand is not None and cand["id"] != old.get("id"):
                remap[str(old.get("id"))] = cand["id"]
    return remap


def _esc_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _figure_md(blk: dict[str, Any]) -> str:
    asset = blk.get("asset") or ""
    caption = (blk.get("caption") or "").strip()
    if not asset:
        return caption
    wf = blk.get("width_frac")
    style = ""
    if isinstance(wf, (int, float)) and wf > 0:
        style = f' style="width:{round(float(wf) * 100, 1)}%"'
    alt = _esc_html(caption or "插图")
    img = f'<img src="images/{_esc_html(asset)}" alt="{alt}">'
    cap = f"<figcaption>{_esc_html(caption)}</figcaption>" if caption else ""
    return f'<figure class="kb-ir-fig"{style}>{img}{cap}</figure>'


def _inline_img_md(blk: dict[str, Any]) -> str:
    asset = blk.get("asset") or ""
    alt = (blk.get("caption") or "图").strip() or "图"
    if not asset:
        return ""
    return f"![{alt}](images/{asset})"


def _row_html(cells: list[dict[str, Any]]) -> str:
    parts = ['<div class="kb-ir-figrow">']
    for cell in cells:
        asset = cell.get("asset") or ""
        caption = (cell.get("caption") or "").strip()
        wf = cell.get("width_frac")
        style = ""
        if isinstance(wf, (int, float)) and wf > 0:
            style = f' style="width:{round(float(wf) * 100, 1)}%"'
        fig = [f'<figure class="kb-ir-figcell"{style}>']
        if asset:
            alt = _esc_html(caption or "插图")
            fig.append(f'<img src="images/{_esc_html(asset)}" alt="{alt}">')
        if caption:
            fig.append(f"<figcaption>{_esc_html(caption)}</figcaption>")
        fig.append("</figure>")
        parts.append("".join(fig))
    parts.append("</div>")
    return "".join(parts)


def _block_md(blk: dict[str, Any]) -> str:
    btype = blk.get("type")
    text = (blk.get("text") or "").strip()
    caption = (blk.get("caption") or "").strip()
    if btype == "title":
        if blk.get("kind") == "box":
            kind = blk.get("box_kind") or ""
            body = "" if _strip_box_punct(text) == kind else text
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
            inner = f"$$\n{latex}\n$$"
        elif blk.get("asset"):
            inner = f"![公式](images/{blk['asset']})"
        else:
            inner = text
        if not inner:
            return ""
        if "kb-ir-eq" in inner:
            return inner
        return f'<div class="kb-ir-eq">\n{inner}\n</div>'
    return text


def render_page_md(page: dict[str, Any], notes: list | None = None) -> str:
    """Canonical IR 一页 → Markdown（新库正文预览/对拍用）。

    - 行内小图并入邻行文本；
    - 并排图输出带 width_frac 的 HTML 行组；
    - 行间公式包 kb-ir-eq，居中交给 page-ir 插槽 CSS；
    - 页注收在页尾分隔线后（chrome 区块）。
    - 传 notes 时先叠加注解（应用失败的部分不进正文，由调用方看报告）。
    """
    if notes:
        page, _rejected = apply_notes(page, notes)
    # 收口块是 HTML 版式用的合并行，正文 Markdown 仍按源块出，否则同一段出两遍
    blocks = [b for b in (page.get("blocks") or []) if not is_close_block(b)]
    extras: dict[int, list[tuple[str, str]]] = {}
    skip: set[int] = set()
    for i, blk in enumerate(blocks):
        if blk.get("kind") != "inline_image":
            continue
        img = _inline_img_md(blk)
        if not img:
            continue
        prev_ok = i > 0 and blocks[i - 1].get("type") == "text" and (i - 1) not in skip
        next_ok = i + 1 < len(blocks) and blocks[i + 1].get("type") == "text"
        if prev_ok:
            extras.setdefault(i - 1, []).append(("after", img))
            skip.add(i)
        elif next_ok:
            extras.setdefault(i + 1, []).append(("before", img))
            skip.add(i)

    parts: list[str] = []
    fig_rows: dict[int, list[dict[str, Any]]] = {}
    for blk in blocks:
        row = blk.get("row")
        if row is not None:
            fig_rows.setdefault(int(row), []).append(blk)
    emitted: set[int] = set()

    for i, blk in enumerate(blocks):
        if i in skip:
            continue
        row = blk.get("row")
        if row is not None:
            rid = int(row)
            if rid in emitted:
                continue
            emitted.add(rid)
            html = _row_html(fig_rows[rid])
            if html:
                parts.append(html)
            continue
        md = _block_md(blk)
        for side, img in extras.get(i, []):
            if side == "before":
                md = f"{img} {md}".strip() if md else img
            else:
                md = f"{md} {img}".strip() if md else img
        if md:
            parts.append(md)

    chrome = page.get("chrome") or {}
    footnotes = [f.get("text", "") for f in chrome.get("footnotes", []) if f.get("text")]
    if footnotes:
        parts.append("---\n\n" + "\n\n".join(footnotes))
    return "\n\n".join(p for p in parts if p) + "\n"


CLOSE_PREFIX = "kb-close-"


def is_close_block(blk: dict[str, Any]) -> bool:
    """收口块是从源块派生的合并行，不是第六个来源。

    排版已经不再合并原始块，新摄取的页不会再有这种块。这一支只为还没重摄的老页
    留着：它们磁盘上仍有 kb-close-*，读页、注解、目录都得继续认。
    """
    return str(blk.get("id") or "").startswith(CLOSE_PREFIX)


def _sync_close_blocks(
    blocks: list[dict[str, Any]], touched: set[str] | None = None
) -> list[dict[str, Any]]:
    """源块被注解改字或删掉之后，照着现状重生成收口块；源块全没了就撤掉这条收口。

    `touched` 是本次注解真正动过的源块 ID。只重生成受影响的那几条收口，原因有两条：

    1. 摄取时收口行的 text 来自版式器的 piece 文本，它比收口块自己的 span 更全
       （五源合块的整块文本）。照 span 重拼必然拼不回来，无条件重生成就会让接口
       吐出的字跟磁盘上的字不一样——没有任何注解也不一样。
    2. 绝大多数读页请求一条注解都没有，白跑一遍重生成纯属浪费。

    传 None 表示「不知道动了谁」，退回全量重生成（老行为）。
    """
    from ir_bands import plain_text, spans_from_block

    src = {b["id"]: b for b in blocks if not is_close_block(b)}
    out: list[dict[str, Any]] = []
    for blk in blocks:
        if not is_close_block(blk):
            out.append(blk)
            continue
        if touched is not None:
            deps = {str(x) for x in blk.get("source_ids") or []}
            deps |= {
                str(s.get("b")) for s in ir_spans(blk.get("lines")) if s.get("b")
            }
            if not (deps & touched):
                out.append(blk)
                continue
        rows: list[list[dict[str, Any]]] = []
        texts: list[str] = []
        alive: list[str] = []
        for row in ir_line_rows(blk.get("lines")):
            groups: list[dict[str, Any]] = []
            for span in row["spans"]:
                if str(span.get("t") or "") == "tab":
                    groups.append({"tab": True})
                    continue
                bid = str(span.get("b") or "")
                last = groups[-1] if groups else None
                # 制表位落在同一个源块内部时（扫描稿自带的制表符），它不是收口两栏的
                # 分栏点。不归回上一组的话，同一个源块会被当成两段，而下面每段都按
                # spans_from_block 整块重取，于是这段话被复制两遍。
                if last is not None and last.get("tab") and len(groups) >= 2:
                    prev = groups[-2]
                    if not prev.get("tab") and prev.get("bid") == bid:
                        groups.pop()
                        prev["spans"].append(span)
                        continue
                if last is not None and not last.get("tab") and last.get("bid") == bid:
                    last["spans"].append(span)
                else:
                    groups.append({"bid": bid, "spans": [span]})
            new_row: list[dict[str, Any]] = []
            bits: list[str] = []
            pending_tab = False
            for group in groups:
                if group.get("tab"):
                    # 前一段可能因为源块被 delete 而落空，这时制表位要继续挂着，
                    # 等下一段真落地再补；直接 bool(new_row) 会把它吞掉。
                    pending_tab = pending_tab or bool(new_row)
                    continue
                bid = str(group.get("bid") or "")
                if bid:
                    origin = src.get(bid)
                    if origin is None:  # 源块被 delete 了
                        continue
                    spans = [dict(s, b=bid) for s in spans_from_block(origin)]
                    piece = plain_text(str(origin.get("text") or ""))
                else:  # 没有来源的兜底 span（算法没给成员时按行文本切的），原样留着
                    spans = [dict(s) for s in group["spans"]]
                    piece = plain_text("".join(str(s.get("c") or "") for s in spans))
                if not spans:
                    continue
                if pending_tab:
                    new_row.append({"t": "tab", "c": "\t"})
                    bits.append("\t")
                    pending_tab = False
                new_row.extend(spans)
                bits.append(piece)
                if bid and bid not in alive:
                    alive.append(bid)
            if not new_row:
                continue
            rows.append(new_row)
            texts.append("".join(bits))
        if not rows:
            continue
        blk["lines"] = rows
        blk["text"] = "\n".join(texts)
        if blk.get("source_ids") is not None:
            blk["source_ids"] = alive
        out.append(blk)
    return out


def _remap_tree(node: dict[str, Any], mapping: dict[int, int]) -> None:
    mems = [mapping[i] for i in node.get("members") or [] if i in mapping]
    node["members"] = mems
    for kid in node.get("children") or []:
        _remap_tree(kid, mapping)
    if node.get("kind") == "flow":
        node["empty"] = not mems


def apply_notes(page: dict[str, Any], notes: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """把注解叠到一页 IR 上（纯函数，不改输入）。返回 (新页, 拒绝报告)。

    支持的 op（都按块 ID 定位，只能点源块，不能点 kb-close-* 收口块）：
    - replace: new 替换块文本里第一个 old（old 必须存在，否则拒绝）；
    - delete:  整块移除；
    - retitle: 改 title 块的 text（old 为原标题全文，精确匹配）。
    改完统一重生成收口块。旧页若还有 layout.tree，把树里的块下标挪到新位置；
    没有树就跳过。
    按 notes 顺序应用；前面成功的 replace 会影响后面的 old 匹配。
    """
    import copy

    out = copy.deepcopy(page)
    by_id = {b["id"]: b for b in out.get("blocks", [])}
    rejected: list[dict[str, Any]] = []
    touched: set[str] = set()  # 真被改动/删除的源块，只有它们的收口块需要重生成

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
        if is_close_block(blk):
            _reject(note, "close block is derived, note the source block")
            continue
        op = str(note.get("op") or "replace")
        if op == "delete":
            out["blocks"] = [b for b in out["blocks"] if b["id"] != bid]
            by_id.pop(bid, None)
            touched.add(bid)
            continue
        old = str(note.get("old") or "")
        new = str(note.get("new") or "")
        text = str(blk.get("text") or "")
        if op == "replace":
            # span 层是唯一匹配真相源：blk["text"] 是渲染形态（eq 带 $），
            # 直接对它匹配会命中 $ 边界/跨 span，替换后 text 与 lines 分叉。
            target = None
            for span in ir_spans(blk.get("lines")):
                if span.get("t") in ("text", "eq") and old and old in str(span.get("c", "")):
                    target = span
                    break
            if target is None:
                _reject(note, "old not found")
                continue
            target["c"] = str(target["c"]).replace(old, new, 1)
            blk["text"] = text.replace(old, new, 1)
            touched.add(bid)
        elif op == "retitle":
            if blk.get("type") != "title":
                _reject(note, "not a title block")
                continue
            if old and old != text:
                _reject(note, "old not found")
                continue
            blk["text"] = new
            # 标题可能被切成好几段 span（带下标、跨行、脚注标记）。逐段赋值会把
            # 新标题写进每一段，出来就是「第1章 种群和群落第1章 种群和群落」。
            # 按新文本重切一次，切法跟摄取时同源，顺带支持新标题里带下标。
            from ir_bands import split_marks

            blk["lines"] = [split_marks(new) or [{"t": "text", "c": new}]]
            touched.add(bid)
        else:
            _reject(note, f"unknown op {op}")

    before = [str(b.get("id") or "") for b in page.get("blocks") or []]
    out["blocks"] = _sync_close_blocks(out["blocks"], touched)
    after = {str(b.get("id") or ""): i for i, b in enumerate(out["blocks"])}
    mapping = {i: after[bid] for i, bid in enumerate(before) if bid in after}
    if len(mapping) != len(before):
        tree = (out.get("layout") or {}).get("tree")
        if isinstance(tree, dict):
            _remap_tree(tree, mapping)
    return out, rejected
