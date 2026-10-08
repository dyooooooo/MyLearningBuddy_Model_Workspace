"""Printed catalog tools. Facts first; the local 35B only where print has no text.

toc_locate    catalog pages: one 35B call on thumbnails (index pages first)
toc_facts     one page's text lines with position, size, weight: the PDF text
              layer; scanned pages get 35B lines with boxes instead
toc_assemble  rows and levels for the whole catalog (no model)
toc_fill      page numbers the text layer lacks: 35B reads small crops
toc_verify    harness on the body: offset printed -> PDF page from running
              page numbers, then each label must be on the page it points to;
              labels are also checked against MinerU's reading of the page
toc_golden    compare toc.print with frozen answers in eval/printed_toc/

Nothing here writes; ``printed_toc_agent`` writes textbooks/<id>/toc.print.
"""
from __future__ import annotations

import base64
import collections
import difflib
import io
import json
import os
import re
import time
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any

import printed_toc_layout as layout

_SKIP_TYPES = {"header", "footer", "page_number", "page_footnote", "aside_text"}
_TAG = re.compile(r"<[^>]+>")
_NO_INDEX_WINDOWS = ((1, 16), (17, 32))
_THUMB_SIDE = 384
_READ_DPI = 120
_CROP_ZOOM = 4
_HEADER_BAND = 0.12
_HEADER_PAGES = 12
_HEADER_REPEAT = 3
_VERIFY_PAGES = 40
_OFFSET_VOTES = 3
_HEADER_DRIFT = 12.0
_TEXT_LAYER_MIN = 40
_NUMBERING = re.compile(
    r"^(?:第\s*\S{1,4}?\s*[章节单元课部分编]|附录\s*\d*|\d+(?:\.\d+)*|[A-H](?=\s)|"
    r"[一二三四五六七八九十]+[、\s]|[（(][^）)]{1,3}[）)]|\*)\s*"
)


# ---------------------------------------------------------------- shared


def _vision_url() -> str:
    return os.environ.get("TOC_VISION_URL", "http://127.0.0.1:8081/v1/chat/completions")


def _home() -> Path:
    return Path(__file__).resolve().parents[1]


def _prompt(name: str) -> str:
    return (_home() / "prompts" / name).read_text(encoding="utf-8")


def book_dir(book_id: str, root: str = "") -> Path:
    base = Path(root) if root else _home() / "textbooks"
    sid = (book_id or "").strip()
    if (base / sid).is_dir():
        return base / sid
    hits = [p for p in base.iterdir() if p.is_dir() and p.name.startswith(sid)] if sid else []
    if not hits:
        raise FileNotFoundError(sid)
    return hits[0]


def _load_json_list(path: Path | None) -> list:
    if path is None or not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _mineru_file(book: Path, suffix: str) -> Path | None:
    hits = sorted((book / "mineru").glob(f"*{suffix}"))
    return hits[0] if hits else None


def _split(text: object) -> list[str]:
    return [ln.strip() for ln in _TAG.sub("", str(text or "")).splitlines() if ln.strip()]


def collect_pages(book: Path) -> dict[str, Any]:
    """MinerU ``index`` lines, page text and printed page numbers per page. Read only."""
    model = _load_json_list(_mineru_file(book, "_model.json"))
    index: dict[int, list[str]] = {}
    for idx, page in enumerate(model):
        for item in page if isinstance(page, list) else []:
            if isinstance(item, dict) and item.get("type") == "index":
                index.setdefault(idx + 1, []).extend(_split(item.get("content")))
    text: dict[int, list[str]] = {}
    numbers: dict[int, list[str]] = {}
    for item in _load_json_list(_mineru_file(book, "_content_list.json")):
        if not isinstance(item, dict):
            continue
        page = int(item.get("page_idx", -1)) + 1
        if page <= 0:
            continue
        if item.get("type") == "page_number":
            numbers.setdefault(page, []).extend(_split(item.get("text")))
            continue
        if item.get("type") in _SKIP_TYPES:
            continue
        raw = item.get("list_items") if isinstance(item.get("list_items"), list) else item.get("text")
        lines = _split("\n".join(map(str, raw)) if isinstance(raw, list) else raw)
        if lines:
            text.setdefault(page, []).extend(lines)
    return {"page_count": len(model), "index": index, "text": text, "numbers": numbers}


def _open_pdf(book: Path):
    import fitz

    path = book / "book.pdf"
    return fitz.open(path) if path.is_file() else None


