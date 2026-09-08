"""Official page markdown must not carry mid-layer fence tokens.

Library only — not in manifest.yaml. No ``app.*`` imports.

``pages/*.md`` is the official source. ``::: kb-*`` fences live in ``mid/``.
When mid is written back, peel every well-formed fence *and* the torn leftovers
(`` kb-center``, stray ``:::``, glued ``（2）::: kb-center``).
"""
from __future__ import annotations

import re
from pathlib import Path

FENCE_NAMES = frozenset(
    {
        "kb-center",
        "kb-title",
        "kb-subtitle",
        "kb-author",
        "kb-section",
        "kb-section-center",
        "kb-verse",
        "kb-verse-center",
        "kb-date",
        "kb-list-item",
        "kb-lead",
        "kb-solution",
        "kb-solution-tail",
        "kb-page-toc",
        "kb-table-wrap",
        "kb-section-icon",
        "kb-drama",
        "kb-stage",
        "kb-dash",
    }
)

_FENCE = re.compile(r"::: (kb-[\w-]+)\r?\n([\s\S]*?)\r?\n:::")
_OPENER = re.compile(r"::: kb-[\w-]+")
_NAME = "|".join(sorted(FENCE_NAMES, key=len, reverse=True))
_NAME_LINE = re.compile(rf"^[ \t]*(?::: )?({_NAME})[ \t]*$")
_NAME_TOKEN = re.compile(rf"(?:^|(?<=\s))({_NAME})(?=\s|[^\w-]|$)")
_OPEN_LINE = re.compile(rf"^[ \t]*::: ({_NAME})[ \t]*$")
_CLOSER = re.compile(r"^[ \t]*:::[ \t]*$")
_HOLD = re.compile(r"\x00F(\d+)\x00")
_SKIN_HTML = re.compile(
    r'class="kb-(?!table)[^"]*"|</?nav\b[^>]*kb-page-toc|</?ol\b[^>]*kb-page-toc',
    re.I,
)


def detach_glued_markers(text: str) -> str:
    """Put ``::: kb-*`` / ``:::`` on their own line when glued to prose."""
    out = text or ""
    out = re.sub(r"(?<=\S)(::: kb-[A-Za-z0-9_-]+)", r"\n\1", out)
    out = re.sub(r"(::: kb-[A-Za-z0-9_-]+)(?=[^A-Za-z0-9_\s-])", r"\1\n", out)
    out = re.sub(r"^(:::)(?=\S)", r"\1\n", out, flags=re.M)
    out = re.sub(r"(?<=\S)(:::)[ \t]*$", r"\n\1", out, flags=re.M)
    return out


def strip_well_formed_fences(text: str) -> str:
    out = text or ""
    prev = None
    while prev != out:
        prev = out
        out = _FENCE.sub(lambda m: (m.group(2) or "").strip(), out)
    return out


def strip_fence_tokens(text: str) -> str:
    """Drop leftover openers, closers, and bare fence names. Keep real prose."""
    lines: list[str] = []
    for line in (text or "").splitlines():
        if _CLOSER.match(line) or _NAME_LINE.match(line):
            continue
        cleaned = _NAME_TOKEN.sub("", _OPENER.sub("", line))
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).rstrip()
        if not cleaned.strip() and (_OPENER.search(line) or _NAME_TOKEN.search(line)):
            continue
        lines.append(cleaned)
    body = "\n".join(lines)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip() + ("\n" if body.strip() else "")


def sanitize_source(text: str) -> str:
    """Official ``pages/*.md`` body: no fences, no torn remnants."""
    return strip_fence_tokens(strip_well_formed_fences(detach_glued_markers(text or "")))


def write_source_atomic(path: Path, text: str) -> bool:
    """Write official page markdown after peeling fence tokens. True if changed."""
    after = sanitize_source(text)
    before = path.read_text(encoding="utf-8") if path.is_file() else None
    if before == after:
        return False
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(after, encoding="utf-8")
    tmp.replace(path)
    return True


def _fence_body_dirty(body: str) -> bool:
    raw = body or ""
    if _OPENER.search(raw) or _NAME_TOKEN.search(raw):
        return True
    return any(_CLOSER.match(line) or _NAME_LINE.match(line) for line in raw.splitlines())


