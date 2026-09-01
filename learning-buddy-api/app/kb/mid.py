"""Official page markdown ↔ mid-layer fences, and skin-HTML strip for migrate."""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path
from typing import Any

from app.kb.prose import polish_page_prose, unhash_lines
from app.kb.toc import looks_like_toc_blob

_FENCE = re.compile(r"::: (kb-[\w-]+)\r?\n([\s\S]*?)\r?\n:::")
_LIST_ITEM = re.compile(r'<p class="kb-list-item">([\s\S]*?)</p>', re.I)
_SOL_TAIL = re.compile(r'<p class="kb-solution-tail">([\s\S]*?)</p>', re.I)
_SOL = re.compile(r'<p class="kb-solution">[\s\S]*?</p>', re.I)
_LEAD = re.compile(
    r'<p class="kb-lead">[\s\S]*?<span class="kb-lead-label">([\s\S]*?)</span>([\s\S]*?)</p>',
    re.I,
)
_TOC_HEAD = re.compile(
    r'<(?:div|p|h[1-6]|span) class="kb-page-toc-heading">([\s\S]*?)</(?:div|p|h[1-6]|span)>',
    re.I,
)
_TOC_NAV = re.compile(r'<nav class="kb-page-toc"[\s\S]*?</nav>', re.I)
_TOC_LI = re.compile(r"<li\b[^>]*>([\s\S]*?)</li>", re.I)
_TOC_TITLE = re.compile(r'kb-page-toc-title">([\s\S]*?)<', re.I)
_TOC_PAGE = re.compile(r'kb-page-toc-page">([\s\S]*?)<', re.I)
_CENTER_DIV = re.compile(r'<div class="kb-center">\s*([\s\S]*?)\s*</div>', re.I)
_TABLE_WRAP = re.compile(r'<div class="kb-table-wrap">([\s\S]*?)</div>', re.I)
_TAGS = re.compile(r"<[^>]+>")
_MARKER = re.compile(r"(?<![\w.\-])[（(]\s*([0-9０-９]+)\s*[）)]")
_LEAD_LINE = re.compile(r"^((?:[\u4e00-\u9fff]{1,6})?定理|定义|证明|探究与实践)(?:\s+|$)")
_SOLUTION = re.compile(r"^解(?:[\s\u3000]+|(?=[（(]))(.*)$")
_FULLWIDTH_DIGIT = str.maketrans("０１２３４５６７８９", "0123456789")
_IMG = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)(?:\s*\n+\s*\*([^*\n]+)\*)?")
_TABLE = re.compile(r"<table\b[\s\S]*?</table>", re.I)


def has_kb_fences(md: str) -> bool:
    return bool(re.search(r"::: kb-[\w-]+", md or ""))


def looks_like_skin(md: str) -> bool:
    text = md or ""
    return 'class="kb-' in text or "<nav class=\"kb-page-toc\"" in text


def _plain(html: str) -> str:
    return unescape(_TAGS.sub("", html or "")).strip()


def fence(name: str, body: str) -> str:
    inner = (body or "").rstrip()
    return f"::: {name}\n{inner}\n:::"


def strip_skin(md: str) -> str:
    text = md or ""

    def _list(match: re.Match[str]) -> str:
        return _plain(match.group(1))

    text = _LIST_ITEM.sub(_list, text)
    text = _SOL_TAIL.sub(_list, text)
    text = _SOL.sub("解", text)

    def _lead(match: re.Match[str]) -> str:
        label = _plain(match.group(1))
        rest = _plain(match.group(2))
        return f"{label} {rest}".strip()

    text = _LEAD.sub(_lead, text)
    text = _TOC_HEAD.sub(lambda m: _plain(m.group(1)) + "\n", text)

    def _nav(match: re.Match[str]) -> str:
        rows: list[str] = []
        for li in _TOC_LI.finditer(match.group(0)):
            title_m = _TOC_TITLE.search(li.group(1))
            page_m = _TOC_PAGE.search(li.group(1))
            label = _plain(title_m.group(1)) if title_m else ""
            num = _plain(page_m.group(1)) if page_m else ""
            if label:
                rows.append(f"{label} {num}".strip() if num else label)
        return "\n".join(rows)

    text = _TOC_NAV.sub(lambda m: "\n" + _nav(m), text)
    text = _CENTER_DIV.sub(lambda m: m.group(1).strip(), text)
    text = _TABLE_WRAP.sub(lambda m: m.group(1).strip(), text)
    text = _FENCE.sub(lambda m: m.group(2).strip(), text)
    text = re.sub(r"^(目录|目 录|CONTENTS|Contents)(?=\S)", r"\1\n", text, flags=re.M)
    return text


