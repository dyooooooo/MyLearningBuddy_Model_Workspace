"""Compile 35B PageBlueprints into deterministic Studio HTML."""
from __future__ import annotations

import re
from html import escape

from app.import_35b.render_zone import render_zone_grid_html
from app.import_35b.schemas import BlueprintBlock, PageBlueprint
from app.import_35b.ssr_math import ssr_typeset_page
from app.math.normalize import _bridge_available, _call_bridge


def _math_span(latex: str, *, display: bool = False) -> str:
    cls = "math math--display" if display else "math math--inline"
    return f'<span class="{cls}" data-latex="{escape(latex.strip())}"></span>'


def _latex_renderable(latex: str) -> bool:
    """Server-side MathJax validation gate for a LaTeX string.

    Prevents emitting a ``data-latex`` span that the client MathJax will fail to
    render (raw garbage / MFR degeneration on-screen). When the node bridge is
    unavailable we cannot validate, so we defer to the client (prior behavior).
    """
    latex = (latex or "").strip()
    if not latex:
        return False
    if not _bridge_available():
        return True
    return bool(_call_bridge(latex, "latex").get("valid"))


# Step C — when a segment cannot be rendered as math, show a *human-readable*
# fallback (unicode) rather than raw LaTeX tokens like ``\cap`` / ``\mid``.
_LATEX_TO_UNICODE = [
    (r"\notin", "∉"), (r"\varnothing", "∅"), (r"\emptyset", "∅"),
    (r"\subseteq", "⊆"), (r"\supseteq", "⊇"), (r"\subset", "⊂"), (r"\supset", "⊃"),
    (r"\cap", "∩"), (r"\cup", "∪"), (r"\in", "∈"), (r"\mid", "|"),
    (r"\leq", "≤"), (r"\geq", "≥"), (r"\neq", "≠"), (r"\times", "×"),
    (r"\pm", "±"), (r"\to", "→"), (r"\infty", "∞"), (r"\cdot", "·"),
    (r"\{", "{"), (r"\}", "}"), (r"\,", " "), (r"\ ", " "),
    (r"\left", ""), (r"\right", ""), (r"\overline", ""), (r"\text", ""),
]
_LEFTOVER_CMD_RE = re.compile(r"\\[a-zA-Z]+")


def _delatex(raw: str) -> str:
    out = raw.strip()
    # Protect literal braces (``\{`` / ``\}``) so the grouping-brace cleanup
    # below does not eat them.
    out = out.replace(r"\{", "\x00L\x00").replace(r"\}", "\x00R\x00")
    for tok, uni in _LATEX_TO_UNICODE:
        if tok in ("\\{", "\\}"):
            continue
        out = out.replace(tok, uni)
    out = _LEFTOVER_CMD_RE.sub("", out)
    out = out.replace("{", "").replace("}", "").replace("$", "")
    out = out.replace("\x00L\x00", "{").replace("\x00R\x00", "}")
    return re.sub(r"\s+", " ", out).strip()


def _fallback_math(raw: str) -> str:
    return f'<span class="math math--fallback">{escape(_delatex(raw))}</span>'


def _escape_text(raw: str) -> str:
    """Escape plain text while preserving in-paragraph line breaks.

    Blank lines become paragraph boundaries; single newlines become ``<br>``
    so the source line/paragraph structure survives into the rendered page.
    """
    esc = escape(raw)
    esc = re.sub(r"\n\s*\n", "</p><p>", esc)
    esc = esc.replace("\n", "<br>")
    return esc


def _render_segments(segments: list[dict]) -> str:
    parts: list[str] = []
    for seg in segments:
        raw = str(seg.get("raw") or "")
        if seg.get("kind") == "math":
            latex = seg.get("latex")
            if seg.get("valid") and latex:
                parts.append(_math_span(str(latex)))
            else:
                parts.append(_fallback_math(raw))
        else:
            parts.append(_escape_text(raw))
    return "".join(parts)


