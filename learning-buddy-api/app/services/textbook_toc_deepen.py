"""PDF textbook import only: deepen coarse TOC leaves by knowledge point.

Belongs to the textbook import pipeline — never call from corpus_publish / RAG.

Hard rule: every child must be anchored to real IR blocks (ids + pages).
Labels must come from (or appear in) those blocks — no free-form invention.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.clients.vllm import chat_completion, chat_completion_json, parse_json_block_robust
from app.config import settings
from app.services.corpus_tree_from_ir import IrSection, _slug, finalize_leaf_section
from app.services.document_ir import DocumentBlock, html_to_plain_text, is_lesson_text

logger = logging.getLogger(__name__)

_KP_FINE_RE = re.compile(r"^\d+\.\d+\.\d+")
_EXAMPLE_RE = re.compile(r"例\s*[０-９0-9]+")
_EXAMPLE_HEAD_RE = re.compile(r"^(例\s*[０-９0-9]+)")
_WS_RE = re.compile(r"\s+")

DEEPEN_SYSTEM = """你是高中教材目录编辑。把过粗的小节按课文真实结构拆成子节。
硬性规则：
1. 只能用输入 blocks 的下标区间 [i_start, i_end]（含两端）；禁止编造不存在的块。
2. 优先在真实小标题 / 「例N」处切开；不要按字数硬切。
3. label 必须能在该区间正文中找到依据（抄小标题原文，或用区间首句关键词）；禁止杜撰书中没有的标题。
4. 子节编号尽量延续父节（如 1.1 → 1.1.1）；若区间小标题已带编号则沿用。
5. 区间按课文顺序、不重叠；未覆盖的块由系统并入相邻节（你应尽量覆盖全部下标）。
6. 只输出 JSON。子节数 2–12。"""

DEEPEN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "children": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "primary_kp": {"type": "string"},
                    "i_start": {"type": "integer"},
                    "i_end": {"type": "integer"},
                },
                "required": ["label", "i_start", "i_end"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["children"],
    "additionalProperties": False,
}


def is_kp_fine_label(label: str) -> bool:
    return bool(_KP_FINE_RE.match((label or "").strip()))


def _plain(sec: IrSection) -> str:
    text = sec.text or ""
    if text.strip().startswith("<"):
        return html_to_plain_text(text)
    return text


def _norm(s: str) -> str:
    return _WS_RE.sub("", (s or "").strip().lower())


def leaf_needs_deepen(sec: IrSection) -> bool:
    """Coarse lesson/body leaves may need deepen; kp-fine labels skip."""
    kind = str(sec.meta.get("section_kind") or "")
    if kind not in ("lesson", "body"):
        return False
    if not sec.blocks and not (sec.text or "").strip():
        return False
    if is_kp_fine_label(sec.label):
        return False
    plain = _plain(sec)
    if len(_EXAMPLE_RE.findall(plain)) >= 2:
        return True
    soft = max(400, int(settings.textbook_toc_deepen_soft_chars))
    if len(plain) >= soft:
        return True
    if re.match(r"^\d+\.\d+\b", (sec.label or "").strip()):
        return True
    return len(plain) > 200


def _block_plain(b: DocumentBlock) -> str:
    t = (b.text or b.caption or "").strip()
    if t.startswith("<"):
        return html_to_plain_text(t)
    m = re.match(r"^#{1,6}\s*(.+)$", t)
    return (m.group(1) if m else t).strip()


def _block_catalog(sec: IrSection) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, b in enumerate(sec.blocks):
        snippet = _block_plain(b)[:64].replace("\\", " ").replace('"', "'")
        out.append(
            {
                "i": i,
                "id": b.id,
                "page": b.page_no,
                "type": b.type,
                "text": snippet,
            }
        )
    return out


def _is_inleaf_anchor(block: DocumentBlock, *, parent_label: str) -> bool:
    """True if this block is a real in-leaf cut point (heading / 例N / 1.1.1)."""
    plain = _block_plain(block)
    if not plain or len(plain) > 80:
        return False
    if _norm(plain) == _norm(parent_label):
        return False
    # Same lesson heading repeated → not a sub-cut
    if is_lesson_text(plain) and not is_kp_fine_label(plain):
        return False
    if block.type in ("heading", "title"):
        return True
    if is_kp_fine_label(plain):
        return True
    if _EXAMPLE_HEAD_RE.match(plain):
        return True
    return False


def _heading_first_specs(parent: IrSection) -> list[dict[str, Any]] | None:
    """Deterministic split on real IR anchors inside the leaf. None if <2 anchors."""
    blocks = parent.blocks
    if len(blocks) < 2:
        return None
    anchors = [
        i
        for i, b in enumerate(blocks)
        if _is_inleaf_anchor(b, parent_label=parent.label)
    ]
    # Need at least 2 cut points, or 1 cut not at start (prefix + rest)
    if len(anchors) >= 2:
        starts = list(anchors)
    elif len(anchors) == 1 and anchors[0] > 0:
        starts = [0, anchors[0]]
    else:
        return None

    specs: list[dict[str, Any]] = []
    for k, start in enumerate(starts):
        end = (starts[k + 1] - 1) if k + 1 < len(starts) else len(blocks) - 1
        # First slice absorbs any preamble before the first anchor (chap intro,
        # figures). Leaving those uncovered made _fill_remainder dump them onto
        # the last child (e.g. 「B组」) and break start_page sort order.
        slice_start = 0 if k == 0 else start
        if end < slice_start:
            continue
        slice_blocks = blocks[slice_start : end + 1]
        label = _ground_label("", slice_blocks, parent_label=parent.label, child_index=k)
        ids = [b.id for b in slice_blocks]
        specs.append({"label": label, "primary_kp": "", "block_ids": ids})
    return specs if len(specs) >= 2 else None


def _ground_label(
    proposed: str,
    blocks: list[DocumentBlock],
    *,
    parent_label: str,
    child_index: int,
) -> str:
    """Pick a label that is locatable in the assigned blocks' real text."""
    plains = [_block_plain(b) for b in blocks if _block_plain(b)]
    joined = "\n".join(plains)
    joined_norm = _norm(joined)
    proposed = (proposed or "").strip()

    # 1) Prefer real heading / 例N / 1.1.1 at the start of the slice
    for b in blocks[:3]:
        p = _block_plain(b)
        if not p:
            continue
        if _is_inleaf_anchor(b, parent_label=parent_label) or b.type in ("heading", "title"):
            return p[:60]

    # 2) AI proposal only if it appears in the slice text
    if proposed and len(proposed) >= 2 and _norm(proposed) in joined_norm:
        return proposed[:60]

    # 3) First non-empty sentence / line from real blocks
    if plains:
        first = plains[0]
        first = re.split(r"[。！？\n]", first)[0].strip() or first.strip()
        # Number under parent when possible
        m = re.match(r"^(\d+\.\d+)\b", parent_label.strip())
        if m and not re.match(r"^\d+\.\d+", first):
            return f"{m.group(1)}.{child_index + 1} {first[:40]}".strip()
        return first[:60]

    m = re.match(r"^(\d+\.\d+)\b", parent_label.strip())
    if m:
        return f"{m.group(1)}.{child_index + 1}"
    return f"{parent_label} · 节{child_index + 1}"


