from __future__ import annotations

import json
from pathlib import Path


def _snippet(text: str, needle: str) -> str:
    idx = text.find(needle)
    return text[max(0, idx) : max(0, idx) + 160] if idx >= 0 else text[:160]


def _search_ir_book(book: Path, needle: str, limit: int) -> list[dict[str, str]]:
    pages = book / "ir" / "pages"
    if not pages.is_dir():
        return []
    hits: list[dict[str, str]] = []
    for path in sorted(pages.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        page_no = int(data.get("page") or 0)
        for blk in data.get("blocks") or []:
            if not isinstance(blk, dict):
                continue
            text = str(blk.get("text") or "")
            if needle not in text:
                continue
            bid = str(blk.get("id") or "")
            hits.append({
                "path": f"{book.name}:{page_no}#{bid}",
                "snippet": _snippet(text, needle),
            })
            if len(hits) >= limit:
                return hits
    return hits


def _iter_ir_books(base: Path) -> list[Path]:
    if (base / "ir" / "pages").is_dir():
        return [base]
    textbooks = base / "textbooks" if (base / "textbooks").is_dir() else base
    if not textbooks.is_dir():
        return []
    return [
        path for path in sorted(textbooks.iterdir())
        if path.is_dir() and (path / "ir" / "pages").is_dir()
    ]


def _search_md(base: Path, needle: str, limit: int) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for path in sorted(base.rglob("*.md")):
        if path.name == "SKILL.md":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if needle not in text and needle.lower() not in path.as_posix().lower():
            continue
        hits.append({
            "path": str(path),
            "snippet": _snippet(text, needle) if needle in text else path.stem,
        })
        if len(hits) >= limit:
            break
    return hits


def search_md(root: str, query: str, limit: int = 20) -> list[dict[str, str]]:
    """默认搜中间表示块文本；无中间表示或 root 是 pages/ 时扫 Markdown。"""
    needle = (query or "").strip()
    if not needle:
        return []
    base = Path(root)
    if not base.is_dir():
        return []
    if base.name == "pages":
        return _search_md(base, needle, limit)
    hits: list[dict[str, str]] = []
    for book in _iter_ir_books(base):
        room = limit - len(hits)
        if room <= 0:
            break
        hits.extend(_search_ir_book(book, needle, room))
    if hits:
        return hits
    return _search_md(base, needle, limit)
