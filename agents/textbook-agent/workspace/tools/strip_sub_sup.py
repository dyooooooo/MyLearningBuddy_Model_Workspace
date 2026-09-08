from __future__ import annotations

import re
from pathlib import Path

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")

# Tag tokens from import: HTML, spaced HTML, \lt...\gt, entities (case-insensitive).
_TAG_TOKENS = re.compile(
    r"(?:"
    r"<\s*/?\s*(?:sub|sup)\s*>"
    r"|\\lt\s*/?\s*(?:sub|sup)\s*\\gt"
    r"|&lt;\s*/?\s*(?:sub|sup)\s*&gt;"
    r")",
    re.I,
)

# Same patterns for scan hit detection.
_HIT_PATTERN = re.compile(
    r"(?:"
    r"<\s*/?\s*(?:sub|sup)\s*>"
    r"|\\lt\s*/?\s*(?:sub|sup)\s*\\gt"
    r"|&lt;\s*/?\s*(?:sub|sup)\s*&gt;"
    r")",
    re.I,
)

_PUNCT_IN_MATH = re.compile(r"\$\s*([．。.,，、；;：:!！?？])\s*\$")
_EMPTY_INLINE = re.compile(r"\$\s*\$")
_EMPTY_DISPLAY = re.compile(r"\$\$\s*\$\$")
_CJK_IN_MATH = re.compile(r"\$\s*([\u4e00-\u9fff])\s*\$")
_DIGIT_IN_MATH = re.compile(r"\$\s*(\d+)\s*\$")
_SPLIT_PART_HEADER = re.compile(r"第部\s*(\d+)\s*分")
_LETTER_FRAGMENTS_IN_MATH = re.compile(r"\$\s*([A-Za-z]+(?:\s+[A-Za-z]+)+)\s*\$")
_TRAILING_DOLLAR = re.compile(r"\$\s*$")


def strip_sub_sup_tags(text: str) -> str:
    """Remove sub/sup import tag tokens; keep sandwiched text unchanged."""
    out = text
    prev = None
    while prev != out:
        prev = out
        out = _TAG_TOKENS.sub("", out)
    return _cleanup_fragments(out)


def _cleanup_fragments(text: str) -> str:
    out = text
    out = _PUNCT_IN_MATH.sub(r"\1", out)
    out = _EMPTY_INLINE.sub("", out)
    out = _EMPTY_DISPLAY.sub("", out)
    out = _CJK_IN_MATH.sub(r"\1", out)
    out = _LETTER_FRAGMENTS_IN_MATH.sub(
        lambda m: "$" + m.group(1).replace(" ", "") + "$", out
    )

    lines: list[str] = []
    for line in out.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            line = _DIGIT_IN_MATH.sub(r"\1", line)
        line = _SPLIT_PART_HEADER.sub(r"第\1部分", line)
        line = _TRAILING_DOLLAR.sub("", line)
        lines.append(line)
    return "\n".join(lines)


def _page_paths(base: Path, book_id: str) -> list[Path]:
    pages_dir = base / "textbooks" / book_id / "pages"
    if not pages_dir.is_dir():
        raise FileNotFoundError(str(pages_dir))
    return sorted(pages_dir.glob("*.md"))


def strip_sub_sup(book_id: str, apply: str = "scan", root: str = "") -> dict[str, object]:
    """Strip sub/sup import residue from all pages of one textbook."""
    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    mode = (apply or "scan").strip().lower()
    if mode not in {"scan", "fix"}:
        raise ValueError("apply must be scan or fix")

    base = Path(root) if root else Path.cwd()
    paths = _page_paths(base, sid)

    pages_with_hits: list[str] = []
    hit_count = 0
    changed_pages = 0

    for path in paths:
        original = path.read_text(encoding="utf-8")
        hits = len(_HIT_PATTERN.findall(original))
        if hits:
            pages_with_hits.append(path.name)
            hit_count += hits

        if mode == "fix":
            cleaned = strip_sub_sup_tags(original)
            if cleaned != original:
                try:
                    from source_text import write_source_atomic
                except ImportError:
                    write_source_atomic = None
                if write_source_atomic is not None:
                    write_source_atomic(path, cleaned)
                else:
                    tmp = path.with_suffix(".md.tmp")
                    tmp.write_text(cleaned, encoding="utf-8")
                    tmp.replace(path)
                changed_pages += 1

    result: dict[str, object] = {
        "book_id": sid,
        "apply": mode,
        "pages_with_hits": pages_with_hits,
        "hit_count": hit_count,
    }
    if mode == "fix":
        result["changed_pages"] = changed_pages
    return result
