"""MinerU 中栏流水线用的轻量汇总（不依赖 Cursor SDK）。"""

from __future__ import annotations

from typing import Any


def pages_ready_n(meta: dict[str, Any]) -> int | None:
    raw = meta.get("pages_ready")
    if isinstance(raw, list):
        return len(raw)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def symptom_rows_for_tick(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """按症状名汇总：几处、哪些页、手册怎么写。给中栏流水线用。"""
    if not isinstance(data, dict):
        return []
    rows: dict[str, dict[str, Any]] = {}
    for bucket, known in (("known", True), ("unknown", False)):
        for finding in data.get(bucket) or []:
            if not isinstance(finding, dict):
                continue
            name = str(finding.get("suspect_type") or "").strip()
            if not name:
                continue
            rec = rows.setdefault(
                name,
                {
                    "suspect_type": name,
                    "known": known,
                    "count": 0,
                    "pages": [],
                    "playbook": str(finding.get("playbook") or "")[:240],
                    "remediation_kind": str(finding.get("remediation_kind") or ""),
                    "landing_target": str(finding.get("landing_target") or ""),
                },
            )
            rec["count"] = int(rec["count"]) + 1
            try:
                page = int(finding.get("page_no"))
            except (TypeError, ValueError):
                page = 0
            if page > 0:
                pages = rec["pages"]
                if isinstance(pages, list) and page not in pages:
                    pages.append(page)
    return list(rows.values())