def page_image(book: Path, page: int, side: int = 0) -> bytes | None:
    """Page JPEG from images/, or rendered from book.pdf when missing."""
    from PIL import Image

    path = book / "images" / f"p{page:04d}.jpg"
    if path.is_file():
        img = Image.open(path)
    else:
        doc = _open_pdf(book)
        if doc is None or page > doc.page_count:
            return None
        with doc:
            pix = doc[page - 1].get_pixmap(dpi=_READ_DPI)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    img = img.convert("RGB")
    if side:
        img.thumbnail((side, side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70 if side else 85)
    return buf.getvalue()


def _image_part(raw: bytes) -> dict[str, Any]:
    url = "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
    return {"type": "image_url", "image_url": {"url": url}}


def _chat(content: list[dict[str, Any]], max_tokens: int) -> tuple[dict[str, Any], float]:
    payload = {
        "model": "local",
        "temperature": 0,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": content}],
    }
    started = time.perf_counter()
    req = urllib.request.Request(
        _vision_url(),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    elapsed = round(time.perf_counter() - started, 3)
    text = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start : end + 1]) if 0 <= start < end else {}
    except json.JSONDecodeError:
        data = {}
    return (data if isinstance(data, dict) else {}), elapsed


# ---------------------------------------------------------------- 1 locate


def _plan(collected: dict[str, Any]) -> tuple[str, list[list[int]]]:
    count = int(collected["page_count"] or 0)
    front = sorted(p for p in collected["index"] if p <= _NO_INDEX_WINDOWS[0][1])
    if front:
        around = set(front) | {front[0] - 1, front[-1] + 1}
        return "index", [sorted(p for p in around if 2 <= p <= count)]
    windows = [list(range(a, min(b, count) + 1)) for a, b in _NO_INDEX_WINDOWS if a <= count]
    return "model", windows


def _locate_window(book: Path, candidates: list[int]) -> tuple[list[int], float]:
    content: list[dict[str, Any]] = [{"type": "text", "text": _prompt("printed_toc_locate.md")}]
    shown: list[int] = []
    for page in candidates:
        raw = page_image(book, page, _THUMB_SIDE)
        if raw is None:
            continue
        content.append({"type": "text", "text": f"第 {page} 页："})
        content.append(_image_part(raw))
        shown.append(page)
    if not shown:
        return [], 0.0
    data, elapsed = _chat(content, 60)
    pages: list[int] = []
    for raw in data.get("toc_pages") or []:
        try:
            page = int(raw)
        except (TypeError, ValueError):
            continue
        if page in shown and page not in pages:
            pages.append(page)
    return sorted(pages), elapsed


def locate(book: Path, collected: dict[str, Any]) -> dict[str, Any]:
    route, windows = _plan(collected)
    toc_pages: list[int] = []
    seconds: list[float] = []
    for window in windows:
        found, elapsed = _locate_window(book, window)
        seconds.append(elapsed)
        if found:
            toc_pages = found
            break
    if not toc_pages and route == "index":
        toc_pages = sorted(p for p in collected["index"] if p in windows[0])
    return {"route": route, "candidates": windows, "toc_pages": toc_pages, "seconds": seconds}


def toc_locate(book_id: str, root: str = "") -> dict[str, Any]:
    """Find the printed catalog pages (one 35B call on thumbnails). Read only."""
    book = book_dir(book_id, root)
    return locate(book, collect_pages(book))


# ---------------------------------------------------------------- 2 facts


def _in_band(ln: dict[str, Any], height: float) -> bool:
    middle = (ln["y0"] + ln["y1"]) / 2
    return middle < height * _HEADER_BAND or middle > height * (1 - _HEADER_BAND)


