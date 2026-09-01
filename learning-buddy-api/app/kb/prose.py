"""Polish stored knowledge-base markdown the way the textbook studio treats prose.

Captions, headings, numbered items, and leftover formula screenshots — no
layout columns and no studio imports.
"""

from __future__ import annotations

import re
from html import escape

from app.kb.math_text import wrap_bare_math
from app.kb.toc import looks_like_toc_blob

_SPAN = re.compile(
    r"""\{\s*['"]type['"]\s*:\s*['"][^'"]+['"]\s*,\s*['"]content['"]\s*:\s*['"]([\s\S]+?)['"]\s*\}"""
)
_HEADING = re.compile(
    r"^(?:第[一二三四五六七八九十百零\d]+章.*|内容提要|复习题"
    r"|\d+\.\d+\s+\S.*"
    r"|例\s*\d+.*"
    r"|练习\s*[\d.()]+.*"
    r"|定理\s*\d+.*"
    r"|\d+\s+[\u4e00-\u9fff]{2,16})$"
)
# Do not treat f(3) or 图1-1-4(1) as list markers.
_MARKER = re.compile(r"(?<![\w.\-])[（(]\s*([0-9０-９]+)\s*[）)]")
_FULLWIDTH_DIGIT = str.maketrans("０１２３４５６７８９", "0123456789")
_FORMULA_NEXT = r"(?:\\[a-zA-Z{]|[_^]\s*\{)"
_SHORT_FORMULA_ALT = re.compile(r"^[\[\(][^\[\]\(\)]{0,16}[\]\)]$")
_CHAPTER_TITLE = re.compile(r"^[\u4e00-\u9fff、]{2,20}$")
_LEAD = re.compile(r"^((?:[\u4e00-\u9fff]{1,6})?定理|定义|证明|探究与实践)(?:\s+|$)")
_SOLUTION = re.compile(r"^解(?:[\s\u3000]+|(?=[（(]))(.*)$")
_EX_ITEM = re.compile(r"^\d+\.\s+[\u4e00-\u9fff]")


def _spans_in(text: str) -> list[str]:
    return [m.group(1).strip() for m in _SPAN.finditer(text or "") if m.group(1).strip()]


def _safe_alt(text: str) -> str:
    t = (text or "").strip() or "插图"
    if t.startswith(("图", "表")):
        return t.replace("]", "］")
    if _SHORT_FORMULA_ALT.fullmatch(t):
        return (
            t.replace("[", "［")
            .replace("]", "］")
            .replace("(", "（")
            .replace(")", "）")
        )
    return t.replace("]", "］")


def _pick_caption(alt: str) -> str:
    spans = _spans_in(alt)
    if not spans:
        return _safe_alt(alt)
    for span in spans:
        if re.match(r"图\s*\d", span) or re.match(r"表\s*\d", span):
            return span.replace("]", "］")
    words = [t for t in spans if not re.match(r"^[\[\(\$]", t) and len(t) > 2]
    return _safe_alt(words[0] if words else spans[-1])


def fix_mineru_captions(md: str) -> str:
    text = md or ""

    def _alt(match: re.Match[str]) -> str:
        alt, url = match.group(1), match.group(2)
        if "{" not in alt:
            return f"![{_safe_alt(alt) if '[' in alt or alt.endswith(']') else alt}]({url})"
        return f"![{_pick_caption(alt)}]({url})"

    text = re.sub(r"!\[([\s\S]*?)\]\(([^)]+)\)", _alt, text)
    text = re.sub(
        r"(!\[[^\]]+\]\([^)]+\))\s*\n+\*\{\s*['\"]type['\"][\s\S]{0,200}?\*",
        r"\1",
        text,
    )
    text = _SPAN.sub(r"\1", text)

    def _dup_italic(match: re.Match[str]) -> str:
        img, alt, inner = match.group(1), match.group(2), match.group(3)
        if alt in inner or re.search(r"图\s*\d", inner or ""):
            return img
        return match.group(0)

    text = re.sub(
        r"(!\[([^\]]+)\]\([^)]+\))\s*\n+\*([\s\S]{0,120}?)\*",
        _dup_italic,
        text,
    )
    return restore_visible_captions(text)


def drop_noise_lines(md: str) -> str:
    text = re.sub(r"^\s*#+\s*$", "", md or "", flags=re.M)
    text = re.sub(r"^\s*第\s*$", "", text, flags=re.M)
    text = re.sub(r"^\s*[?？]\s*$", "", text, flags=re.M)
    return re.sub(r"^\s*例\s*$", "", text, flags=re.M)


def drop_formula_shots(md: str) -> str:
    def _shot(match: re.Match[str]) -> str:
        alt = (match.group(1) or "").strip()
        return match.group(0) if alt.startswith("图") else ""

    return re.sub(
        rf"!\[([^\]]*)\]\([^)]+\)[ \t]*\n+(?=[^\n]*{_FORMULA_NEXT})",
        _shot,
        md or "",
    )


_SKIP_VISIBLE_CAPTION = frozenset({"插图", "公式", "表格"})


