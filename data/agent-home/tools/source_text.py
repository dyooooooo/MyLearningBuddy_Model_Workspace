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
_OPENER = re.compile(r"(?<!:)::: kb-[\w-]+")
_DIRECTIVE_LINE = re.compile(
    r"^(?P<marks>:{3,})(?:[ \t]+(?P<named>kb-[\w-]+)|(?P<glued>kb-[\w-]+))?[ \t]*$"
)
_NAME = "|".join(sorted(FENCE_NAMES, key=len, reverse=True))
_NAME_LINE = re.compile(rf"^[ \t]*(?::: )?({_NAME})[ \t]*$")
_NAME_TOKEN = re.compile(rf"(?:^|(?<=\s))({_NAME})(?=\s|[^\w-]|$)")
_CLOSER = re.compile(r"^[ \t]*:{3,}[ \t]*$")
_HOLD = re.compile(r"\x00F(\d+)\x00")
_SKIN_HTML = re.compile(
    r'class="kb-(?!table)[^"]*"|</?nav\b[^>]*kb-page-toc|</?ol\b[^>]*kb-page-toc',
    re.I,
)


def detach_glued_markers(text: str) -> str:
    """Put ``::: kb-*`` / ``:::`` on their own line when glued to prose."""
    out = text or ""
    out = re.sub(r"(?<=\S)(?<!:)(::: kb-[A-Za-z0-9_-]+)", r"\n\1", out)
    out = re.sub(r"(?<!:)(::: kb-[A-Za-z0-9_-]+)(?=[^A-Za-z0-9_\s-])", r"\1\n", out)
    out = re.sub(r"^(:::)(?!:)(?=\S)", r"\1\n", out, flags=re.M)
    out = re.sub(r"(?<=\S)(?<!:)(:::)[ \t]*$", r"\n\1", out, flags=re.M)
    return out


def iter_top_level_fences(text: str) -> list[tuple[str, str, int, int]]:
    """Colon-count walker matching remark-directive: more colons wrap fewer.

    Yields ``(name, inner, start, end)`` for top-level ``::: kb-*`` / ``:::kb-*``
    fences. Nested fences stay inside ``inner``.
    """
    raw = text or ""
    stack: list[dict[str, object]] = []
    found: list[tuple[str, str, int, int]] = []
    pos = 0
    for line in raw.splitlines(keepends=True):
        match = _DIRECTIVE_LINE.match(line.rstrip("\r\n"))
        if match:
            marks = str(match.group("marks") or "")
            name = match.group("named") or match.group("glued")
            if name:
                stack.append(
                    {
                        "marks": marks,
                        "name": name,
                        "start": pos,
                        "inner_start": pos + len(line),
                    }
                )
            elif stack and len(marks) >= len(str(stack[-1]["marks"])):
                top = stack.pop()
                end = pos + len(line.rstrip("\r\n"))
                if not stack:
                    inner_start = int(top["inner_start"])
                    found.append((str(top["name"]), raw[inner_start:pos], int(top["start"]), end))
        pos += len(line)
    return found


def extract_directive_fences(text: str) -> list[tuple[str, str]]:
    return [(name, inner) for name, inner, _start, _end in iter_top_level_fences(text)]


def strip_well_formed_fences(text: str) -> str:
    out = text or ""
    prev = None
    while prev != out:
        prev = out
        fences = iter_top_level_fences(out)
        if not fences:
            out = _FENCE.sub(lambda m: (m.group(2) or "").strip(), out)
            continue
        parts: list[str] = []
        cursor = 0
        for _name, inner, start, end in fences:
            parts.append(out[cursor:start])
            parts.append((inner or "").strip())
            cursor = end
        parts.append(out[cursor:])
        out = "".join(parts)
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


def _opener_marks(raw: str) -> str:
    first = (raw.splitlines() or [""])[0].rstrip("\r")
    match = _DIRECTIVE_LINE.match(first)
    if not match:
        return ":::"
    name = match.group("named") or match.group("glued")
    if not name:
        return ":::"
    return str(match.group("marks") or ":::")


def _rewrite_fence(name: str, body: str, raw: str) -> str:
    marks = _opener_marks(raw)
    return f"{marks} {name}\n{(body or '').rstrip()}\n{marks}"


def sanitize_mid(text: str) -> str:
    """Keep well-formed fences; peel remnants and unwrap nested/torn inners."""
    held: list[str] = []

    def _stash(name: str, body: str, raw: str) -> str:
        if iter_top_level_fences(body):
            held.append(_rewrite_fence(name, sanitize_mid(body), raw))
        elif _fence_body_dirty(body):
            held.append(_rewrite_fence(name, sanitize_source(body), raw))
        else:
            held.append(raw)
        return f"\x00F{len(held) - 1}\x00"

    detached = detach_glued_markers(text or "")
    fences = iter_top_level_fences(detached)
    if fences:
        parts: list[str] = []
        cursor = 0
        for name, body, start, end in fences:
            parts.append(detached[cursor:start])
            parts.append(_stash(name, body, detached[start:end]))
            cursor = end
        parts.append(detached[cursor:])
        masked = "".join(parts)
    else:
        def _keep(match: re.Match[str]) -> str:
            return _stash(match.group(1), match.group(2) or "", match.group(0))

        masked = _FENCE.sub(_keep, detached)
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
    stack: list[tuple[str, int, str]] = []
    for i, line in enumerate(raw.splitlines(), 1):
        excerpt = line.strip()[:100]
        directive = _DIRECTIVE_LINE.match(line.rstrip("\r"))
        if directive:
            marks = str(directive.group("marks") or "")
            name = directive.group("named") or directive.group("glued")
            if name:
                if stack and len(marks) >= len(stack[-1][2]):
                    issues.append({"kind": "nested", "line": i, "excerpt": excerpt})
                stack.append((name, i, marks))
                continue
            if stack and len(marks) >= len(stack[-1][2]):
                stack.pop()
                continue
            issues.append({"kind": "orphan_closer", "line": i, "excerpt": excerpt})
            continue
        if _OPENER.search(line):
            issues.append({"kind": "glued_opener", "line": i, "excerpt": excerpt})
        elif _NAME_LINE.match(line):
            issues.append({"kind": "remnant_name", "line": i, "excerpt": excerpt})
        elif _NAME_TOKEN.search(line):
            issues.append({"kind": "glued_name", "line": i, "excerpt": excerpt})
    for name, i, _marks in stack:
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
