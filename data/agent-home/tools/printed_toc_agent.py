"""Small agent for a printed table of contents, any textbook.

Planner is Python so most books never call a model. The local 35B only
sees a page when that page is a picture. Shared words live in
``configs/toc_lexicon.yaml``. Output is ``textbooks/<id>/toc.print``.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

import toc_lexicon

_SKIP_TYPES = {"header", "footer", "page_number", "ocr_text"}
_TOC_WORD = re.compile(r"^(目录|目\s*录|contents)$", re.I)
_PAREN_PAGE = re.compile(r"^(?P<title>.+?)\s*\(p\.\s*(?P<page>\d{1,4})\)\s*$", re.I)
_PUBLISHER = re.compile(r"出版社|出版集团")
_FRONT_PAGES = 16
_IMAGE_TEXT_MAX = 80
_LEXICON_MIN = 8

_VISION_PROMPT = """这是教材的一页，可能是双栏目录。请先左栏、后右栏，从上到下逐条抽出。
必须包含每个大标题（第N章、第N单元、1 Decisions 这种），以及每一条分项目（1.1、第N节、A/B/C/D/E、Self-assessment、内容提要、复习题、附录里的词表）。
页码只取该行末尾或括号里的数字，写成 print_page；没有就 null。
不要写大标题下面的灰色说明句。
level 只能是 0 或 1：章、单元、附录是 0，其余是 1。
只输出一个 JSON，不要解释：
{"entries":[{"label":"1 Decisions","level":0,"print_page":null}]}
"""


def _vision_url() -> str:
    return os.environ.get("TOC_VISION_URL", "http://127.0.0.1:8081/v1/chat/completions")


def _model_id() -> str:
    return os.environ.get(
        "TOC_VISION_MODEL",
        "/home/mylearningbuddy/opt/models/gguf/Qwen3.6-35B-A3B-MXFP4_MOE.gguf",
    )


def _book_dir(book_id: str, root: str = "") -> Path:
    base = Path(root) if root else Path(__file__).resolve().parents[1] / "textbooks"
    sid = (book_id or "").strip()
    direct = base / sid
    if direct.is_dir():
        return direct
    hits = [p for p in base.iterdir() if p.is_dir() and p.name.startswith(sid)]
    if not hits:
        raise FileNotFoundError(sid)
    return hits[0]


def _load_model(book: Path) -> list:
    hits = sorted((book / "mineru").glob("*_model.json"))
    if not hits:
        return []
    data = json.loads(hits[0].read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _page_text_and_images(page: list) -> tuple[list[str], int]:
    lines: list[str] = []
    images = 0
    for item in page:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        if kind == "image":
            images += 1
            continue
        if kind in _SKIP_TYPES or "header" in kind or "footer" in kind:
            continue
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        lines.extend(ln.strip() for ln in text.splitlines() if ln.strip())
    return lines, images


def scan_toc_pages(book: Path) -> dict[str, Any]:
    """Tool: find likely printed-catalog pages in the first 16 pages. No model."""
    pages = _load_model(book)
    index_pages: list[dict[str, Any]] = []
    image_pages: list[int] = []
    for idx, page in enumerate(pages[:_FRONT_PAGES]):
        if not isinstance(page, list):
            continue
        lines, images = _page_text_and_images(page)
        index_lines = []
        for item in page:
            if isinstance(item, dict) and item.get("type") == "index":
                index_lines.extend(
                    ln.strip() for ln in str(item.get("content") or "").splitlines() if ln.strip()
                )
        if len(index_lines) >= 3:
            index_pages.append({"pdf_page": idx + 1, "lines": index_lines})
        elif images and len("".join(lines)) < _IMAGE_TEXT_MAX and idx > 0:
            image_pages.append(idx + 1)
    runs = _longest_run(image_pages)
    return {"index_pages": index_pages, "image_pages": runs}


def _longest_run(pages: list[int]) -> list[int]:
    if not pages:
        return []
    best: list[int] = []
    cur = [pages[0]]
    for page in pages[1:]:
        if page == cur[-1] + 1:
            cur.append(page)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [page]
    if len(cur) > len(best):
        best = cur
    return best[:3]


def _peel(line: str) -> tuple[str, int | None]:
    matched = _PAREN_PAGE.match(line.strip())
    if matched:
        return matched.group("title").strip(), int(matched.group("page"))
    label, page = toc_lexicon.strip_page(line)
    if page is None:
        return (label or line).strip(), None
    return label.strip(), int(page)


def _level_of(label: str) -> int | None:
    if not label or _TOC_WORD.match(label) or _PUBLISHER.search(label):
        return None
    hit = toc_lexicon.match_line(label)
    if hit is not None:
        return 0 if hit.level <= 0 else 1
    return None


def parse_lines(lines: list[str], pdf_page: int) -> list[dict[str, Any]]:
    """Tool: turn catalog lines into entries using the shared word list."""
    out: list[dict[str, Any]] = []
    for line in lines:
        label, print_page = _peel(line)
        level = _level_of(label)
        if level is None:
            continue
        out.append({
            "label": label,
            "level": level,
            "is_chapter": level == 0,
            "print_page": print_page,
            "pdf_page": pdf_page,
            "source": "lexicon",
        })
    return out


def _vision_read(image_path: Path, pdf_page: int) -> tuple[list[dict[str, Any]], float]:
    """Tool: ask the local 35B to read one catalog image."""
    import base64

    raw = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = {
        "model": _model_id(),
        "temperature": 0,
        "max_tokens": 1200,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{raw}"}},
            ],
        }],
    }
    started = time.perf_counter()
    req = urllib.request.Request(
        _vision_url(),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    elapsed = round(time.perf_counter() - started, 3)
    text = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    start, end = text.find("{"), text.rfind("}")
    rows = []
    if start >= 0 and end > start:
        try:
            rows = json.loads(text[start:end + 1]).get("entries") or []
        except json.JSONDecodeError:
            rows = []
    kept = harness_vision(rows, pdf_page)
    return kept, elapsed


def harness_vision(rows: list, pdf_page: int) -> list[dict[str, Any]]:
    """Drop invented shapes. Level must be 0 or 1. A page number must be a small integer."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        if not label or _TOC_WORD.match(label) or _PUBLISHER.search(label):
            continue
        try:
            level = int(row.get("level"))
        except (TypeError, ValueError):
            continue
        if level not in (0, 1):
            continue
        page = row.get("print_page")
        if page is not None and page != "":
            try:
                page = int(page)
            except (TypeError, ValueError):
                page = None
            if page is not None and not (1 <= page <= 400):
                page = None
        else:
            page = None
        out.append({
            "label": label,
            "level": level,
            "is_chapter": level == 0,
            "print_page": page,
            "pdf_page": pdf_page,
            "source": "vision",
        })
    return out