def _unify_marker(text: str) -> str:
    def _mark(match: re.Match[str]) -> str:
        return f"（{match.group(1).translate(_FULLWIDTH_DIGIT)}）"

    return re.sub(r"^[（(]\s*([0-9０-９]+)\s*[）)]\s*", _mark, text)


def _has_packed(text: str) -> bool:
    matches = list(_MARKER.finditer(text))
    return any(not text[a.end() : b.start()].strip() for a, b in zip(matches, matches[1:]))


def _split_numbered(text: str) -> list[str] | None:
    matches = list(_MARKER.finditer(text))
    if len(matches) < 2 or _has_packed(text):
        return None
    chunks = [
        text[m.start() : (matches[i + 1].start() if i + 1 < len(matches) else len(text))].strip()
        for i, m in enumerate(matches)
    ]
    rich = [c for c in chunks if len(_MARKER.sub("", c).strip()) >= 4]
    if len(rich) < 2:
        return None
    prefix = text[: matches[0].start()].strip()
    if prefix and prefix != "解":
        return [prefix, *chunks]
    return chunks


def _tag_lists_and_leads(md: str) -> str:
    out: list[str] = []
    for raw in (md or "").split("\n"):
        trimmed = raw.strip()
        if not trimmed or trimmed.startswith(("<", "!", "#", ":::")):
            out.append(raw)
            continue
        body = trimmed
        solution = False
        sol = _SOLUTION.match(body)
        if sol and not body.startswith("解题"):
            solution = True
            body = sol.group(1).strip()
        chunks = _split_numbered(body)
        if solution:
            out.append(fence("kb-solution", "解"))
        if chunks:
            out.extend(fence("kb-list-item", _unify_marker(chunk)) for chunk in chunks)
            continue
        if re.match(r"^[（(]\s*[0-9０-９]+\s*[）)]", body) and not _has_packed(body):
            out.append(fence("kb-list-item", _unify_marker(body)))
            continue
        if re.match(r"^\d+\.\s+[\u4e00-\u9fff]", body) and not re.match(r"^\d+\.\d+", body):
            out.append(fence("kb-list-item", body))
            continue
        if solution and body:
            out.append(fence("kb-solution-tail", body))
            continue
        if _LEAD_LINE.match(trimmed):
            out.append(fence("kb-lead", trimmed))
            continue
        out.append(raw)
    return "\n".join(out)


def _tag_tables(md: str) -> str:
    raw = md or ""
    if "::: kb-table-wrap" in raw:
        return raw

    def _wrap(match: re.Match[str]) -> str:
        return fence("kb-table-wrap", match.group(0).strip())

    return _TABLE.sub(_wrap, raw)


def _tag_toc(md: str) -> str:
    raw = md or ""
    if "::: kb-page-toc" in raw or 'class="kb-page-toc"' in raw:
        return raw
    if not looks_like_toc_blob(raw):
        return raw
    return fence("kb-page-toc", raw.strip())


def _tag_centers(md: str, center_texts: list[str] | None) -> str:
    text = md or ""
    wanted = {t.strip() for t in (center_texts or []) if t and t.strip()}

    def _img(match: re.Match[str]) -> str:
        return fence("kb-center", match.group(0).strip())

    text = _IMG.sub(_img, text)
    text = re.sub(
        r"\$\$([\s\S]+?)\$\$",
        lambda m: fence("kb-center", m.group(0).strip()),
        text,
    )
    if not wanted:
        return text
    lines: list[str] = []
    for line in text.split("\n"):
        t = line.strip()
        if not t or t.startswith((":::", "<", "!")):
            lines.append(line)
            continue
        if t in wanted or any(w in t and len(w) >= 4 for w in wanted):
            lines.append(fence("kb-center", t))
        else:
            lines.append(line)
    return "\n".join(lines)


