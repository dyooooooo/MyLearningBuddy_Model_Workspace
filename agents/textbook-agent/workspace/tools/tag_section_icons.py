from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_COLUMNS = (
    "学习提示",
    "单元学习任务",
    "思考与交流",
    "积累与运用",
    "阅读指导",
)
_COL_ALT = "|".join(re.escape(name) for name in _COLUMNS)
_HEAD = r"#{1,6}[ \t]+(?:" + _COL_ALT + r")[ \t]*"
_CENTERED = re.compile(
    r"::: kb-center\r?\n"
    r"(!\[[^\]]*\]\([^)]+\))\r?\n"
    r":::\r?\n+"
    r"(" + _HEAD + r")"
)
_BARE = re.compile(
    r"(!\[[^\]]*\]\([^)]+\))\r?\n+"
    r"(" + _HEAD + r")"
)
_KEEP = re.compile(r"::: kb-section-icon\r?\n[\s\S]*?\r?\n:::")
_BROKEN = re.compile(
    r"::: kb-section-icon\r?\n::: kb-section-icon\r?\n"
    r"(!\[[^\]]*\]\([^)]+\))\r?\n"
    r"(" + _HEAD + r")\r?\n::::::"
)
_TIGHT = re.compile(
    r"::: kb-section-icon\r?\n"
    r"(!\[[^\]]*\]\([^)]+\))\r?\n"
    r"(" + _HEAD + r")\r?\n"
    r":::"
)
_GLUED = re.compile(
    r"(::: kb-section-icon\r?\n[\s\S]*?\r?\n):::([^\n])"
)
_ONE_NL = re.compile(
    r"(::: kb-section-icon\r?\n[\s\S]*?\r?\n):::\r?\n(?!\r?\n)"
)


def _book_dir(book_id: str, root: str) -> Path:
    base = Path(root).resolve() if root else Path.cwd()
    if (base / "textbooks").is_dir():
        return base / "textbooks" / book_id
    return base / book_id


def _read_meta(book: Path) -> dict[str, Any]:
    path = book / "meta.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def is_chinese_textbook(meta: dict[str, Any] | None) -> bool:
    if not isinstance(meta, dict):
        return False
    subject = str(meta.get("subject") or "").strip().lower()
    category = str(meta.get("category") or "").strip().lower()
    return subject == "chinese" and category in {"textbook", "教材"}


def _wrap(image: str, heading: str) -> str:
    return (
        "::: kb-section-icon\n"
        f"{image.strip()}\n\n"
        f"{heading.strip()}\n"
        ":::"
    )


def _space_around(text: str) -> str:
    out = _TIGHT.sub(lambda m: _wrap(m.group(1), m.group(2)), text)
    out = _GLUED.sub(r"\1:::\n\n\2", out)
    out = _ONE_NL.sub(r"\1:::\n\n", out)
    out = re.sub(r"(?<!\n\n)(::: kb-section-icon\n)", r"\n\1", out)
    return out


def _map_outside(text: str, transform) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _KEEP.finditer(text):
        if match.start() > cursor:
            parts.append(transform(text[cursor : match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    if cursor < len(text):
        parts.append(transform(text[cursor:]))
    return "".join(parts)


def apply_section_icons(text: str) -> str:
    if not text or "::: kb-page-toc" in text:
        return text
    out = _BROKEN.sub(lambda m: _wrap(m.group(1), m.group(2)), text)
    out = _map_outside(out, lambda chunk: _CENTERED.sub(lambda m: _wrap(m.group(1), m.group(2)), chunk))
    out = _map_outside(out, lambda chunk: _BARE.sub(lambda m: _wrap(m.group(1), m.group(2)), chunk))
    out = _space_around(out)
    if text.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out


def tag_section_icons(
    book_id: str, apply: str = "fix", root: str = ""
) -> dict[str, Any]:
    """把栏目标题前的图标收成 kb-section-icon，只写中间文件。"""
    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    mode = (apply or "fix").strip().lower()
    if mode not in {"scan", "fix"}:
        raise ValueError("apply must be scan or fix")

    book = _book_dir(sid, root)
    meta = _read_meta(book)
    if not is_chinese_textbook(meta):
        return {
            "book_id": sid,
            "apply": mode,
            "skipped": True,
            "reason": "not chinese textbook",
            "subject": str(meta.get("subject") or ""),
            "category": str(meta.get("category") or ""),
        }

    from app import library

    pages_dir = book / "pages"
    mid_dir = book / "mid"
    stems: list[str] = []
    if pages_dir.is_dir():
        stems = [p.stem for p in sorted(pages_dir.glob("*.md")) if p.name[:4].isdigit()]
    elif mid_dir.is_dir():
        stems = [p.stem for p in sorted(mid_dir.glob("*.md")) if p.name[:4].isdigit()]
    if not stems:
        raise FileNotFoundError(str(pages_dir if pages_dir.is_dir() else mid_dir))

    hits: list[str] = []
    pending: list[tuple[int, str]] = []
    pages = 0
    for stem in stems:
        page_no = int(stem)
        pages += 1
        existing = library.get_page_mid(sid, page_no) or ""
        if not existing:
            mid_path = mid_dir / f"{page_no:04d}.md"
            if mid_path.is_file():
                existing = mid_path.read_text(encoding="utf-8")
        if not existing:
            continue
        updated = apply_section_icons(existing)
        if updated == existing:
            continue
        hits.append(f"{page_no:04d}.md")
        pending.append((page_no, updated))

    result: dict[str, Any] = {
        "book_id": sid,
        "apply": mode,
        "pages": pages,
        "changed": len(hits),
        "files": hits[:40],
    }
    if not hits:
        result["skipped"] = True
        result["reason"] = "already_ok"
        return result
    if mode != "fix":
        return result
    written = 0
    for page_no, text in pending:
        library.write_mid(sid, page_no, text)
        written += 1
    result["written"] = written
    return result
