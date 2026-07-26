"""Content normalization for PageBlueprint blocks."""
from __future__ import annotations

from app.math.cjk_split import looks_like_math
from app.math.normalize import normalize_math_only, normalize_plain
from app.import_35b.model_boundary import has_hard_control_chars
from app.import_35b.payload_heuristics import (
    figure_caption_should_demote,
    should_reroute_aside_left,
    table_looks_like_toc,
)
from app.import_35b.schemas import (
    BlueprintBlock,
    BlockContent,
    PageBlueprint,
    TextPayload,
)


# A text segment whose raw is a blank line.  ``page_compiler._escape_text``
# turns ``\n\n`` into a ``</p><p>`` boundary, so inserting one of these
# between paragraphs keeps each source paragraph in its own ``<p>`` and
# guarantees a math span never spans a paragraph break (the p10/p11/p18
# "stretched formula / no segmentation" bug).
_PARA_BREAK_SEGMENT = {"kind": "text", "raw": "\n\n", "latex": None, "valid": False}


def _segment_is_damaged(seg: dict, *, math_only: bool) -> bool:
    """控制字符门：防腐层没能确定性还原的损伤不得进入 data-latex。

    ``\\x08/\\x0c/\\x0b`` 是明确的 JSON 转义残骸；**行内** math 段里的
    ``\\t``/``\\n`` 同样是损伤信号（``90^\\text{o}`` 被吃成
    ``90^<tab>ext{o}`` 后 MathJax 仍可能“成功”渲染出错误内容，必须在
    这里拦下走 fallback）。equation 块（math_only）的多行 LaTeX 里换行
    是合法空白，不检查。
    """
    if seg.get("kind") != "math":
        return False
    for value in (seg.get("raw") or "", seg.get("latex") or ""):
        if has_hard_control_chars(value):
            return True
        if not math_only and ("\t" in value or "\n" in value):
            return True
    return False


def _segments_for_text(text: str, *, math_only: bool = False) -> list[dict]:
    rich = normalize_math_only(text) if math_only else normalize_plain(text)
    segments = [s.model_dump(mode="json") for s in rich.segments]
    for seg in segments:
        if seg.get("valid") and _segment_is_damaged(seg, math_only=math_only):
            seg["valid"] = False
            seg["latex"] = None
    return segments


def _paragraph_needs_math(text: str) -> bool:
    return bool(text) and ("$" in text or looks_like_math(text))


def _segments_for_paragraphs(paragraphs: list[str]) -> list[dict]:
    """Segment each paragraph independently and rejoin with paragraph breaks.

    Segmenting per paragraph (instead of over the whole block) means CJK/math
    splitting can never merge ``(1)(2)(3)`` sub-items across a paragraph
    boundary into one oversized inline-math span.
    """
    segments: list[dict] = []
    for idx, para in enumerate(paragraphs):
        if idx > 0:
            segments.append(dict(_PARA_BREAK_SEGMENT))
        if _paragraph_needs_math(para):
            segments.extend(_segments_for_text(para))
        else:
            segments.append({"kind": "text", "raw": para, "latex": None, "valid": False})
    return segments


def _hoist_figure_caption_to_text(block: BlueprintBlock) -> BlueprintBlock:
    """kind=text with empty text but figure.caption → text.paragraphs (p3)."""
    if block.kind != "text":
        return block
    content = block.content
    if content.text_paragraphs():
        return block
    cap = (content.figure_caption() or "").strip()
    if not cap:
        return block
    new_content = content.model_copy(
        update={
            "text": TextPayload(paragraphs=[cap]),
            "figure": None,
            "caption": None,
        }
    )
    return block.model_copy(update={"content": new_content})


def _demote_bogus_figure(block: BlueprintBlock) -> BlueprintBlock:
    """figure + body/TOC caption → kind=text (p6 mis-label)."""
    if block.kind != "figure":
        return block
    cap = (block.content.figure_caption() or "").strip()
    if not figure_caption_should_demote(cap):
        return block
    new_content = BlockContent(text=TextPayload(paragraphs=[cap]))
    return block.model_copy(
        update={
            "kind": "text",
            "role": "body" if block.role == "figure" else block.role,
            "content": new_content,
            "figure_group_id": None,
        }
    )


def _retarget_toc_table(block: BlueprintBlock) -> BlueprintBlock:
    if block.kind != "table":
        return block
    if block.role == "toc":
        return block
    rows = block.content.table_rows()
    if not table_looks_like_toc(rows):
        return block
    return block.model_copy(update={"role": "toc"})


def _maybe_aside_left(block: BlueprintBlock) -> BlueprintBlock:
    if not should_reroute_aside_left(list(block.bbox or []), zone=block.zone):
        return block
    role = block.role if block.role in ("note", "reading", "symbol_note", "think") else "note"
    return block.model_copy(update={"zone": "aside_left", "role": role})


def normalize_block_content(block: BlueprintBlock) -> BlueprintBlock:
    block = _hoist_figure_caption_to_text(block)
    block = _demote_bogus_figure(block)
    block = _retarget_toc_table(block)
    block = _maybe_aside_left(block)
    content = block.content

    # Equation blocks are one math expression: never split on CJK/paragraphs so
    # multi-branch notation (piecewise / cases) survives intact.  The rendered
    # LaTeX comes from ``equation_latex()`` directly; segments are fallback only.
    if block.kind == "equation":
        text = content.equation_latex() or content.equation_fallback_text()
        if not text.strip():
            return block
        text_segments = _segments_for_text(text, math_only=True)
        return block.model_copy(
            update={"content": content.model_copy(update={"text_segments": text_segments})}
        )

    paragraphs = content.text_paragraphs()
    if not paragraphs:
        return block
    # Only build rich segments when at least one paragraph carries math; plain
    # prose is rendered straight from ``text_paragraphs()`` by the compiler.
    if not any(_paragraph_needs_math(p) for p in paragraphs):
        return block

    text_segments = _segments_for_paragraphs(paragraphs)
    return block.model_copy(
        update={"content": content.model_copy(update={"text_segments": text_segments})}
    )


def normalize_blueprint(blueprint: PageBlueprint) -> PageBlueprint:
    return blueprint.model_copy(
        update={"blocks": [normalize_block_content(block) for block in blueprint.blocks]}
    )

