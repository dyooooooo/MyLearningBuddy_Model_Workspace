"""AI 改动权限类：非越权（edition）vs 越权（pipeline）。

与 learning-buddy-api ``editions.authority`` 语义对齐（轻量副本，避免跨仓依赖）。
"""

from __future__ import annotations

from typing import Any, Literal

AuthorityClass = Literal["edition", "pipeline"]

_PIPELINE_PATH_MARKERS = (
    "learning-buddy-api/app/",
    "exam-ocr-server/app/",
    "/app/services/ir/",
    "/app/services/mineru",
    "mineru_adapter",
    "column_detect",
    "layout_enrich",
)

_EDITION_DATA_MARKERS = (
    "/configs/editions/",
    "/configs/layout/",
    "layout_rules.yaml",
    "/overrides/pages/",
)


def classify_authority(
    *,
    mode: str = "",
    paths: list[str] | None = None,
    explicit: str = "",
) -> AuthorityClass:
    ex = (explicit or "").strip().lower()
    if ex in ("edition", "pipeline"):
        return ex  # type: ignore[return-value]
    if ex in ("非越权", "in_bounds", "in-bounds"):
        return "edition"
    if ex in ("越权", "out_of_bounds", "out-of-bounds"):
        return "pipeline"

    for raw in paths or []:
        p = str(raw).replace("\\", "/")
        if any(m in p for m in _EDITION_DATA_MARKERS):
            continue
        if any(m in p for m in _PIPELINE_PATH_MARKERS) and (
            p.endswith((".py", ".pyi", ".pyx")) or "/app/" in p
        ):
            return "pipeline"

    m = (mode or "").strip().lower()
    if m in ("fs_violation", "fs_edit", "pipeline", "core_code"):
        return "pipeline"
    return "edition"


def authority_label(auth: str) -> str:
    return "越权" if auth == "pipeline" else "非越权"


def stamp_authority(entry: dict[str, Any], *, mode: str = "", paths: list[str] | None = None) -> dict[str, Any]:
    out = dict(entry)
    auth = classify_authority(
        mode=mode or str(out.get("mode") or ""),
        paths=paths or (list(out.get("paths") or []) if isinstance(out.get("paths"), list) else None),
        explicit=str(out.get("authority") or ""),
    )
    out["authority"] = auth
    out["authority_label"] = authority_label(auth)
    return out