def _ranges_to_children_specs(
    children_raw: list[Any],
    catalog: list[dict[str, Any]],
    parent: IrSection,
) -> list[dict[str, Any]]:
    """Map model ranges → real block_ids; ground labels from IR text."""
    n = len(catalog)
    if n <= 0:
        return []
    id_by_i = {int(row["i"]): str(row["id"]) for row in catalog if "i" in row and "id" in row}
    by_id = {b.id: b for b in parent.blocks}
    specs: list[dict[str, Any]] = []
    for raw in children_raw:
        if not isinstance(raw, dict):
            continue
        try:
            a = int(raw.get("i_start", 0))
            b = int(raw.get("i_end", a))
        except (TypeError, ValueError):
            # Legacy tests may pass block_ids only
            ids = [str(x) for x in (raw.get("block_ids") or [])]
            blocks = [by_id[i] for i in ids if i in by_id]
            if not blocks:
                continue
            label = _ground_label(
                str(raw.get("label") or ""),
                blocks,
                parent_label=parent.label,
                child_index=len(specs),
            )
            specs.append(
                {
                    "label": label,
                    "primary_kp": str(raw.get("primary_kp") or "").strip(),
                    "block_ids": [b.id for b in blocks],
                }
            )
            continue
        if a > b:
            a, b = b, a
        a = max(0, min(a, n - 1))
        b = max(0, min(b, n - 1))
        ids = [id_by_i[i] for i in range(a, b + 1) if i in id_by_i]
        blocks = [by_id[i] for i in ids if i in by_id]
        if not blocks:
            continue
        label = _ground_label(
            str(raw.get("label") or ""),
            blocks,
            parent_label=parent.label,
            child_index=len(specs),
        )
        specs.append(
            {
                "label": label,
                "primary_kp": str(raw.get("primary_kp") or "").strip(),
                "block_ids": [x.id for x in blocks],
            }
        )
    return specs


