"""Per-page pipeline stage diff → structured changelog for Studio debug drawer."""
from __future__ import annotations

import json
from typing import Any

_MAX_SNIPPET = 200

TRANSITIONS: list[tuple[str, str, str, str]] = [
    ("raw_model", "repaired_model", "防腐层修复", "boundary_repair"),
    ("repaired_model", "post_repair", "Schema 校验", "validate"),
    ("post_repair", "post_mfr", "MFR 公式增强", "mfr"),
    ("post_mfr", "post_crop", "插图裁剪", "crop"),
    ("post_crop", "blueprint", "Normalize", "normalize"),
]


def _trunc(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) <= _MAX_SNIPPET:
        return text
    return text[: _MAX_SNIPPET - 1] + "…"


def _blocks_by_id(bp: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(bp, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for b in bp.get("blocks") or []:
        if isinstance(b, dict) and b.get("id"):
            out[str(b["id"])] = b
    return out


def _reason_for_transition(transition: str, field_path: str) -> str:
    _prefix, reason_key = transition.split("→", 1) if "→" in transition else ("", "")
    # Map transition end stage to default reason namespace
    if transition == "raw_model→repaired_model":
        return "boundary_repair:field"
    if transition == "repaired_model→post_repair":
        return "validate:field"
    if transition == "post_repair→post_mfr":
        if "equation" in field_path or "latex" in field_path:
            return "mfr:equation_replace"
        return "mfr:field"
    if transition == "post_mfr→post_crop":
        if "figure" in field_path or "asset" in field_path:
            return "crop:figure_asset"
        return "crop:field"
    if transition == "post_crop→blueprint":
        if "segments" in field_path:
            return "normalize:segment"
        if "valid" in field_path:
            return "normalize:valid_flag"
        return "normalize:field"
    return "changed"


def _summary_zh(
    *,
    stage_label: str,
    block_id: str,
    field_path: str,
    kind: str,
    before: Any,
    after: Any,
    reason: str,
) -> str:
    blk = f"块 {block_id} · " if block_id else ""
    if kind == "added":
        return f"{blk}新增 {field_path}"
    if kind == "removed":
        return f"{blk}删除 {field_path}"
    if reason.startswith("boundary_repair"):
        return f"{blk}{field_path}：防腐层修复（{_trunc(before)} → {_trunc(after)}）"
    if reason.startswith("mfr:"):
        return f"{blk}公式：MFR 改写（{_trunc(before)} → {_trunc(after)}）"
    if reason.startswith("crop:"):
        return f"{blk}插图资源已裁剪/写入（{_trunc(after)}）"
    if reason.startswith("normalize:"):
        return f"{blk}{field_path}：Normalize（{_trunc(before)} → {_trunc(after)}）"
    return f"{blk}{stage_label} · {field_path}：{_trunc(before)} → {_trunc(after)}"


def _diff_values(
    before: Any,
    after: Any,
    path: str,
    *,
    transition: str,
    stage_label: str,
    block_id: str,
    changes: list[dict[str, Any]],
) -> None:
    if before == after:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        keys = set(before) | set(after)
        for k in sorted(keys):
            child = f"{path}.{k}" if path else k
            if k not in before:
                changes.append(
                    _entry(
                        transition=transition,
                        stage_label=stage_label,
                        block_id=block_id,
                        path=child,
                        kind="added",
                        before=None,
                        after=after[k],
                    )
                )
            elif k not in after:
                changes.append(
                    _entry(
                        transition=transition,
                        stage_label=stage_label,
                        block_id=block_id,
                        path=child,
                        kind="removed",
                        before=before[k],
                        after=None,
                    )
                )
            else:
                _diff_values(
                    before[k],
                    after[k],
                    child,
                    transition=transition,
                    stage_label=stage_label,
                    block_id=block_id,
                    changes=changes,
                )
        return
    if isinstance(before, list) and isinstance(after, list):
        if json.dumps(before, ensure_ascii=False, sort_keys=True) == json.dumps(
            after, ensure_ascii=False, sort_keys=True
        ):
            return
        changes.append(
            _entry(
                transition=transition,
                stage_label=stage_label,
                block_id=block_id,
                path=path,
                kind="modified",
                before=before,
                after=after,
            )
        )
        return
    changes.append(
        _entry(
            transition=transition,
            stage_label=stage_label,
            block_id=block_id,
            path=path,
            kind="modified",
            before=before,
            after=after,
        )
    )


def _entry(
    *,
    transition: str,
    stage_label: str,
    block_id: str,
    path: str,
    kind: str,
    before: Any,
    after: Any,
) -> dict[str, Any]:
    reason = _reason_for_transition(transition, path)
    return {
        "transition": transition,
        "stage_label": stage_label,
        "block_id": block_id,
        "path": path,
        "kind": kind,
        "before": _trunc(before) if before is not None else "",
        "after": _trunc(after) if after is not None else "",
        "reason": reason,
        "summary_zh": _summary_zh(
            stage_label=stage_label,
            block_id=block_id,
            field_path=path,
            kind=kind,
            before=before,
            after=after,
            reason=reason,
        ),
    }


def _diff_blueprints(
    before_bp: dict[str, Any] | None,
    after_bp: dict[str, Any] | None,
    *,
    transition: str,
    stage_label: str,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    before_blocks = _blocks_by_id(before_bp)
    after_blocks = _blocks_by_id(after_bp)
    all_ids = sorted(set(before_blocks) | set(after_blocks))
    for bid in all_ids:
        b0 = before_blocks.get(bid)
        b1 = after_blocks.get(bid)
        if b0 is None and b1 is not None:
            changes.append(
                _entry(
                    transition=transition,
                    stage_label=stage_label,
                    block_id=bid,
                    path="blocks",
                    kind="added",
                    before=None,
                    after=b1,
                )
            )
            continue
        if b0 is not None and b1 is None:
            changes.append(
                _entry(
                    transition=transition,
                    stage_label=stage_label,
                    block_id=bid,
                    path="blocks",
                    kind="removed",
                    before=b0,
                    after=None,
                )
            )
            continue
        if b0 is not None and b1 is not None:
            _diff_values(
                b0,
                b1,
                f"blocks.{bid}",
                transition=transition,
                stage_label=stage_label,
                block_id=bid,
                changes=changes,
            )
    # page-level fields (exclude blocks list duplication)
    if isinstance(before_bp, dict) and isinstance(after_bp, dict):
        b_meta = {k: v for k, v in before_bp.items() if k != "blocks"}
        a_meta = {k: v for k, v in after_bp.items() if k != "blocks"}
        _diff_values(
            b_meta,
            a_meta,
            "page",
            transition=transition,
            stage_label=stage_label,
            block_id="",
            changes=changes,
        )
    return changes


def _merge_boundary_repairs(
    changes: list[dict[str, Any]],
    repairs: list[dict[str, Any]],
    *,
    transition: str,
    stage_label: str,
) -> list[dict[str, Any]]:
    if not repairs:
        return changes
    seen = {c.get("summary_zh") for c in changes}
    for rep in repairs:
        cmd = str(rep.get("command") or rep.get("rule") or "repair")
        damaged = rep.get("damaged") or rep.get("before") or ""
        summary = f"防腐层：LaTeX 命令 {cmd} 已从转义损伤还原"
        if summary in seen:
            continue
        seen.add(summary)
        changes.append(
            {
                "transition": transition,
                "stage_label": stage_label,
                "block_id": "",
                "path": "boundary_repairs",
                "kind": "modified",
                "before": _trunc(damaged),
                "after": _trunc(cmd),
                "reason": f"boundary_repair:{rep.get('rule', 'ctrl')}",
                "summary_zh": summary,
            }
        )
    return changes


def build_stage_changelog(
    page_no: int,
    stages: dict[str, Any],
    *,
    boundary_repairs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build changelog from named stage snapshots (dict per stage key)."""
    by_transition: dict[str, dict[str, Any]] = {}
    total = 0
    counts_by_label: dict[str, int] = {}

    for left, right, label, _reason_ns in TRANSITIONS:
        key = f"{left}→{right}"
        before = stages.get(left)
        after = stages.get(right)
        if before is None and after is None:
            by_transition[key] = {"count": 0, "changes": [], "stage_label": label}
            continue
        changes = _diff_blueprints(before, after, transition=key, stage_label=label)
        if key == "raw_model→repaired_model" and boundary_repairs:
            changes = _merge_boundary_repairs(
                changes,
                boundary_repairs,
                transition=key,
                stage_label=label,
            )
        by_transition[key] = {"count": len(changes), "changes": changes, "stage_label": label}
        total += len(changes)
        if changes:
            counts_by_label[label] = counts_by_label.get(label, 0) + len(changes)

    parts = [f"{k} {v}" for k, v in counts_by_label.items()]
    if total == 0:
        summary_zh = "本页模型输出未被后处理修改"
    else:
        summary_zh = f"本页共 {total} 处后处理修改" + (f"：{'、'.join(parts)}" if parts else "")

    return {
        "page_no": page_no,
        "total_changes": total,
        "by_transition": by_transition,
        "summary_zh": summary_zh,
    }


def changelog_summary(changelog: dict[str, Any]) -> dict[str, Any]:
    by_t = changelog.get("by_transition") or {}
    return {
        "total_changes": int(changelog.get("total_changes") or 0),
        "summary_zh": str(changelog.get("summary_zh") or ""),
        "by_transition": {
            k: int((v or {}).get("count") or 0) for k, v in by_t.items() if isinstance(v, dict)
        },
    }
