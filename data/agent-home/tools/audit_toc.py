from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from write_toc_pages import write_toc_pages


def audit_toc(book_id: str) -> dict[str, Any]:
    """Scan one book's printed toc against toc.json and keep the report under logs/audit/."""
    from app.config import settings
    from app.logsetup import event, logs_dir

    sid = (book_id or "").strip()
    if not sid:
        raise ValueError("book_id required")
    report = write_toc_pages(sid, apply="scan")
    folder = logs_dir() / "audit"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = folder / f"{sid}-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rel = str(path.relative_to(settings.home))
    event(
        "toc.audit",
        book_id=sid,
        path=rel,
        level_mismatch=report.get("level_mismatch"),
        guess=report.get("guess"),
        miss=report.get("miss"),
        unmapped=report.get("unmapped_count"),
        empty_toc_pages=report.get("empty_toc_pages"),
    )
    report["audit_path"] = rel
    return report