def running_headers(doc: Any, toc_pages: list[int]) -> dict[str, float]:
    """Header / footer texts repeated on the pages after the catalog -> their usual height."""
    if doc is None or not toc_pages:
        return {}
    seen: dict[str, list[float]] = {}
    last = max(toc_pages)
    for page in range(last + 1, min(doc.page_count, last + _HEADER_PAGES) + 1):
        height = doc[page - 1].rect.height
        keys: dict[str, float] = {}
        for ln in layout.text_layer_lines(doc[page - 1], edge=0):
            key = layout.header_key(ln["text"])
            if len(key) >= 2 and _in_band(ln, height):
                keys.setdefault(key, (ln["y0"] + ln["y1"]) / 2)
        for key, middle in keys.items():
            seen.setdefault(key, []).append(middle)
    return {
        key: sorted(ys)[len(ys) // 2] for key, ys in seen.items() if len(ys) >= _HEADER_REPEAT
    }


def drop_headers(item: dict[str, Any], headers: dict[str, float]) -> None:
    """A catalog line is a running header only at the edge and at the header's usual height."""
    kept = []
    for ln in item["lines"]:
        usual = headers.get(layout.header_key(ln["text"]))
        middle = (ln["y0"] + ln["y1"]) / 2
        if usual is not None and _in_band(ln, item["height"]) and abs(middle - usual) <= _HEADER_DRIFT:
            continue
        kept.append(ln)
    item["lines"] = kept


def has_text_layer(doc: Any, pages: list[int]) -> bool:
    if doc is None:
        return False
    chars = sum(len(doc[p - 1].get_text("text").strip()) for p in pages if p <= doc.page_count)
    return chars >= _TEXT_LAYER_MIN * max(1, len(pages))


def _model_lines(book: Path, page: int) -> tuple[list[dict[str, Any]], float, float, float]:
    raw = page_image(book, page)
    if raw is None:
        return [], 0.0, 0.0, 0.0
    from PIL import Image

    width, height = Image.open(io.BytesIO(raw)).size
    content = [{"type": "text", "text": _prompt("printed_toc_lines.md")}, _image_part(raw)]
    data, elapsed = _chat(content, 4000)
    rows = data.get("lines") if isinstance(data.get("lines"), list) else []
    return layout.model_lines(rows, float(width), float(height)), elapsed, float(width), float(height)


def page_facts(book: Path, doc: Any, page: int, text_layer: bool) -> dict[str, Any]:
    if text_layer:
        rect = doc[page - 1].rect
        return {
            "page": page, "method": "text_layer", "seconds": 0.0,
            "width": rect.width, "height": rect.height,
            "lines": layout.text_layer_lines(doc[page - 1]),
        }
    lines, elapsed, width, height = _model_lines(book, page)
    return {
        "page": page, "method": "model_lines", "seconds": elapsed,
        "width": width, "height": height, "lines": lines,
    }


def _public_line(ln: dict[str, Any]) -> dict[str, Any]:
    return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in ln.items() if k != "chars"}


def toc_facts(book_id: str, page: int, root: str = "") -> dict[str, Any]:
    """One page's text lines with box, size, weight (text layer, else 35B). Read only."""
    book = book_dir(book_id, root)
    doc = _open_pdf(book)
    try:
        facts = page_facts(book, doc, page, has_text_layer(doc, [page]))
    finally:
        if doc is not None:
            doc.close()
    facts["lines"] = [_public_line(ln) for ln in facts["lines"]]
    return facts


# ---------------------------------------------------------------- 3 assemble


def assemble(facts: list[dict[str, Any]], headers: dict[str, float]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in facts:
        drop_headers(item, headers)
        rows.extend(layout.page_rows(item["lines"], item["page"]))
    layout.assign_levels(rows)
    return rows


def _catalog(book: Path, pages: list[int]) -> tuple[list[dict[str, Any]], str, float]:
    doc = _open_pdf(book)
    try:
        text_layer = has_text_layer(doc, pages)
        facts = [page_facts(book, doc, page, text_layer) for page in pages]
        headers = running_headers(doc, pages) if text_layer else {}
    finally:
        if doc is not None:
            doc.close()
    seconds = round(sum(item["seconds"] for item in facts), 3)
    return assemble(facts, headers), ("text_layer" if text_layer else "model_lines"), seconds


def _pages_arg(pages: str, book: Path) -> list[int]:
    found = [int(p) for p in re.findall(r"\d+", pages or "")]
    if found:
        return found
    old = book / "toc.print"
    if old.is_file():
        return list(json.loads(old.read_text(encoding="utf-8")).get("toc_pages") or [])
    return []


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    keep = ("pdf_page", "col", "text", "level", "print_page", "hole", "size", "bold", "indent")
    return {k: row.get(k) for k in keep}


def toc_assemble(book_id: str, pages: str = "", root: str = "") -> dict[str, Any]:
    """Rows and levels of the catalog pages (default: toc_pages of toc.print). No model."""
    book = book_dir(book_id, root)
    rows, method, _ = _catalog(book, _pages_arg(pages, book))
    return {"method": method, "rows": [_public_row(r) for r in rows]}


# ---------------------------------------------------------------- 4 fill


def _crop(book: Path, doc: Any, row: dict[str, Any]) -> bytes | None:
    """Hole box in PDF points when ``doc`` is given, else in page-image pixels."""
    from PIL import Image

    x0, y0, x1, y1 = row["hole_box"]
    if doc is not None:
        import fitz

        page = doc[row["pdf_page"] - 1]
        clip = fitz.Rect(x0, y0, x1, y1) & page.rect
        if clip.is_empty:
            return None
        pix = page.get_pixmap(matrix=fitz.Matrix(_CROP_ZOOM, _CROP_ZOOM), clip=clip)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    else:
        raw = page_image(book, row["pdf_page"])
        if raw is None:
            return None
        img = Image.open(io.BytesIO(raw)).crop(tuple(int(v) for v in (x0, y0, x1, y1)))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=90)
    return buf.getvalue()


