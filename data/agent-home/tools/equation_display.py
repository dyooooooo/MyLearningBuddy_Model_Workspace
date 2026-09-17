"""Shape rules for textbook diagram-like equations.

Library only — not in manifest.yaml. No ``app.*`` imports.
A formula is a diagram when its LaTeX looks like an annotated figure
(long reaction arrow, chemfig/tikz, or array/overset with CJK), not when
the book subject is chemistry.
"""
from __future__ import annotations

import re
from typing import Any

_DIAGRAM_MACRO = re.compile(
    r"\\(?:xlongequal|xleftrightarrow|xleftrightharpoons|chemfig|tikz(?:picture)?)\b"
)
_STACK_MACRO = re.compile(
    r"\\(?:overset|underset|stackrel|overbrace|underbrace)\b"
)
_ARRAY_ENV = re.compile(
    r"\\begin\{(?:array|matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|smallmatrix)\}",
    re.I,
)
_CJK = re.compile(r"[\u4e00-\u9fff]")
_EQUATION_TYPES = frozenset({"equation", "equation_interline"})


def is_diagram_latex(tex: str) -> bool:
    text = tex or ""
    if not text.strip():
        return False
    if _DIAGRAM_MACRO.search(text):
        return True
    if _CJK.search(text) and (_ARRAY_ENV.search(text) or _STACK_MACRO.search(text)):
        return True
    return False


def diagram_asset_name(page_no: int, bbox: tuple[float, float, float, float]) -> str:
    x0, y0, x1, y1 = (int(round(float(v))) for v in bbox)
    return f"eq-p{int(page_no):04d}-{x0}-{y0}-{x1}-{y1}.jpg"


def block_bbox(block: dict[str, Any]) -> tuple[float, float, float, float] | None:
    bb = block.get("bbox")
    if not isinstance(bb, (list, tuple)) or len(bb) < 4:
        return None
    try:
        x0, y0, x1, y1 = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
    except (TypeError, ValueError):
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    if max(abs(x0), abs(y0), abs(x1), abs(y1)) <= 1.5:
        x0, y0, x1, y1 = x0 * 1000.0, y0 * 1000.0, x1 * 1000.0, y1 * 1000.0
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return x0, y0, x1, y1


def block_page_no(block: dict[str, Any]) -> int:
    for key in ("page_no", "page_idx", "page_index"):
        val = block.get(key)
        if isinstance(val, int):
            return val + 1 if key != "page_no" else val
        if isinstance(val, str) and val.strip().lstrip("-").isdigit():
            num = int(val)
            return num + 1 if key != "page_no" else num
    return 0


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "".join(p for p in (_as_text(x) for x in value) if p)
    if isinstance(value, dict):
        for key in (
            "content",
            "text",
            "math_content",
            "item_content",
            "paragraph_content",
        ):
            if key in value:
                got = _as_text(value[key])
                if got:
                    return got
    return ""


def block_latex(block: dict[str, Any]) -> str:
    text = _as_text(block.get("text"))
    if text:
        return text
    content = block.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, dict):
        got = _as_text(content.get("math_content")) or _as_text(content)
        if got:
            return got
    return _as_text(content)


def is_equation_type(block: dict[str, Any]) -> bool:
    return str(block.get("type") or "").strip().lower() in _EQUATION_TYPES


def is_diagram_block(block: dict[str, Any]) -> bool:
    return is_equation_type(block) and is_diagram_latex(block_latex(block))
