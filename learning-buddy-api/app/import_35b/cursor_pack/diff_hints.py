"""Semantic diff hints for Cursor — skip Pydantic default-field noise."""
from __future__ import annotations

import json
from typing import Any

_SEMANTIC_BLOCK_KEYS = frozenset(
    {"id", "kind", "role", "zone", "bbox", "anchor_id", "figure_group_id", "content_type"}
)
_SEMANTIC_CONTENT_KEYS = frozenset(
    {"text", "equation", "table", "figure", "latex", "table_html", "caption"}
)


def _block_semantic(block: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(block, dict):
        return {}
    out: dict[str, Any] = {k: block[k] for k in _SEMANTIC_BLOCK_KEYS if k in block}
    content = block.get("content")
    if isinstance(content, dict):
        out["content"] = {
            k: content[k] for k in _SEMANTIC_CONTENT_KEYS if k in content
        }
    return out


def semantic_blueprint_summary(bp: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(bp, dict):
        return {}
    blocks = bp.get("blocks") or []
    roles: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for b in blocks:
        if not isinstance(b, dict):
            continue
        r = str(b.get("role") or "?")
        k = str(b.get("kind") or "?")
        roles[r] = roles.get(r, 0) + 1
        kinds[k] = kinds.get(k, 0) + 1
    return {
        "page_no": bp.get("page_no"),
        "block_count": len(blocks),
        "roles": roles,
        "kinds": kinds,
    }


def build_diff_hints(
    raw: dict[str, Any] | None,
    repaired: dict[str, Any] | None,
    blueprint: dict[str, Any] | None,
) -> str:
    lines: list[str] = ["# Diff hints（语义字段，已过滤 Schema 默认项）", ""]
    lines.append("## 块统计")
    lines.append(f"- raw: `{json.dumps(semantic_blueprint_summary(raw), ensure_ascii=False)}`")
    lines.append(
        f"- blueprint: `{json.dumps(semantic_blueprint_summary(blueprint), ensure_ascii=False)}`"
    )
    lines.append("")
    if raw and repaired:
        raw_blocks = {b.get("id"): b for b in (raw.get("blocks") or []) if isinstance(b, dict) and b.get("id")}
        rep_blocks = {
            b.get("id"): b
            for b in (repaired.get("blocks") or [])
            if isinstance(b, dict) and b.get("id")
        }
        changed_ids: list[str] = []
        for bid in sorted(set(raw_blocks) | set(rep_blocks)):
            if _block_semantic(raw_blocks.get(bid)) != _block_semantic(rep_blocks.get(bid)):
                changed_ids.append(str(bid))
        lines.append("## 防腐层语义变更块")
        lines.append(", ".join(changed_ids) if changed_ids else "（无）")
        lines.append("")
    lines.append("## 说明")
    lines.append("- `post_repair` 与后段 JSON 相同 = MFR/crop/normalize no-op，可忽略")
    lines.append("- 完整 changelog 见 evidence/stage_changelog.json")
    return "\n".join(lines) + "\n"