def fill(book: Path, rows: list[dict[str, Any]], method: str) -> float:
    """One 35B call per page: read the page number in each hole crop."""
    holes: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        if row["hole"] and row["hole_box"]:
            holes.setdefault(row["pdf_page"], []).append(row)
    if not holes:
        return 0.0
    seconds = 0.0
    doc = _open_pdf(book) if method == "text_layer" else None
    try:
        for page, items in holes.items():
            content: list[dict[str, Any]] = [{"type": "text", "text": _prompt("printed_toc_fill.md")}]
            shown: list[dict[str, Any]] = []
            for row in items:
                raw = _crop(book, doc, row)
                if raw is None:
                    continue
                content.append({"type": "text", "text": f"第 {len(shown) + 1} 块（{row['text'][:30]}）："})
                content.append(_image_part(raw))
                shown.append(row)
            if not shown:
                continue
            data, elapsed = _chat(content, 20 + 8 * len(shown))
            seconds += elapsed
            values = data.get("pages") if isinstance(data.get("pages"), list) else []
            if len(values) != len(shown):
                continue
            for row, value in zip(shown, values):
                number = layout._digits(str(value)) if value is not None else None
                if number is not None:
                    row["print_page"] = number
                    row["filled"] = True
    finally:
        if doc is not None:
            doc.close()
    return round(seconds, 3)


def toc_fill(book_id: str, pages: str = "", root: str = "") -> dict[str, Any]:
    """Assemble, then let the 35B read the missing page numbers from crops. Read only."""
    book = book_dir(book_id, root)
    rows, method, _ = _catalog(book, _pages_arg(pages, book))
    seconds = fill(book, rows, method)
    filled = [_public_row(r) for r in rows if r.get("filled") or r["hole"]]
    return {"method": method, "seconds": seconds, "holes": filled}


# ---------------------------------------------------------------- 5 verify


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[\W_]", "", text)


def _core(label: str) -> str:
    title = label.split("/")[0]
    title = _NUMBERING.sub("", title.strip(), count=1)
    title = re.sub(r"[(（](节选|其[一二三四])[)）]", "", title)
    return _plain(title)[:8]


def page_offset(doc: Any, collected: dict[str, Any], after: int) -> int | None:
    """PDF page minus printed page, the most common one over running page numbers."""
    printed: collections.Counter[int] = collections.Counter()
    if doc is not None:
        for page in range(after + 1, min(doc.page_count, after + _VERIFY_PAGES) + 1):
            height = doc[page - 1].rect.height
            for ln in layout.text_layer_lines(doc[page - 1], edge=0):
                middle = (ln["y0"] + ln["y1"]) / 2
                if height * 0.08 < middle < height * 0.92:
                    continue
                if re.fullmatch(r"\d{1,3}", ln["text"]):
                    printed[page - int(ln["text"])] += 1
    mineru: collections.Counter[int] = collections.Counter()
    for page, texts in collected["numbers"].items():
        for text in texts:
            if after < page <= after + _VERIFY_PAGES and re.fullmatch(r"\d{1,3}", text.strip()):
                mineru[page - int(text)] += 1
    for votes in (printed, mineru):
        if votes:
            offset, count = votes.most_common(1)[0]
            if count >= _OFFSET_VOTES:
                return offset
    return None


def _body_text(doc: Any, collected: dict[str, Any], page: int) -> str:
    if doc is not None and 1 <= page <= doc.page_count:
        text = doc[page - 1].get_text("text")
        if text.strip():
            return _plain(text)
    return _plain("".join(collected["text"].get(page, [])))