def restore_visible_captions(md: str) -> str:
    """Image alt is not shown as text — put 图/人物/区间图注 back under the picture."""

    def _cap(match: re.Match[str]) -> str:
        alt = (match.group(1) or "").strip()
        if not alt or alt in _SKIP_VISIBLE_CAPTION:
            return match.group(0)
        return f"{match.group(0)}\n\n*{alt}*"

    return re.sub(
        r"!\[([^\]]+)\]\(([^)]+)\)(?!\s*\n+\*)",
        _cap,
        md or "",
    )


def looks_like_heading(text: str) -> bool:
    t = (text or "").strip()
    if not t or t.startswith(("#", "<")):
        return False
    if t.startswith(("例", "练习", "定理")):
        return len(t) <= 56 and bool(_HEADING.match(t))
    return len(t) <= 40 and bool(_HEADING.match(t))


def lift_headings(md: str) -> str:
    lines = (md or "").split("\n")
    out: list[str] = []
    prev_img = False
    for line in lines:
        t = line.strip()
        if looks_like_heading(line):
            out.append(f"### {t}")
            prev_img = False
            continue
        if prev_img and _CHAPTER_TITLE.match(t) and not looks_like_heading(t):
            out.append(f"### {t}")
            prev_img = False
            continue
        out.append(line)
        if t:
            prev_img = t.startswith("![")
    return "\n".join(out)


def _unify_marker(text: str) -> str:
    def _mark(match: re.Match[str]) -> str:
        return f"（{match.group(1).translate(_FULLWIDTH_DIGIT)}）"

    return re.sub(r"^[（(]\s*([0-9０-９]+)\s*[）)]\s*", _mark, text)


def _has_packed_markers(text: str) -> bool:
    matches = list(_MARKER.finditer(text))
    return any(not text[a.end() : b.start()].strip() for a, b in zip(matches, matches[1:]))


def _split_numbered(text: str) -> list[str] | None:
    matches = list(_MARKER.finditer(text))
    if len(matches) < 2 or _has_packed_markers(text):
        return None
    chunks = [
        text[m.start() : (matches[i + 1].start() if i + 1 < len(matches) else len(text))].strip()
        for i, m in enumerate(matches)
    ]
    rich = [c for c in chunks if len(_MARKER.sub("", c).strip()) >= 4]
    if len(rich) < 2:
        return None
    prefix = text[: matches[0].start()].strip()
    if prefix and prefix != "解":
        return [prefix, *chunks]
    return chunks


def _item_html(text: str, cls: str) -> str:
    return f'<p class="{cls}">{wrap_bare_math(escape(text))}</p>'


def polish_lists(md: str) -> str:
    out: list[str] = []
    for raw in (md or "").split("\n"):
        trimmed = raw.strip()
        if not trimmed or trimmed.startswith(("<", "!", "#")):
            out.append(raw)
            continue
        body = trimmed
        solution = False
        sol = _SOLUTION.match(body)
        if sol and not body.startswith("解题"):
            solution = True
            body = sol.group(1).strip()
        chunks = _split_numbered(body)
        if solution:
            out.append('<p class="kb-solution"><span class="kb-solution-label">解</span></p>')
        if chunks:
            out.extend(_item_html(_unify_marker(chunk), "kb-list-item") for chunk in chunks)
            continue
        if re.match(r"^[（(]\s*[0-9０-９]+\s*[）)]", body) and not _has_packed_markers(body):
            out.append(_item_html(_unify_marker(body), "kb-list-item"))
            continue
        if _EX_ITEM.match(body) and not re.match(r"^\d+\.\d+", body):
            out.append(_item_html(body, "kb-list-item"))
            continue
        if solution and body:
            out.append(_item_html(body, "kb-solution-tail"))
            continue
        out.append(raw)
    return "\n".join(out)


def polish_leads(md: str) -> str:
    out: list[str] = []
    for raw in (md or "").split("\n"):
        trimmed = raw.strip()
        if not trimmed or trimmed.startswith(("<", "!", "#")):
            out.append(raw)
            continue
        match = _LEAD.match(trimmed)
        if not match:
            out.append(raw)
            continue
        rest = trimmed[match.end() :].strip()
        label = match.group(1)
        if rest:
            out.append(
                f'<p class="kb-lead"><span class="kb-lead-label">{escape(label)}</span> '
                f"{wrap_bare_math(escape(rest))}</p>"
            )
        else:
            out.append(f'<p class="kb-lead"><span class="kb-lead-label">{escape(label)}</span></p>')
    return "\n".join(out)


_HASH_LINE = re.compile(r"^#{1,6}\s+", re.M)


def unhash_lines(md: str) -> str:
    return _HASH_LINE.sub("", md or "")


def polish_page_prose(md: str) -> str:
    """Official markdown only: captions, noise, headings. No kb-* HTML."""
    cleaned = drop_formula_shots(drop_noise_lines(fix_mineru_captions(md)))
    plain = unhash_lines(cleaned)
    if looks_like_toc_blob(plain):
        return plain
    return lift_headings(cleaned)
