"""Structured RAG hints for eval SOLVE (no standard answers)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.rag.retriever import EvalContext

_HINT_HEADER = (
    "【约束】以下仅为知识点、解法、公式与定义，不得抄写其中的最终数值答案；须依据题干独立推导。"
)


def _strip_answer_fields(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    meta = dict(out.get("meta") or {})
    meta.pop("answer", None)
    out["meta"] = meta
    return out


def _line_kp(item: dict[str, Any]) -> str:
    label = item.get("label") or item.get("id") or ""
    summary = item.get("summary") or ""
    return f"- {label}: {summary}".strip(": ")


def _line_method(item: dict[str, Any]) -> str:
    label = item.get("label") or item.get("id") or ""
    text = item.get("text") or item.get("summary") or ""
    return f"- {label}: {text}".strip(": ")


def _line_shortcut(item: dict[str, Any]) -> str:
    label = item.get("label") or item.get("id") or ""
    formula = item.get("formula") or ""
    summary = item.get("summary") or ""
    if formula:
        return f"- {label}: {formula} ({summary})".strip()
    return f"- {label}: {summary}".strip(": ")


def _line_chunk(item: dict[str, Any]) -> str:
    label = item.get("label") or item.get("id") or ""
    text = item.get("summary") or item.get("text") or ""
    return f"- {label}: {text}".strip(": ")


@dataclass
class SolveHints:
    hints_text: str = ""
    hints_meta: dict[str, Any] = field(default_factory=dict)
    entry_node_ids: list[str] = field(default_factory=list)

    def format_for_prompt(self) -> str:
        return self.hints_text


def build_solve_hints(ctx: EvalContext) -> SolveHints:
    n = settings.eval_hints_max_per_section
    kps = [_strip_answer_fields(x) for x in (ctx.kps or [])[:n]]
    methods = [_strip_answer_fields(x) for x in (ctx.methods or [])[:n]]
    shortcuts = [_strip_answer_fields(x) for x in (ctx.shortcuts or [])[:n]]
    chunks = [_strip_answer_fields(x) for x in (ctx.chunks or [])[:n]]

    sections: list[str] = [_HINT_HEADER]
    if kps:
        sections.append("【知识点】\n" + "\n".join(_line_kp(k) for k in kps))
    if methods:
        sections.append("【解法】\n" + "\n".join(_line_method(m) for m in methods))
    if shortcuts:
        sections.append("【公式口诀】\n" + "\n".join(_line_shortcut(s) for s in shortcuts))
    if chunks:
        sections.append("【定义】\n" + "\n".join(_line_chunk(c) for c in chunks))

    hints_text = "\n\n".join(sections)
    meta = {
        "entry_node_ids": list(ctx.entry_node_ids or []),
        "kp_count": len(kps),
        "method_count": len(methods),
        "shortcut_count": len(shortcuts),
        "chunk_count": len(chunks),
        "tags": list(ctx.tags or [])[:12],
    }
    return SolveHints(
        hints_text=hints_text,
        hints_meta=meta,
        entry_node_ids=list(ctx.entry_node_ids or []),
    )