def tag_source(md: str, *, center_texts: list[str] | None = None) -> str:
    cleaned = polish_page_prose(strip_skin(md or ""))
    plain = unhash_lines(cleaned)
    if looks_like_toc_blob(plain):
        return _tag_toc(plain)
    listed = _tag_lists_and_leads(cleaned)
    tables = _tag_tables(listed)
    return _tag_centers(tables, center_texts)


def block_is_centered(bbox: object, *, page_width: float = 1000.0) -> bool:
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return False
    try:
        x0 = float(bbox[0])
        x1 = float(bbox[2])
    except (TypeError, ValueError):
        return False
    width_scale = page_width
    if max(abs(x0), abs(x1)) <= 1.5:
        x0 *= 1000.0
        x1 *= 1000.0
        width_scale = 1000.0
    width = x1 - x0
    if width <= 0 or width >= 0.55 * width_scale:
        return False
    left = x0
    right = width_scale - x1
    return abs(left - right) <= 0.18 * width_scale


def centered_texts_from_blocks(blocks: list[dict[str, Any]]) -> list[str]:
    from app.kb.markdown import _plain_text

    out: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        if not block_is_centered(block.get("bbox")):
            continue
        text = _plain_text(block).strip()
        if text:
            out.append(text)
    return out


def write_mid(book_id: str, page_no: int, tagged: str) -> Path:
    from app.kb.workspace_store import mid_path

    path = mid_path(book_id, page_no)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tagged or "", encoding="utf-8")
    return path


def read_mid(book_id: str, page_no: int) -> str | None:
    from app.kb.workspace_store import mid_path

    path = mid_path(book_id, page_no)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def migrate_page(book_id: str, page_no: int, *, center_texts: list[str] | None = None) -> dict[str, Any]:
    from app.kb.workspace_store import page_path, write_page

    src_path = page_path(book_id, page_no)
    if not src_path.is_file():
        return {"page_no": page_no, "skipped": True}
    raw = src_path.read_text(encoding="utf-8")
    source = polish_page_prose(strip_skin(raw))
    write_page(book_id, page_no, source)
    tagged = tag_source(source, center_texts=center_texts)
    write_mid(book_id, page_no, tagged)
    names = sorted({m.group(1) for m in _FENCE.finditer(tagged)})
    return {
        "page_no": page_no,
        "stripped": looks_like_skin(raw) or raw != source,
        "fences": names,
        "source_bytes": len(source.encode("utf-8")),
        "mid_bytes": len(tagged.encode("utf-8")),
    }


def migrate_book(book_id: str) -> dict[str, Any]:
    from app.kb.markdown import group_blocks_by_page
    from app.kb.mineru_client import _load_blocks
    from app.kb.workspace_store import book_dir, mineru_dir, page_path

    folder = book_dir(book_id) / "pages"
    pages = sorted(folder.glob("[0-9][0-9][0-9][0-9].md")) if folder.is_dir() else []
    by_page: dict[int, list[str]] = {}
    mineru = mineru_dir(book_id)
    if mineru.is_dir():
        try:
            blocks = _load_blocks(mineru)
            grouped = group_blocks_by_page(blocks)
            by_page = {n: centered_texts_from_blocks(items) for n, items in grouped.items()}
        except Exception:
            by_page = {}
    rows = []
    for path in pages:
        page_no = int(path.stem)
        rows.append(migrate_page(book_id, page_no, center_texts=by_page.get(page_no)))
    return {"book_id": book_id, "pages": rows, "page_count": len(rows)}


def migrate_all_books() -> dict[str, Any]:
    from app.kb.workspace_store import list_metas

    books = []
    for meta in list_metas():
        book_id = str(meta.get("id") or "")
        if not book_id:
            continue
        books.append(migrate_book(book_id))
    return {"ok": True, "books": books, "book_count": len(books)}
