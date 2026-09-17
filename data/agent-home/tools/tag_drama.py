from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_SPOKEN_SPACE = re.compile(
    r"^[\u4e00-\u9fffA-Za-z·．.]{2,8}[ \t\u3000]+(?:（[^）]*）)?[ \t\u3000]*.+"
)
_SPOKEN_PAREN = re.compile(
    r"^[\u4e00-\u9fffA-Za-z·．.]{2,8}（[^）]*）"
)
_COLOPHON = re.compile(r"ISBN|版权所有|书号")
_ARIA = re.compile(r"^【[^】]+】")
_YUAN = re.compile(r"^（.{0,40}(?:云|唱|科)")
_HEAD = re.compile(r"^#{1,6}\s+")
_STOP_HEAD = re.compile(
    r"^#{1,6}\s+(?:学习提示|单元学习任务|阅读指导|思考与交流)"
)
_TABLE = re.compile(r"<table\b[\s\S]*?</table>", re.I)
_ROW = re.compile(r"<tr\b[^>]*>([\s\S]*?)</tr>", re.I)
_CELL = re.compile(r"<t[dh]\b[^>]*>([\s\S]*?)</t[dh]>", re.I)
_TAG = re.compile(r"<[^>]+>")
_FENCE = re.compile(r"^::: ")


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


def _plain(html: str) -> str:
    text = _TAG.sub("", html or "")
    return (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .strip()
    )


def _is_speech(line: str) -> bool:
    t = (line or "").strip()
    if not t or t.startswith(":::"):
        return False
    if _ARIA.match(t) or _YUAN.match(t):
        return True
    if _SPOKEN_PAREN.match(t):
        return True
    if _SPOKEN_SPACE.match(t):
        return True
    return False


def _is_stage(line: str) -> bool:
    t = (line or "").strip()
    if not t or _is_speech(t) or _HEAD.match(t) or t.startswith("!"):
        return False
    if t.startswith(":::") or t == "---":
        return False
    return len(re.sub(r"\s+", "", t)) >= 16


def _looks_name(cell: str) -> bool:
    t = (cell or "").strip()
    if not t or len(t) > 8:
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z·]{1,8}", t))


def _table_to_lines(html: str) -> str | None:
    if re.search(r"<caption\b", html, re.I):
        return None
    rows: list[list[str]] = []
    for row in _ROW.findall(html):
        cells = [_plain(c) for c in _CELL.findall(row)]
        if cells:
            rows.append(cells)
    if len(rows) < 3:
        return None
    if any(len(r) != 2 for r in rows):
        return None
    names = sum(1 for r in rows if _looks_name(r[0]))
    if names < 3:
        return None
    out: list[str] = []
    for left, right in rows:
        if left and right:
            out.append(f"{left} {right}")
        elif right:
            out.append(right)
    return "\n\n".join(out) if out else None


def _convert_tables(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        converted = _table_to_lines(match.group(0))
        return converted if converted else match.group(0)

    return _TABLE.sub(repl, text)


def _wrap_run(paras: list[str]) -> str:
    speeches = [p for p in paras if _is_speech(p)]
    if len(speeches) < 2:
        return "\n\n".join(paras)
    if len(speeches) < 3 and len(speeches) * 2 < len([p for p in paras if p.strip()]):
        return "\n\n".join(paras)
    body = paras[:]
    prefix = ""
    if body and _is_stage(body[0]) and not _is_speech(body[0]):
        prefix = f"::: kb-stage\n{body[0]}\n:::\n\n"
        body = body[1:]
    inner = "\n\n".join(body).strip()
    return f"{prefix}::: kb-drama\n{inner}\n:::"


def _copy_fence(lines: list[str], start: int) -> tuple[str, int]:
    chunk = [lines[start]]
    idx = start + 1
    while idx < len(lines):
        chunk.append(lines[idx])
        if lines[idx].strip() == ":::":
            return "\n".join(chunk), idx + 1
        idx += 1
    return "\n".join(chunk), idx


def apply_drama(text: str) -> str:
    if not text or "::: kb-page-toc" in text:
        return text
    src = _convert_tables(text.replace("\r\n", "\n").replace("\r", "\n"))
    if _COLOPHON.search(src.split("---", 1)[0]):
        return text
    if "::: kb-drama" in src and "<table" not in src:
        return src if src.endswith("\n") or not text.endswith("\n") else src + "\n"
    lines = src.split("\n")
    hr = next((i for i, ln in enumerate(lines) if ln.strip() == "---"), len(lines))
    head = lines[:hr]
    tail = lines[hr:]
    out: list[str] = []
    run: list[str] = []
    i = 0

    def flush() -> None:
        nonlocal run
        if not run:
            return
        chunk = _wrap_run([p for p in run if p is not None])
        out.append(chunk)
        run = []

    while i < len(head):
        raw = head[i]
        stripped = raw.strip()
        if stripped.startswith("::: "):
            flush()
            fence, nxt = _copy_fence(head, i)
            out.append(fence)
            i = nxt
            continue
        if _STOP_HEAD.match(stripped) or stripped.startswith("!"):
            flush()
            out.append(raw)
            i += 1
            continue
        if _HEAD.match(stripped):
            flush()
            out.append(raw)
            i += 1
            continue
        if not stripped:
            if run:
                i += 1
                continue
            out.append(raw)
            i += 1
            continue
        run.append(stripped)
        i += 1
    flush()
    body = "\n\n".join(chunk.strip("\n") for chunk in out if chunk.strip())
    rest = "\n".join(tail)
    if rest:
        updated = body + "\n\n" + rest
    else:
        updated = body + ("\n" if src.endswith("\n") else "")
    if text.endswith("\n") and not updated.endswith("\n"):
        updated += "\n"
    added = "::: kb-drama" in updated or "::: kb-stage" in updated
    table_gone = "<table" in src and "<table" not in updated
    if not added and not table_gone:
        return text
    return updated


def tag_drama(book_id: str, apply: str = "fix", root: str = "") -> dict[str, Any]:
    """把戏剧对白收成 kb-drama / kb-stage，只写中间文件。"""
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
        updated = apply_drama(existing)
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
