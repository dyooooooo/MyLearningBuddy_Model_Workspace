"""强制整页重判围栏（目录页围栏丢了时用）。

`page_sync.sync_page` 只在 `mid/NNNN.md` 不存在时做一次整页判定（`tag_mid`），之后
一律走 `paste_fences` 按块补围栏 —— 它永远不会重问「这一整页是不是目录页」。
所以已经导入过的书，如果当初 `::: kb-page-toc` 判错或漏了，不会自愈。

用法：

- `retag_page(book_id="…", scope="toc")` —— 重判 `toc.json` 里 `toc_pages` 列的页
- `retag_page(book_id="…", pages="6,7")` —— 重判指定页，支持 `6-9` 区间

只重写 `mid/`，不动官方 `pages/`。归属清单见 doc/toc-code-ownership.md。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_PAGE_TOKEN = re.compile(r"\d+(?:-\d+)?")


def _parse_pages(raw: str) -> list[int]:
    out: list[int] = []
    for token in _PAGE_TOKEN.findall(raw or ""):
        if "-" in token:
            a, b = token.split("-", 1)
            lo, hi = int(a), int(b)
            if lo > hi:
                lo, hi = hi, lo
            out.extend(range(lo, hi + 1))
        else:
            out.append(int(token))
    return sorted({n for n in out if n > 0})


def _roots(root: str) -> list[Path]:
    """显式 root 优先，其次 agent home，最后 cwd。

    工具被 `docker exec` 直接调时 cwd 是 `/app`，那下面没有 textbooks/，
    所以不能只靠 cwd。本工具反正已经依赖 `app.page_sync`，取 settings.home 不算新耦合。
    """
    out: list[Path] = []
    if (root or "").strip():
        out.append(Path(root))
    try:
        from app.config import settings

        out.append(Path(settings.home))
    except Exception:
        pass
    out.append(Path.cwd())
    seen: list[Path] = []
    for path in out:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.append(resolved)
    return seen


def _book_dir(base: Path, book_id: str) -> Path:
    book = base / "textbooks" / book_id if (base / "textbooks").is_dir() else base / book_id
    if not book.is_dir():
        raise FileNotFoundError(str(book))
    return book


def _find_book(book_id: str, root: str) -> Path:
    tried: list[str] = []
    for base in _roots(root):
        try:
            return _book_dir(base, book_id)
        except FileNotFoundError as exc:
            tried.append(str(exc))
    raise FileNotFoundError("; ".join(tried))


def _toc_pages(book: Path) -> list[int]:
    pages: list[int] = []
    for name in ("toc.json", "mineru_toc.json", "toc_force.json"):
        path = book / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for raw in data.get("toc_pages") or []:
            try:
                page = int(raw)
            except (TypeError, ValueError):
                continue
            if page > 0:
                pages.append(page)
    return sorted(set(pages))


def retag_page(book_id: str, pages: str = "", scope: str = "", root: str = "") -> dict[str, object]:
    """整页重跑围栏判定并覆盖 mid/。scope=toc 取 toc.json 的 toc_pages。"""
    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    book = _find_book(sid, root)

    wanted = _parse_pages(pages)
    if not wanted and (scope or "").strip().lower() == "toc":
        wanted = _toc_pages(book)
    if not wanted:
        return {
            "ok": False,
            "book_id": sid,
            "reason": "no_pages",
            "hint": 'pages="6,7" 或 scope="toc"',
        }

    from app.page_sync import retag_page as _retag

    rows: list[dict[str, object]] = []
    changed = 0
    for page_no in wanted:
        row = _retag(sid, page_no)
        rows.append(row)
        if row.get("changed"):
            changed += 1
    return {
        "ok": all(bool(row.get("ok")) for row in rows),
        "book_id": sid,
        "pages": wanted,
        "changed": changed,
        "results": rows,
    }