def _validate_partition(
    parent: IrSection,
    specs: list[dict[str, Any]],
) -> tuple[bool, str]:
    """Require non-empty children whose block_ids ⊆ parent and cover all blocks."""
    if len(specs) < 2:
        return False, "need_at_least_2_children"
    parent_ids = [b.id for b in parent.blocks]
    parent_set = set(parent_ids)
    seen: set[str] = set()
    for spec in specs:
        ids = [str(x) for x in (spec.get("block_ids") or [])]
        if not ids:
            return False, "empty_child"
        for i in ids:
            if i not in parent_set:
                return False, f"unknown_block:{i}"
            if i in seen:
                return False, f"overlap:{i}"
            seen.add(i)
    missing = [i for i in parent_ids if i not in seen]
    if missing:
        # Allow small gap only if we can attach to neighbors in _assign — for
        # grounded mode we require full coverage (caller may fill remainder once).
        return False, f"uncovered:{len(missing)}"
    return True, "ok"


def _fill_remainder(parent: IrSection, specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach uncovered blocks to the nearest previous child (document order).

    Never dump leftovers onto the last child only — that pulled early preamble
    into 「B组」 and made start_page sort put B组 at the top of the tree.
    """
    by_id = {b.id: b for b in parent.blocks}
    order = {b.id: i for i, b in enumerate(parent.blocks)}
    covered: set[str] = set()
    for spec in specs:
        covered.update(str(x) for x in (spec.get("block_ids") or []))
    out = [dict(s, block_ids=list(s.get("block_ids") or [])) for s in specs]
    if not out:
        return out

    def _last_index(spec: dict[str, Any]) -> int:
        idxs = [order[i] for i in (spec.get("block_ids") or []) if i in order]
        return max(idxs) if idxs else -1

    def _first_index(spec: dict[str, Any]) -> int:
        idxs = [order[i] for i in (spec.get("block_ids") or []) if i in order]
        return min(idxs) if idxs else 10**9

    for b in parent.blocks:
        if b.id in covered:
            continue
        bi = order[b.id]
        best = -1
        best_last = -1
        for ci, spec in enumerate(out):
            last = _last_index(spec)
            if last < bi and last >= best_last:
                best_last = last
                best = ci
        if best < 0:
            # Before every child's span — attach to the earliest child
            best = min(range(len(out)), key=lambda ci: _first_index(out[ci]))
        out[best]["block_ids"].append(b.id)
        covered.add(b.id)

    for ci, spec in enumerate(out):
        ids = sorted(
            (str(x) for x in (spec.get("block_ids") or [])),
            key=lambda i: order.get(i, 10**9),
        )
        spec["block_ids"] = ids
        blocks = [by_id[i] for i in ids if i in by_id]
        spec["label"] = _ground_label(
            spec.get("label") or "",
            blocks,
            parent_label=parent.label,
            child_index=ci,
        )
    return out


def _salvage_children_json(text: str) -> dict[str, Any] | None:
    try:
        data = parse_json_block_robust(text)
        if isinstance(data, dict) and isinstance(data.get("children"), list):
            return data
    except Exception:
        pass
    children: list[dict[str, Any]] = []
    for m in re.finditer(
        r'"label"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*(?:"primary_kp"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*)?'
        r'"i_start"\s*:\s*(-?\d+)\s*,\s*"i_end"\s*:\s*(-?\d+)',
        text,
    ):
        children.append(
            {
                "label": m.group(1).replace('\\"', '"'),
                "primary_kp": (m.group(2) or "").replace('\\"', '"'),
                "i_start": int(m.group(3)),
                "i_end": int(m.group(4)),
            }
        )
    return {"children": children} if children else None


def ai_unify_outline_for_leaf(
    sec: IrSection,
    *,
    path_labels: list[str],
    kp_candidates: list[str] | None = None,
) -> dict[str, Any] | None:
    """Ask vLLM for index ranges; map/ground to real IR blocks. None on failure."""
    catalog = _block_catalog(sec)
    if not catalog:
        return None
    plain = _plain(sec)[:2500].replace("\\", " ")
    user = {
        "path": " / ".join(path_labels),
        "parent_label": sec.label,
        "plain_excerpt": plain,
        "blocks": catalog,
        "block_count": len(catalog),
        "kp_candidates": (kp_candidates or [])[:40],
        "hint": "只用 i_start/i_end；label 必须能在对应 blocks 原文中找到。",
    }
    user_text = json.dumps(user, ensure_ascii=False)
    data: dict[str, Any] | None = None
    try:
        data = chat_completion_json(
            DEEPEN_SYSTEM,
            user_text,
            DEEPEN_SCHEMA,
            schema_name="toc_deepen",
            max_tokens=4096,
            timeout_s=240.0,
        )
    except Exception:
        logger.warning(
            "textbook toc deepen schema path failed section=%s; trying unconstrained",
            sec.section_id,
            exc_info=True,
        )
        try:
            raw = chat_completion(
                DEEPEN_SYSTEM,
                user_text,
                max_tokens=4096,
                enable_thinking=False,
            )
            data = _salvage_children_json(raw)
            if data is None:
                data = parse_json_block_robust(raw)
        except Exception:
            logger.exception("textbook toc deepen AI failed section=%s", sec.section_id)
            return None

    if not isinstance(data, dict):
        return None
    children_raw = data.get("children")
    if not isinstance(children_raw, list) or not children_raw:
        return None

    specs = _ranges_to_children_specs(children_raw, catalog, sec)
    if len(specs) < 2:
        return None
    specs = _fill_remainder(sec, specs)
    ok, reason = _validate_partition(sec, specs)
    if not ok:
        logger.warning(
            "textbook toc deepen reject ungrounded partition section=%s reason=%s",
            sec.section_id,
            reason,
        )
        return None
    return {"children": specs}


def _assign_blocks(
    parent: IrSection,
    children_spec: list[dict[str, Any]],
) -> list[tuple[str, str, list[DocumentBlock], dict]]:
    by_id = {b.id: b for b in parent.blocks}
    assigned: list[tuple[str, str, list[DocumentBlock], dict]] = []
    for spec in children_spec:
        label = str(spec.get("label") or "").strip()
        ids = [str(x) for x in (spec.get("block_ids") or [])]
        blocks = [by_id[i] for i in ids if i in by_id]
        if not label or not blocks:
            continue
        pages = sorted({int(b.page_no) for b in blocks if b.page_no})
        meta = {
            "start_page": pages[0] if pages else parent.start_page,
            "end_page": pages[-1] if pages else parent.start_page,
            "block_ids": [b.id for b in blocks],
            "grounded": True,
        }
        pk = str(spec.get("primary_kp") or "").strip()
        assigned.append((label, pk, blocks, meta))
    return assigned


def deepen_leaf_section(
    parent: IrSection,
    *,
    prefix: str,
    path_labels: list[str],
    kp_candidates: list[str] | None = None,
) -> tuple[list[IrSection], dict[str, Any]]:
    """Replace one coarse leaf with container + grounded kp children (or mark review)."""
    meta_out: dict[str, Any] = {
        "parent_id": parent.section_id,
        "deepened": False,
        "needs_structure_review": False,
        "split_mode": "",
    }
    if not leaf_needs_deepen(parent):
        finalize_leaf_section(parent, prefix)
        return [parent], meta_out

    # 1) Prefer deterministic cuts on real IR headings / 例N
    specs = _heading_first_specs(parent)
    if specs:
        specs = _fill_remainder(parent, specs)
        ok, _ = _validate_partition(parent, specs)
        outline = {"children": specs} if ok else None
        meta_out["split_mode"] = "ir_anchor" if ok else ""
    else:
        outline = None

    # 2) AI proposes ranges only; labels grounded to block text
    if outline is None:
        outline = ai_unify_outline_for_leaf(
            parent, path_labels=path_labels, kp_candidates=kp_candidates
        )
        if outline:
            meta_out["split_mode"] = "ai_range_grounded"

    if not outline:
        parent.meta = {
            **parent.meta,
            "needs_toc_deepen": True,
            "needs_structure_review": True,
        }
        finalize_leaf_section(parent, prefix)
        meta_out["needs_structure_review"] = True
        return [parent], meta_out

    mapped = _assign_blocks(parent, list(outline.get("children") or []))
    if len(mapped) <= 1:
        if mapped and mapped[0][1]:
            parent.meta = {**parent.meta, "proposed_primary_kp": mapped[0][1]}
        finalize_leaf_section(parent, prefix)
        meta_out["deepened"] = False
        meta_out["single_kp"] = True
        return [parent], meta_out

    parent.chunks = []
    parent.meta = {
        **parent.meta,
        "section_kind": parent.meta.get("section_kind") or "lesson",
        "structure_role": "container_after_deepen",
    }
    parent.text = parent.label
    parent.summary = parent.label[:240]

    children: list[IrSection] = []
    for i, (label, pk, blocks, loc) in enumerate(mapped):
        sid = f"sec:{prefix}:kp:{_slug(parent.label)}:{i}:{_slug(label)}"
        child = IrSection(
            section_id=sid,
            label=label,
            summary=label,
            text="",
            level=parent.level + 1,
            parent_section_id=parent.section_id,
            start_page=int(loc.get("start_page") or parent.start_page),
            blocks=list(blocks),
            meta={
                "doc_type": "section",
                "section_kind": "lesson",
                "locale_en": parent.meta.get("locale_en", False),
                "deepened_from": parent.section_id,
                "start_page": loc.get("start_page"),
                "end_page": loc.get("end_page"),
                "block_ids": loc.get("block_ids") or [b.id for b in blocks],
                "grounded": True,
                "split_mode": meta_out.get("split_mode") or "ai_range_grounded",
                **({"proposed_primary_kp": pk} if pk else {}),
            },
        )
        finalize_leaf_section(child, prefix)
        if pk and child.chunks:
            child.chunks[0]["meta"] = {
                **child.chunks[0].get("meta", {}),
                "proposed_primary_kp": pk,
            }
        # Ensure chunk meta carries page span for Studio locate
        if child.chunks:
            pages = sorted({int(b.page_no) for b in blocks if b.page_no})
            child.chunks[0]["meta"] = {
                **child.chunks[0].get("meta", {}),
                "source_pages": pages,
                "start_page": pages[0] if pages else child.start_page,
                "end_page": pages[-1] if pages else child.start_page,
                "grounded": True,
            }
        children.append(child)

    meta_out["deepened"] = True
    meta_out["child_count"] = len(children)
    return [parent, *children], meta_out


def deepen_textbook_sections(
    sections: list[IrSection],
    *,
    prefix: str,
    textbook_label: str = "",
    kp_candidates: list[str] | None = None,
) -> tuple[list[IrSection], dict[str, Any]]:
    """Walk initial TOC; deepen coarse leaves. Chapters kept as containers."""
    id_to_label = {s.section_id: s.label for s in sections}
    out: list[IrSection] = []
    report: dict[str, Any] = {
        "structure_status": "structure_ready",
        "deepened_parents": [],
        "needs_structure_review": [],
        "leaf_count": 0,
        "split_modes": {},
    }

    for sec in sections:
        kind = str(sec.meta.get("section_kind") or "")
        if kind == "chapter":
            sec.chunks = []
            out.append(sec)
            continue
        # 前言 / 目录 stay as leaf sections — never AI-deepen
        if kind in ("preface", "toc", "front"):
            finalize_leaf_section(sec, prefix)
            out.append(sec)
            continue

        path = []
        if textbook_label:
            path.append(textbook_label)
        if sec.parent_section_id and sec.parent_section_id in id_to_label:
            path.append(id_to_label[sec.parent_section_id])
        path.append(sec.label)

        pieces, meta = deepen_leaf_section(
            sec,
            prefix=prefix,
            path_labels=path,
            kp_candidates=kp_candidates,
        )
        out.extend(pieces)
        if meta.get("deepened"):
            report["deepened_parents"].append(sec.section_id)
            if meta.get("split_mode"):
                report["split_modes"][sec.section_id] = meta["split_mode"]
        if meta.get("needs_structure_review"):
            report["needs_structure_review"].append(sec.section_id)
            report["structure_status"] = "needs_structure_review"

    report["leaf_count"] = sum(
        1
        for s in out
        if s.chunks
        and str(s.meta.get("section_kind") or "") in ("lesson", "body", "front")
    )
    return out, report