def verify(book: Path, rows: list[dict[str, Any]], collected: dict[str, Any], toc_pages: list[int]) -> int | None:
    doc = _open_pdf(book)
    try:
        offset = page_offset(doc, collected, max(toc_pages or [0]))
        cache: dict[int, str] = {}

        def body(page: int) -> str:
            if page not in cache:
                cache[page] = _body_text(doc, collected, page)
            return cache[page]

        for row in rows:
            key = _core(row["text"])
            if row["print_page"] is None or offset is None or len(key) < 2:
                row["verified"] = "no_page" if row["print_page"] is None else "unchecked"
                continue
            target = row["print_page"] + offset
            if key in body(target):
                row["verified"] = "body"
            elif any(key in body(target + d) for d in (-1, 1, 2)):
                row["verified"] = "body_near"
            else:
                row["verified"] = "not_found"
    finally:
        if doc is not None:
            doc.close()
    return offset


def check_text(rows: list[dict[str, Any]], collected: dict[str, Any]) -> None:
    """Letters in a label that MinerU never read on that page: glyphs the text layer maps wrongly."""
    for row in rows:
        lines = collected["text"].get(row["pdf_page"], []) + collected["index"].get(row["pdf_page"], [])
        seen = set(_plain("".join(lines)))
        if not seen:
            continue
        odd = "".join(ch for ch in dict.fromkeys(_plain(row["text"])) if ch not in seen)
        if not odd:
            continue
        row["text_suspect"] = odd
        best = max(lines, key=lambda ln: difflib.SequenceMatcher(None, row["text"], ln).ratio())
        row["mineru_text"] = best


def toc_verify(book_id: str, root: str = "") -> dict[str, Any]:
    """Check the entries of toc.print against the body pages and MinerU. Read only."""
    book = book_dir(book_id, root)
    doc = json.loads((book / "toc.print").read_text(encoding="utf-8"))
    rows = [dict(e, text=e["label"]) for e in doc.get("entries") or []]
    collected = collect_pages(book)
    offset = verify(book, rows, collected, doc.get("toc_pages") or [])
    check_text(rows, collected)
    counts = collections.Counter(r["verified"] for r in rows)
    odd = [{"label": r["label"], "text_suspect": r["text_suspect"], "mineru_text": r["mineru_text"]}
           for r in rows if r.get("text_suspect")]
    return {"page_offset": offset, "verified": dict(counts), "text_suspect": odd}


# ---------------------------------------------------------------- 6 golden


def golden_dir() -> Path:
    return _home() / "eval" / "printed_toc"


def _gold_key(label: str) -> str:
    return _plain(label).lower()


def compare(gold: list[dict[str, Any]], got: list[dict[str, Any]]) -> dict[str, Any]:
    a = [_gold_key(e["label"]) for e in gold]
    b = [_gold_key(e["label"]) for e in got]
    out: dict[str, list] = {"missing": [], "extra": [], "label": [], "level": [], "page": []}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            pairs = zip(gold[i1:i2], got[j1:j2])
        elif tag == "replace" and i2 - i1 == j2 - j1:
            pairs = zip(gold[i1:i2], got[j1:j2])
            for g, e in zip(gold[i1:i2], got[j1:j2]):
                out["label"].append([g["label"], e["label"]])
        else:
            out["missing"] += [g["label"] for g in gold[i1:i2]]
            out["extra"] += [e["label"] for e in got[j1:j2]]
            continue
        for g, e in pairs:
            if g["level"] != e["level"]:
                out["level"].append([g["label"], g["level"], e["level"]])
            if g.get("print_page") != e.get("print_page"):
                out["page"].append([g["label"], g.get("print_page"), e.get("print_page")])
    out["pass"] = not any(out[k] for k in ("missing", "extra", "label", "level", "page"))
    return out


def toc_golden(book_id: str = "", root: str = "") -> dict[str, Any]:
    """Compare toc.print with eval/printed_toc/<book>.json (all frozen books when empty). Read only."""
    files = sorted(golden_dir().glob("*.json"))
    if book_id:
        files = [f for f in files if f.stem.startswith(book_id.strip())]
    report: dict[str, Any] = {}
    for path in files:
        gold = json.loads(path.read_text(encoding="utf-8"))
        try:
            book = book_dir(path.stem, root)
            got = json.loads((book / "toc.print").read_text(encoding="utf-8")).get("entries") or []
        except FileNotFoundError:
            report[path.stem] = {"pass": False, "error": "no toc.print"}
            continue
        report[path.stem] = compare(gold.get("entries") or [], got)
    return {"books": len(report), "passed": sum(1 for r in report.values() if r["pass"]), "report": report}
