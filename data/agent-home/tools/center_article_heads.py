from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_SUP = re.compile(r"\$\^\{[^}]+\}\$")
_HEAD = re.compile(r"^#{1,6}\s+")
_HTML = re.compile(
    r'<p class="kb-article-(title|author|date)"[^>]*>(.*?)</p>', re.S
)
_NOTE = re.compile(
    r"^[\u2460-\u2473\u3251-\u325f\u32b1-\u32bf]\d*\s*(?:〔|选自|节选)"
)
_CIRCLED = re.compile(r"^[\u2460-\u2473\u3251-\u325f\u32b1-\u32bf]")
_SKIP = frozenset({"学习提示", "阅读指导", "单元学习任务", "古诗词诵读"})
_TOC = "kb-page-toc"


def _book_dir(book_id: str, root: str) -> Path:
    base = Path(root).resolve() if root else Path.cwd()
    if (base / "textbooks").is_dir():
        return base / "textbooks" / book_id
    return base / book_id


def _strip_markup(text: str) -> str:
    out = (text or "").strip()
    match = _HTML.match(out)
    if match:
        out = match.group(2).strip()
    out = _HEAD.sub("", out)
    out = _SUP.sub("", out)
    out = out.replace("*", "")
    return re.sub(r"\s+", "", out)


def _inner(text: str) -> str:
    out = (text or "").strip()
    match = _HTML.match(out)
    if match:
        return match.group(2).strip()
    return _HEAD.sub("", out)


def _parse_label(label: str) -> tuple[str, str] | None:
    text = (label or "").strip().lstrip("*")
    if " / " not in text:
        return None
    title, author = text.rsplit(" / ", 1)
    return title.strip().lstrip("*"), author.strip()


def _title_matches(line: str, title: str) -> bool:
    line_n = _strip_markup(line)
    title_n = _strip_markup(title)
    short = re.sub(r"[（(][^）)]+[）)]$", "", title_n)
    if not line_n or not title_n:
        return False
    if line_n == title_n or line_n == short:
        return True
    if short and (line_n.startswith(short) or short.startswith(line_n)):
        return True
    return title_n.startswith(line_n) or line_n.startswith(title_n)


def _author_matches(line: str, author: str) -> bool:
    line_n = _strip_markup(line)
    author_n = _strip_markup(author)
    if not line_n or not author_n:
        return False
    if line_n == author_n:
        return True
    same_len = 2 <= len(line_n) <= 6 and 2 <= len(author_n) <= 6
    return (
        same_len
        and line_n[0] == author_n[0]
        and abs(len(line_n) - len(author_n)) <= 1
    )


def _is_date(line: str) -> bool:
    return bool(re.match(r"^[（(].*年.*[)）]$", _strip_markup(line)))


def _is_subtitle(line: str) -> bool:
    return _inner(line).startswith(("——", "—"))


def _is_note(line: str) -> bool:
    return bool(_NOTE.match(line.strip()))


def _unwrap_html(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        kind, inner = match.group(1), match.group(2).strip()
        if kind == "title":
            return f"## {inner}"
        return inner

    out = _HTML.sub(repl, text)
    return re.sub(r"\n{3,}", "\n\n", out)


def _heading(text: str) -> str:
    return "## " + _inner(text)


def _plain(text: str) -> str:
    return _inner(text)


def _collect_head(
    lines: list[str], author_idx: int, title: str
) -> tuple[list[int], bool]:
    found: list[int] = []
    saw_title = False
    index = author_idx - 1
    while index >= 0:
        raw = lines[index].strip()
        if not raw:
            index -= 1
            continue
        if raw.startswith(("![", "<nav", "---", "<p class=")):
            break
        if _is_note(raw) or _strip_markup(raw) in _SKIP:
            break
        if len(_strip_markup(raw)) > 60 and not raw.startswith("#"):
            break
        if _title_matches(raw, title) or _is_date(raw) or _is_subtitle(raw):
            found.append(index)
            if _title_matches(raw, title):
                saw_title = True
                break
            index -= 1
            continue
        if not saw_title and len(_strip_markup(raw)) <= 40 and not _CIRCLED.match(raw):
            found.append(index)
            saw_title = True
            break
        break
    found.reverse()
    return found, saw_title


def _block_for(lines: list[str], head: list[int], author_idx: int) -> list[str]:
    parts: list[str] = []
    for index in head:
        src = lines[index]
        if _is_date(src):
            parts.append(_plain(src))
        else:
            parts.append(_heading(src))
    parts.append(_plain(lines[author_idx]))
    out: list[str] = []
    for item in parts:
        if out:
            out.append("")
        out.append(item)
    return out


def _apply_page(
    text: str, pairs: list[tuple[str, str]]
) -> tuple[str, list[str]]:
    if _TOC in text:
        return text, []
    text = _unwrap_html(text)
    lines = text.splitlines()
    used: set[int] = set()
    reps: list[tuple[int, int, list[str], str]] = []
    for title, author in pairs:
        for index, line in enumerate(lines):
            if index in used:
                continue
            raw = line.strip()
            if not raw:
                continue
            if not _author_matches(raw, author):
                continue
            exact = _strip_markup(raw) == _strip_markup(author)
            head, saw = _collect_head(lines, index, title)
            if not saw:
                continue
            if not exact and not any(_title_matches(lines[k], title) for k in head):
                continue
            used.add(index)
            reps.append((head[0], index, _block_for(lines, head, index), title))
            break
    if not reps:
        return text, []
    reps.sort(key=lambda item: item[0], reverse=True)
    labels = [item[3] for item in reversed(reps)]
    for start, end, block, _title in reps:
        lines[start : end + 1] = block
    out = "\n".join(lines)
    if text.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out, labels


def _toc_pairs(book: Path) -> dict[int, list[tuple[str, str]]]:
    path = book / "toc.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return {}
    by_page: dict[int, list[tuple[str, str]]] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        parsed = _parse_label(str(item.get("label") or ""))
        if not parsed:
            continue
        page = item.get("pdf_page")
        if not page:
            continue
        try:
            num = int(page)
        except (TypeError, ValueError):
            continue
        by_page.setdefault(num, []).append(parsed)
    return by_page


def center_article_heads(
    book_id: str, apply: str = "fix", root: str = ""
) -> dict[str, Any]:
    """目录末级「篇名 / 作者」写成 ## 篇名加下一行作者，不写 HTML。"""
    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    mode = (apply or "fix").strip().lower()
    if mode not in {"scan", "fix"}:
        raise ValueError("apply must be scan or fix")

    book = _book_dir(sid, root)
    pages_dir = book / "pages"
    if not pages_dir.is_dir():
        raise FileNotFoundError(str(pages_dir))
    pairs = _toc_pairs(book)
    if not pairs:
        return {
            "book_id": sid,
            "apply": mode,
            "skipped": True,
            "reason": "no_title_author",
            "changed": 0,
        }

    hits: list[str] = []
    pending: list[tuple[Path, str]] = []
    for num, items in sorted(pairs.items()):
        path = pages_dir / f"{num:04d}.md"
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        updated, _labels = _apply_page(original, items)
        if updated == original:
            continue
        hits.append(path.name)
        pending.append((path, updated))

    result: dict[str, Any] = {
        "book_id": sid,
        "apply": mode,
        "changed": len(hits),
        "pages": hits[:40],
    }
    if not hits:
        result["skipped"] = True
        result["reason"] = "already_ok"
        return result
    if mode != "fix":
        return result
    written = 0
    for path, text in pending:
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        written += 1
    result["written"] = written
    return result
