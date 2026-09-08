"""给围栏覆盖的正文写入 Obsidian 块 ID。幂等，只补没有标记的围栏。

`pages/*.md` 是唯一源头。ID 写进正文，中间层围栏内同步同一份标记。
`paste_fences` 靠这些标记重锚，改正文一个字不再丢掉排版。

- `stamp_block_ids(book_id="…", apply="scan")` —— 只统计
- `stamp_block_ids(book_id="…", apply="fix")` —— 写盘
- `book_id` 为空且 `apply=fix` 时处理工作区里每一本（全量回填）

同步过程不写正文；新围栏先走精确匹配，需要 ID 时再跑本工具。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _book_dir(book_id: str, root: str) -> Path:
    base = Path(root).resolve() if root else Path.cwd()
    if (base / "textbooks").is_dir():
        base = (base / "textbooks").resolve()
    return base / book_id


def _page_stems(book: Path) -> list[int]:
    pages = book / "pages"
    if not pages.is_dir():
        return []
    out: list[int] = []
    for path in sorted(pages.glob("*.md")):
        if path.name[:4].isdigit():
            out.append(int(path.stem))
    return out


def _stamp_one(sid: str, page_no: int, *, write: bool) -> dict[str, Any]:
    from app import library
    from app.block_ids import stamp_text
    from app.page_sync import write_stamp

    source_path = library.page_path(sid, page_no)
    mid_path = library.mid_path(sid, page_no)
    if not source_path.is_file():
        return {"page_no": page_no, "skipped": True, "reason": "no_source"}
    source = source_path.read_text(encoding="utf-8")
    mid = mid_path.read_text(encoding="utf-8") if mid_path.is_file() else source
    new_source, new_mid, stats = stamp_text(source, mid, seed=f"{sid}:{page_no}")
    row = {"page_no": page_no, **stats}
    if not stats.get("changed"):
        return row
    if not write:
        return row
    library.write_page(sid, page_no, new_source, sync=False)
    library.write_mid(sid, page_no, new_mid, sync=False)
    write_stamp(sid, page_no, new_source, new_mid)
    return row


def stamp_block_ids(book_id: str = "", apply: str = "scan", root: str = "") -> dict[str, Any]:
    mode = (apply or "scan").strip().lower()
    if mode not in {"scan", "fix"}:
        raise ValueError("apply must be scan or fix")
    write = mode == "fix"

    from app import library
    from app.config import settings

    base = Path(root).resolve() if root else settings.textbooks_dir
    if (base / "textbooks").is_dir():
        base = (base / "textbooks").resolve()

    sids: list[str]
    raw = (book_id or "").strip()
    if raw:
        if not _SAFE_ID.match(raw):
            raise ValueError("book_id required")
        sids = [library.safe_id(raw)]
    else:
        sids = sorted(p.name for p in base.iterdir() if p.is_dir() and (p / "pages").is_dir())

    books: list[dict[str, Any]] = []
    totals = {"stamped": 0, "already": 0, "skipped": 0, "changed_pages": 0}
    for sid in sids:
        book = _book_dir(sid, str(base))
        pages = _page_stems(book)
        stamped = already = skipped = changed_pages = 0
        for page_no in pages:
            row = _stamp_one(sid, page_no, write=write)
            stamped += int(row.get("stamped") or 0)
            already += int(row.get("already") or 0)
            skipped += int(row.get("skipped") or 0)
            if row.get("changed"):
                changed_pages += 1
        books.append({
            "book_id": sid,
            "pages": len(pages),
            "stamped": stamped,
            "already": already,
            "skipped": skipped,
            "changed_pages": changed_pages,
        })
        totals["stamped"] += stamped
        totals["already"] += already
        totals["skipped"] += skipped
        totals["changed_pages"] += changed_pages

    return {
        "ok": True,
        "apply": mode,
        "books": len(sids),
        **totals,
        "detail": books[:40],
    }
