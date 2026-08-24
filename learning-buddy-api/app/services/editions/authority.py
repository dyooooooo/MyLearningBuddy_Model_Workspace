"""AI 改动权限类：非越权（edition）vs 越权（pipeline）。

非越权：后期只需改方案数据（Edition Bundle / layout_rules / page override）即可吸收。
越权：需要改 Pipeline 核心代码（特征量测、判区算法、适配器等）才能系统性修好。
"""

from __future__ import annotations

from typing import Any, Literal

AuthorityClass = Literal["edition", "pipeline"]

# 路径命中这些前缀 → 默认判为越权（核心代码）
_PIPELINE_PATH_MARKERS = (
    "learning-buddy-api/app/",
    "exam-ocr-server/app/",
    "/app/services/ir/",
    "/app/services/mineru",
    "mineru_adapter",
    "column_detect",
    "layout_enrich",
)

# 这些路径虽在 app 旁，但是数据/规则 → 非越权
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
    """根据工具 mode / 改动路径推断权限类；explicit 优先。"""
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
        if any(m in p for m in _PIPELINE_PATH_MARKERS) and p.endswith(
            (".py", ".pyi", ".pyx")
        ):
            return "pipeline"
        if any(m in p for m in _PIPELINE_PATH_MARKERS) and "/app/" in p:
            return "pipeline"

    m = (mode or "").strip().lower()
    # 明确的核心代码闸门 / 违规
    if m in ("fs_violation", "fs_edit", "pipeline", "core_code"):
        return "pipeline"
    # 方案数据 / IR 页级修补 → 非越权
    return "edition"


def normalize_candidate_authority(candidate: dict[str, Any]) -> dict[str, Any]:
    """写入 candidate.authority + 同步 tags（authority:* / 中文标签）。"""
    out = dict(candidate)
    tags = [str(t) for t in (out.get("tags") or []) if str(t).strip()]
    auth = classify_authority(
        mode=str(out.get("mode") or ""),
        paths=list(out.get("paths") or []) if isinstance(out.get("paths"), list) else None,
        explicit=str(out.get("authority") or ""),
    )
    if "authority:pipeline" in tags or "越权" in tags:
        auth = "pipeline"
    elif "authority:edition" in tags or "非越权" in tags:
        if str(out.get("authority") or "").strip() not in ("pipeline", "越权"):
            auth = "edition"

    out["authority"] = auth
    tags = [
        t
        for t in tags
        if not t.startswith("authority:") and t not in ("越权", "非越权")
    ]
    tags.append(f"authority:{auth}")
    tags.append("越权" if auth == "pipeline" else "非越权")
    out["tags"] = tags
    return out


def authority_label(auth: str) -> str:
    return "越权" if auth == "pipeline" else "非越权"