def _render_rich_inline(text: str) -> str:
    """v4 统一富文本管线：任何承载正文的字符串（表格 cell、caption、
    callout 文本）都走与段落相同的 ``$...$`` 分段 + 校验 + 渲染路径。

    表 1-2 类「单元格裸 $ 公式」的根源是存在第二条只 escape 的文本路径；
    收敛到这里之后，新增内容类型自动获得公式能力。
    """
    from app.import_35b.normalize import _paragraph_needs_math, _segments_for_text

    t = (text or "").strip()
    if not t:
        return ""
    if _paragraph_needs_math(t):
        return _render_segments(_segments_for_text(t))
    return _escape_text(t)


_EMPTY_P_RE = re.compile(r"<p>(?:\s|<br>)*</p>")


def _render_text_block(block: BlueprintBlock) -> str:
    content = block.content
    if content.text_segments:
        body = _render_segments(content.text_segments)
        if not body.strip():
            return ""
        # Blank-line segments become ``</p><p>`` boundaries so each source
        # paragraph lands in its own <p>; drop any empties this produces.
        return _EMPTY_P_RE.sub("", f"<p>{body}</p>")
    parts = []
    for para in content.text_paragraphs():
        body = _escape_text(para)
        if body.strip():
            parts.append(f"<p>{body}</p>")
    if parts:
        return "".join(parts)
    body = _escape_text(content.text_plain())
    if body.strip():
        return f"<p>{body}</p>"
    # p3: kind=text but prose landed in figure.caption — render as paragraph
    cap = (content.figure_caption() or "").strip()
    if cap:
        return f"<p>{_render_rich_inline(cap)}</p>"
    return ""


def _render_equation(block: BlueprintBlock) -> str:
    content = block.content
    latex = content.equation_latex()
    if latex and _latex_renderable(latex):
        body = _math_span(latex, display=True)
    elif content.text_segments:
        # normalize already validated these segments (valid → span, else fallback).
        body = _render_segments(content.text_segments)
    else:
        # Never dump raw/broken LaTeX on-screen: show a human-readable fallback.
        fb = content.equation_fallback_text()
        body = escape(_delatex(fb)) if fb else ""
    if not body.strip():
        return ""
    return f'<div class="lb-equation">{body}</div>'


def _render_figure(block: BlueprintBlock) -> str:
    from app.import_35b.payload_heuristics import figure_caption_should_demote

    cap = block.content.figure_caption()
    src = block.content.asset_url or ""
    # 正文/目录误标为 figure（有无 asset 都降级为 <p>，不产出假图）
    if cap and figure_caption_should_demote(cap):
        return f"<p>{_render_rich_inline(cap)}</p>"
    alt = escape(cap or "插图")
    img = f'<img src="{escape(src)}" alt="{alt}" />' if src else ""
    cap_html = f"<figcaption>{_render_rich_inline(cap)}</figcaption>" if cap else ""
    gid = block.figure_group_id
    gid_attr = f' data-figure-group="{escape(gid)}"' if gid else ""
    return f'<figure class="lb-fig-item"{gid_attr}>{img}{cap_html}</figure>'


def _render_toc(block: BlueprintBlock) -> str:
    """Render a table-of-contents block as a dot-leader list, not a <table>."""
    rows = block.content.table_rows()
    items: list[str] = []
    for row in rows:
        cells = [str(c).strip() for c in row]
        page = ""
        if cells and re.fullmatch(r"[0-9０-９]{1,4}", cells[-1]):
            page = cells[-1]
            cells = cells[:-1]
        title = " ".join(c for c in cells if c).strip()
        if not title and not page:
            continue
        is_chapter = bool(re.match(r"^第\s*[0-9０-９一二三四五六七八九十]+\s*章", title))
        row_cls = "lb-toc-row lb-toc-row--chapter" if is_chapter else "lb-toc-row"
        page_html = f'<span class="lb-toc-page">{escape(page)}</span>' if page else ""
        items.append(
            f'<li class="{row_cls}"><span class="lb-toc-title">{escape(title)}</span>{page_html}</li>'
        )
    if not items:
        return ""
    return f'<ol class="lb-toc">{"".join(items)}</ol>'