def sanitize_mid(text: str) -> str:
    """Keep well-formed fences; peel remnants and unwrap nested/torn inners."""
    held: list[str] = []

    def _keep(match: re.Match[str]) -> str:
        name = match.group(1)
        body = match.group(2) or ""
        if _fence_body_dirty(body):
            inner = sanitize_source(body)
            held.append(f"::: {name}\n{inner.rstrip()}\n:::")
        else:
            held.append(match.group(0))
        return f"\x00F{len(held) - 1}\x00"

    masked = _FENCE.sub(_keep, detach_glued_markers(text or ""))
    cleaned = strip_fence_tokens(masked)

    def _restore(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        return held[idx] if 0 <= idx < len(held) else ""

    return _HOLD.sub(_restore, cleaned)


def source_is_polluted(text: str) -> bool:
    raw = text or ""
    if _FENCE.search(raw) or _OPENER.search(raw) or _NAME_TOKEN.search(raw):
        return True
    return any(_CLOSER.match(line) or _NAME_LINE.match(line) for line in raw.splitlines())


def audit_source(text: str) -> list[dict[str, object]]:
    """Official ``pages/``: any fence token is a defect."""
    issues: list[dict[str, object]] = []
    for i, line in enumerate((text or "").splitlines(), 1):
        excerpt = line.strip()[:100]
        if _OPENER.search(line):
            issues.append({"kind": "fence", "line": i, "excerpt": excerpt})
        elif _CLOSER.match(line):
            issues.append({"kind": "orphan_closer", "line": i, "excerpt": excerpt})
        elif _NAME_LINE.match(line):
            issues.append({"kind": "remnant_name", "line": i, "excerpt": excerpt})
        elif _NAME_TOKEN.search(line):
            issues.append({"kind": "glued_name", "line": i, "excerpt": excerpt})
    return issues


def audit_mid(text: str) -> list[dict[str, object]]:
    """Mid-layer fence grammar: nest, unclosed, leftover names, skin HTML."""
    issues: list[dict[str, object]] = []
    raw = text or ""
    if _SKIN_HTML.search(raw):
        issues.append({"kind": "skin_html", "line": 0, "excerpt": 'class="kb-'})
    stack: list[tuple[str, int]] = []
    for i, line in enumerate(raw.splitlines(), 1):
        excerpt = line.strip()[:100]
        opened = _OPEN_LINE.match(line)
        if opened:
            if stack:
                issues.append({"kind": "nested", "line": i, "excerpt": excerpt})
            stack.append((opened.group(1), i))
            continue
        if _CLOSER.match(line):
            if stack:
                stack.pop()
            else:
                issues.append({"kind": "orphan_closer", "line": i, "excerpt": excerpt})
            continue
        if _OPENER.search(line):
            issues.append({"kind": "glued_opener", "line": i, "excerpt": excerpt})
        elif _NAME_LINE.match(line):
            issues.append({"kind": "remnant_name", "line": i, "excerpt": excerpt})
        elif _NAME_TOKEN.search(line):
            issues.append({"kind": "glued_name", "line": i, "excerpt": excerpt})
    for name, i in stack:
        issues.append({"kind": "unclosed", "line": i, "excerpt": f"::: {name}"})
    return issues


def mid_is_polluted(text: str) -> bool:
    return sanitize_mid(text or "") != (text or "")


def clean_textbook_tree(root: Path) -> dict[str, int]:
    """Rewrite polluted ``pages/`` and ``mid/`` under a textbooks root."""
    pages = 0
    mids = 0
    books = 0
    if not root.is_dir():
        return {"books": 0, "pages": 0, "mids": 0}
    for book in sorted(p for p in root.iterdir() if p.is_dir()):
        page_dir = book / "pages"
        mid_dir = book / "mid"
        touched = False
        if page_dir.is_dir():
            for path in sorted(page_dir.glob("[0-9][0-9][0-9][0-9].md")):
                before = path.read_text(encoding="utf-8")
                after = sanitize_source(before)
                if after != before:
                    path.write_text(after, encoding="utf-8")
                    pages += 1
                    touched = True
        if mid_dir.is_dir():
            for path in sorted(mid_dir.glob("[0-9][0-9][0-9][0-9].md")):
                before = path.read_text(encoding="utf-8")
                after = sanitize_mid(before)
                if after != before:
                    path.write_text(after, encoding="utf-8")
                    mids += 1
                    touched = True
        if touched:
            books += 1
    return {"books": books, "pages": pages, "mids": mids}
