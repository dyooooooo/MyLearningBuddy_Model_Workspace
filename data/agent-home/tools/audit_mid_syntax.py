"""Scan and repair mid-layer fence grammar (and leaked tokens in pages/).

中间层 ``mid/*.md`` 只允许成对的 ``::: kb-*`` 围栏。残片、套娃、粘在图注上的
``kb-center`` 会漏到画面上。本工具只做语法审核与剥残片，不改正文。

``apply=scan`` 只报告；``apply=fix`` 经 ``write_page`` / ``write_mid`` 闸门回写。
报告落 ``logs/audit/``。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from source_text import audit_mid, audit_source, sanitize_mid, sanitize_source


def _agent_home(root: str) -> Path:
    base = Path(root).resolve() if (root or "").strip() else Path.cwd()
    if (base / "textbooks").is_dir():
        return base
    if base.name == "textbooks":
        return base.parent
    if base.parent.name == "textbooks":
        return base.parent.parent
    try:
        from app.config import settings

        if not (root or "").strip():
            return settings.home
    except Exception:
        pass
    return base


def _book_dirs(home: Path, book_id: str) -> list[Path]:
    root = home / "textbooks"
    if not root.is_dir():
        return []
    sid = (book_id or "").strip()
    if sid:
        folder = root / sid
        return [folder] if folder.is_dir() else []
    return sorted(p for p in root.iterdir() if p.is_dir())


def _page_no(path: Path) -> int:
    try:
        return int(path.stem)
    except ValueError:
        return 0


def _scan_book(book: Path) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    mids: list[dict[str, Any]] = []
    for path in sorted((book / "pages").glob("[0-9][0-9][0-9][0-9].md")):
        issues = audit_source(path.read_text(encoding="utf-8"))
        if issues:
            pages.append({"page": _page_no(path), "issues": issues})
    for path in sorted((book / "mid").glob("[0-9][0-9][0-9][0-9].md")):
        issues = audit_mid(path.read_text(encoding="utf-8"))
        if issues:
            mids.append({"page": _page_no(path), "issues": issues})
    return {
        "book_id": book.name,
        "pages": pages,
        "mids": mids,
        "page_hits": len(pages),
        "mid_hits": len(mids),
        "issue_count": sum(len(row["issues"]) for row in pages + mids),
    }


def _fix_book(book: Path) -> dict[str, int]:
    changed_pages = 0
    changed_mids = 0
    try:
        from app import library

        sid = book.name
        for path in sorted((book / "pages").glob("[0-9][0-9][0-9][0-9].md")):
            n = _page_no(path)
            before = path.read_text(encoding="utf-8")
            if sanitize_source(before) != before:
                library.write_page(sid, n, before, sync=False)
                changed_pages += 1
        for path in sorted((book / "mid").glob("[0-9][0-9][0-9][0-9].md")):
            n = _page_no(path)
            before = path.read_text(encoding="utf-8")
            if sanitize_mid(before) != before:
                library.write_mid(sid, n, before, sync=False)
                changed_mids += 1
    except Exception:
        from source_text import write_source_atomic

        for path in sorted((book / "pages").glob("[0-9][0-9][0-9][0-9].md")):
            if write_source_atomic(path, path.read_text(encoding="utf-8")):
                changed_pages += 1
        for path in sorted((book / "mid").glob("[0-9][0-9][0-9][0-9].md")):
            before = path.read_text(encoding="utf-8")
            after = sanitize_mid(before)
            if after != before:
                path.write_text(after, encoding="utf-8")
                changed_mids += 1
    return {"pages_fixed": changed_pages, "mids_fixed": changed_mids}


def audit_mid_syntax(book_id: str = "", apply: str = "scan", root: str = "") -> dict[str, Any]:
    """Audit mid-layer fence syntax; apply=fix peels remnants through the write gates."""
    home = _agent_home(root)
    mode = (apply or "scan").strip().lower()
    if mode not in {"scan", "fix"}:
        raise ValueError("apply must be scan or fix")
    books = _book_dirs(home, book_id)
    if not books:
        return {"ok": False, "error": "没有找到教材", "book_id": book_id}

    scanned = [_scan_book(folder) for folder in books]
    fixed: list[dict[str, Any]] = []
    if mode == "fix":
        for folder in books:
            row = {"book_id": folder.name, **_fix_book(folder)}
            fixed.append(row)
        scanned = [_scan_book(folder) for folder in books]

    payload: dict[str, Any] = {
        "ok": True,
        "apply": mode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "books": scanned,
        "fixed": fixed,
        "totals": {
            "books": len(scanned),
            "page_hits": sum(int(b["page_hits"]) for b in scanned),
            "mid_hits": sum(int(b["mid_hits"]) for b in scanned),
            "issue_count": sum(int(b["issue_count"]) for b in scanned),
        },
    }
    try:
        from app.config import settings
        from app.logsetup import event, logs_dir

        folder = logs_dir() / "audit"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        sid = (book_id or "all").strip() or "all"
        path = folder / f"mid-syntax-{sid}-{stamp}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        payload["audit_path"] = str(path.relative_to(settings.home))
        event(
            "mid.syntax.audit",
            book_id=sid,
            apply=mode,
            page_hits=payload["totals"]["page_hits"],
            mid_hits=payload["totals"]["mid_hits"],
            issues=payload["totals"]["issue_count"],
        )
    except Exception:
        pass
    return payload