def _render_table(block: BlueprintBlock) -> str:
    from app.import_35b.payload_heuristics import table_looks_like_toc

    rows = block.content.table_rows()
    if block.role == "toc" or table_looks_like_toc(rows):
        toc = _render_toc(block)
        if toc:
            return toc
    if rows:
        body_rows = []
        for row in rows:
            cells = "".join(f"<td>{_render_rich_inline(str(cell))}</td>" for cell in row)
            if cells:
                body_rows.append(f"<tr>{cells}</tr>")
        caption = block.content.table_caption()
        cap = f"<caption>{_render_rich_inline(caption)}</caption>" if caption else ""
        table = f"<table>{cap}<tbody>{''.join(body_rows)}</tbody></table>"
        return f'<div class="lb-table-wrap">{table}</div>'
    text = block.content.text_plain()
    return f'<div class="lb-table-wrap"><p>{_render_rich_inline(text)}</p></div>' if text else ""


def _callout_class(role: str) -> str:
    if role == "think":
        return "lb-callout lb-callout--think"
    if role == "prompt":
        return "lb-callout lb-callout--prompt"
    if role in ("note", "symbol_note"):
        return "lb-callout lb-callout--note"
    return "lb-callout"


def _render_block(block: BlueprintBlock) -> str:
    if block.kind == "running" or block.zone == "running":
        return ""
    content = block.content
    if block.kind == "figure":
        body = _render_figure(block)
    elif block.kind == "table":
        body = _render_table(block)
    elif block.kind == "equation":
        body = _render_equation(block)
    else:
        body = _render_text_block(block)
    if not body.strip():
        return ""

    bid = f' data-block-id="{escape(block.id)}"'
    anchor = f' data-anchor="{escape(block.anchor_id)}"' if block.anchor_id else ""
    if block.zone in ("aside_left", "aside_right") and block.role != "body":
        label = {"think": "思考", "note": "注释", "prompt": "提示", "symbol_note": "注释"}.get(
            block.role, ""
        )
        label_html = f'<span class="lb-callout-label">{label}</span>' if label else ""
        return f'<div class="{_callout_class(block.role)}"{bid}{anchor}>{label_html}{body}</div>'
    cls = "lb-block lb-full-bleed" if block.zone == "full_bleed" else "lb-block"
    return f'<div class="{cls}"{bid}{anchor}>{body}</div>'


_HEADER_MAX_CHARS = 64


def _is_short_header_text(block: BlueprintBlock) -> bool:
    """``role=running_header`` 但 zone 不是 running 时的兜底判定。

    v4 只信 zone/kind；role 是模型标注、会误标（p3 版权页 6 块全标
    running_header 导致整页丢失）。只有「单段短文本」才可能是真页眉，
    其余一律回归原 zone 渲染，绝不吞块。
    """
    if block.kind != "text":
        return False
    paras = [p for p in block.content.text_paragraphs() if p.strip()]
    if len(paras) != 1:
        return False
    return len(paras[0].strip()) <= _HEADER_MAX_CHARS


def compile_page_html(
    blueprint: PageBlueprint,
    *,
    profile_id: str = "hujiao_math_hs_v1",
    ssr_report: list[dict] | None = None,
) -> str:
    by_zone: dict[str, list[str]] = {
        "aside_left": [],
        "main": [],
        "aside_right": [],
        "full_bleed": [],
    }
    running_header = ""

    for block in blueprint.sorted_blocks():
        is_running_zone = block.zone == "running" or block.kind == "running"
        if is_running_zone or (block.role == "running_header" and _is_short_header_text(block)):
            running_header = running_header or (
                block.content.text_plain()
                or block.content.figure_caption()
                or block.content.table_caption()
            )[:_HEADER_MAX_CHARS]
            continue
        html = _render_block(block)
        if not html.strip():
            continue
        zone = block.zone if block.zone in by_zone else "main"
        by_zone[zone].append(html)

    html = render_zone_grid_html(
        page_no=blueprint.page_no,
        profile_id=profile_id,
        header=running_header,
        by_zone=by_zone,
    )
    # v4 SSR：sanitize 之后做（SVG 不过 bleach 白名单）；SVG 由本服务的
    # MathJax 桥确定性生成，非模型输入，安全由构造保证。
    return ssr_typeset_page(html, report=ssr_report)