def printed_toc_agent(book_id: str, root: str = "") -> dict[str, Any]:
    """Read one book's printed catalog and write toc.print.

    Text catalogs stay on the shared word list. Pictures go to the local 35B.
    """
    started = time.perf_counter()
    book = _book_dir(book_id, root)
    scanned = scan_toc_pages(book)
    entries: list[dict[str, Any]] = []
    route = "none"
    vision_s: list[float] = []
    if scanned["index_pages"]:
        for page in scanned["index_pages"]:
            entries.extend(parse_lines(page["lines"], page["pdf_page"]))
        if len(entries) >= _LEXICON_MIN:
            route = "lexicon"
    if route == "none" and scanned["image_pages"]:
        route = "vision"
        for pdf_page in scanned["image_pages"]:
            image = book / "images" / f"p{pdf_page:04d}.jpg"
            if not image.is_file():
                continue
            got, elapsed = _vision_read(image, pdf_page)
            vision_s.append(elapsed)
            entries.extend(got)
    doc = {
        "book_id": book.name,
        "route": route,
        "toc_pages": [p["pdf_page"] for p in scanned["index_pages"]] or scanned["image_pages"],
        "entries": entries,
        "timing_s": {
            "total": round(time.perf_counter() - started, 3),
            "vision": vision_s,
        },
    }
    out = book / "toc.print"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    doc["file"] = str(out)
    doc["entry_count"] = len(entries)
    return doc
