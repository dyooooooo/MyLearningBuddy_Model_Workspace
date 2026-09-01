from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_CIRCLED = "\u2460-\u2473\u3251-\u325f\u32b1-\u32b2"
_FN_START = re.compile(rf"^([{_CIRCLED}])(\d*)\s*(?:〔|选自|节选)")
_CIRCLED_LEAD = re.compile(rf"^[{_CIRCLED}]")
_HR = re.compile(r"^(?:---|\*\*\*|___|<hr\s*/?>)\s*$", re.I)
_FINAL = re.compile(r"[。！？…]$")
_CAPTION = re.compile(r"(?:图|(?:作)$)")
_URL = re.compile(
    r"(https?://\S+|/api/v1/\S+|!\[[^\]]*\]\([^)]+\)|\[[^\]]*\]\([^)]+\))"
)
_WRAP = re.compile(
    rf"(?<!\$)\^{{([{_CIRCLED}\d]{{1,3}})}}|(?<![\${{])([{_CIRCLED}])(?![}}\$])"
)
_DOUBLE_SUP = re.compile(r"\$(\$\^\{[^}]+\}\$)\$")
_TOC = "kb-page-toc"
_NEXT_FN = re.compile(rf"(?=[{_CIRCLED}]\d*\s*(?:〔|选自|节选))")


def _book_dir(book_id: str, root: str) -> Path:
    base = Path(root).resolve() if root else Path.cwd()
    if (base / "textbooks").is_dir():
        return base / "textbooks" / book_id
    return base / book_id


def _mineru_blocks(book: Path) -> list[dict[str, Any]]:
    folder = book / "mineru"
    if not folder.is_dir():
        return []
    for path in sorted(folder.glob("*content_list*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, list):
            return data
    return []


def _is_fn_line(line: str) -> bool:
    text = line.strip()
    return bool(text) and (bool(_FN_START.match(text)) or bool(_HR.match(text)))


def _is_fn_content(line: str) -> bool:
    return bool(_FN_START.match(line.strip()))


def _is_activity_line(line: str) -> bool:
    text = line.strip()
    return bool(_CIRCLED_LEAD.match(text)) and not _FN_START.match(text)


def _is_caption(text: str) -> bool:
    t = text.strip()
    return len(t) <= 24 and bool(_CAPTION.search(t))


def _circled_value(ch: str) -> int:
    code = ord(ch)
    if 0x2460 <= code <= 0x2473:
        return code - 0x2460 + 1
    if 0x3251 <= code <= 0x325F:
        return code - 0x3251 + 21
    if 0x32B1 <= code <= 0x32BF:
        return code - 0x32B1 + 36
    return 99


def _note_key(text: str) -> tuple[int, ...]:
    match = _FN_START.match(text.strip())
    if not match:
        return (99,)
    number = _circled_value(match.group(1))
    extra = match.group(2)
    if extra:
        number = number * (10 ** len(extra)) + int(extra)
    return (number,)


def _wrap_marks(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return "$^{" + (match.group(1) or match.group(2)) + "}$"

    parts = _URL.split(text)
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        if _URL.fullmatch(part):
            out.append(part)
            continue
        cleaned = _DOUBLE_SUP.sub(r"\1", part)
        chunks = cleaned.split("$")
        rebuilt: list[str] = []
        for i, chunk in enumerate(chunks):
            if i % 2 == 0:
                rebuilt.append(_WRAP.sub(repl, chunk))
            else:
                rebuilt.append(chunk)
        out.append("$".join(rebuilt))
    return "".join(out)


def _wrap_body_line(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith("![") or stripped.startswith("<"):
        return line
    if _is_fn_content(stripped) or _is_activity_line(stripped):
        prefix = line[: len(line) - len(line.lstrip())]
        lead = re.match(rf"^([{_CIRCLED}]\d*)", stripped)
        if not lead:
            return line
        head = lead.group(0)
        tail = stripped[len(head) :]
        return prefix + head + _wrap_marks(tail)
    return _wrap_marks(line)


def _fn_runs(lines: list[str]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    i = 0
    n = len(lines)
    while i < n:
        if _is_fn_content(lines[i]):
            last = i
            k = i + 1
            while k < n:
                if not lines[k].strip() or _is_fn_line(lines[k]):
                    if _is_fn_content(lines[k]):
                        last = k
                    k += 1
                    continue
                break
            runs.append((i, last))
            i = k
        else:
            i += 1
    return runs


def _insert_rule(lines: list[str], start: int) -> list[str]:
    prev = start - 1
    while prev >= 0 and not lines[prev].strip():
        prev -= 1
    if prev >= 0 and _HR.match(lines[prev].strip()):
        return lines
    chunk = ["---", ""]
    if start > 0 and lines[start - 1].strip():
        chunk = ["", "---", ""]
    return lines[:start] + chunk + lines[start:]


def _ensure_rules(text: str) -> str:
    lines = text.splitlines()
    runs = _fn_runs(lines)
    if not runs:
        return text
    for start, _end in reversed(runs):
        lines = _insert_rule(lines, start)
    out = "\n".join(lines)
    if text.endswith("\n"):
        out += "\n"
    return out


def _split_note_blob(text: str) -> list[str]:
    parts = _NEXT_FN.split(text.strip())
    return [part.strip() for part in parts if _FN_START.match(part.strip())]


def _run_notes(lines: list[str], start: int, end: int) -> list[str]:
    notes: list[str] = []
    for line in lines[start : end + 1]:
        if not _is_fn_content(line):
            continue
        notes.extend(_split_note_blob(line.strip()) or [line.strip()])
    return notes


def _merge_notes(current: list[str], mined: list[str]) -> list[str]:
    by_key: dict[tuple[int, ...], str] = {}
    for note in mined + current:
        key = _note_key(note)
        old = by_key.get(key, "")
        if len(note) >= len(old):
            by_key[key] = note
    return [by_key[k] for k in sorted(by_key)]


def _note_headword(note: str) -> str:
    text = note.strip()
    if re.match(rf"^[{_CIRCLED}]\d*\s*(?:选自|节选)", text):
        return ""
    match = re.match(rf"^[{_CIRCLED}]\d*\s*〔([^〕]{{1,40}})〕", text)
    if not match:
        return ""
    head = re.sub(r"（[^）]+）|\([^)]+\)", "", match.group(1))
    return re.split(r"[，,。；;]", head, 1)[0].strip()


def _needs_fill(current: list[str], mined: list[str], body: str = "") -> bool:
    if not mined:
        return False
    have = {_note_key(n): n for n in current}
    for note in mined:
        key = _note_key(note)
        old = have.get(key)
        head = _note_headword(note)
        if body and head and head not in body:
            continue
        if old is None:
            return True
        if len(note) > len(old) + 4 and old.rstrip("，,、 ") in note:
            return True
    return False


def _collect_mineru_notes(items: list[dict[str, Any]]) -> list[str]:
    literary: list[dict[str, Any]] = []
    for item in items:
        kind = item.get("type")
        if kind not in {"page_footnote", "text"}:
            continue
        text = str(item.get("text") or "").strip()
        if not _FN_START.match(text):
            continue
        literary.append(item)
    if not literary:
        return []

    min_y = min(item["bbox"][1] for item in literary if item.get("bbox")) - 12
    notes: list[dict[str, Any]] = []
    for item in literary:
        text = str(item.get("text") or "").strip()
        bbox = item.get("bbox") or [0, 0, 0, 0]
        chunks = _split_note_blob(text) or [text]
        for chunk in chunks:
            notes.append({"text": chunk, "bbox": bbox, "key": _note_key(chunk)})

    fragments: list[dict[str, Any]] = []
    for item in items:
        if item.get("type") != "text":
            continue
        text = str(item.get("text") or "").strip()
        bbox = item.get("bbox") or [0, 0, 0, 0]
        if not text or _FN_START.match(text) or _CIRCLED_LEAD.match(text):
            continue
        if bbox[1] < min_y or _is_caption(text):
            continue
        fragments.append({"text": text, "bbox": bbox})

    for frag in sorted(fragments, key=lambda x: (x["bbox"][1], x["bbox"][0])):
        text = frag["text"]
        open_notes = [n for n in notes if not _FINAL.search(n["text"].rstrip())]
        if not open_notes:
            continue
        target = max(open_notes, key=lambda n: (n["bbox"][1], n["bbox"][0]))
        if text in target["text"]:
            continue
        target["text"] = target["text"].rstrip() + text

    by_key: dict[tuple[int, ...], str] = {}
    for note in notes:
        key = note["key"]
        old = by_key.get(key, "")
        if len(note["text"]) >= len(old):
            by_key[key] = note["text"]
    return [by_key[k] for k in sorted(by_key)]


def _wrap_non_fn_lines(lines: list[str]) -> list[str]:
    runs = _fn_runs(lines)
    fn_idx = set()
    for start, end in runs:
        fn_idx.update(range(start, end + 1))
    out: list[str] = []
    for i, line in enumerate(lines):
        if i in fn_idx and _is_fn_content(line):
            out.append(line)
        elif i in fn_idx and _HR.match(line.strip()):
            out.append(line)
        else:
            out.append(_wrap_body_line(line))
    return out


def _replace_run(
    lines: list[str], start: int, end: int, notes: list[str]
) -> list[str]:
    block = ["---", ""]
    for i, note in enumerate(notes):
        if i:
            block.append("")
        block.append(note)
    prev = start - 1
    while prev >= 0 and not lines[prev].strip():
        prev -= 1
    if prev >= 0 and _HR.match(lines[prev].strip()):
        start = prev
    if start > 0 and lines[start - 1].strip():
        block = [""] + block
    tail = end + 1
    while tail < len(lines) and not lines[tail].strip():
        tail += 1
    return lines[:start] + block + ([""] if tail < len(lines) else []) + lines[tail:]


def _append_notes(text: str, notes: list[str]) -> str:
    body = text.rstrip()
    block = "\n\n---\n\n" + "\n\n".join(notes)
    return body + block + "\n"


def _drop_note_echoes(lines: list[str], notes: list[str]) -> list[str]:
    if not notes:
        return lines
    runs = _fn_runs(lines)
    if not runs:
        return lines
    start, _end = runs[-1]
    prev = start - 1
    while prev >= 0 and (not lines[prev].strip() or _HR.match(lines[prev].strip())):
        prev -= 1
    if prev < 0:
        return lines
    fragment = lines[prev].strip()
    if len(fragment) < 8:
        return lines
    if any(fragment in note and fragment != note for note in notes):
        return lines[:prev] + lines[prev + 1 :]
    return lines


def _restore_page(md: str, mined: list[str]) -> tuple[str, dict[str, int]]:
    stats = {"marks": 0, "notes": 0, "rules": 0}
    if _TOC in md:
        return md, stats
    lines = md.splitlines()
    wrapped = _wrap_non_fn_lines(lines)
    stats["marks"] = int(wrapped != lines)
    lines = wrapped
    runs = _fn_runs(lines)
    if runs:
        start, end = runs[-1]
        current = _run_notes(lines, start, end)
        body = "\n".join(lines[:start])
        mined = [
            note
            for note in mined
            if not _note_headword(note) or _note_headword(note) in body
        ]
        merged = _merge_notes(current, mined)
        if _needs_fill(current, mined, body):
            lines = _replace_run(lines, start, end, merged)
            stats["notes"] = 1
        lines = _drop_note_echoes(lines, merged or current)
        out = _ensure_rules("\n".join(lines))
        if md.endswith("\n") and not out.endswith("\n"):
            out += "\n"
        if "\n---\n" in out and "\n---\n" not in md:
            stats["rules"] = 1
        if out != md and stats["notes"] == 0 and stats["marks"] == 0:
            stats["rules"] = int("\n---\n" in out)
        return out, stats
    if mined:
        out = _ensure_rules(_append_notes("\n".join(lines), mined))
        if not out.endswith("\n"):
            out += "\n"
        stats["notes"] = 1
        stats["rules"] = 1
        return out, stats
    out = "\n".join(lines)
    if md.endswith("\n"):
        out += "\n"
    new = _ensure_rules(out)
    if "\n---\n" in new and "\n---\n" not in md:
        stats["rules"] = 1
    return new, stats


def restore_footnotes(book_id: str, apply: str = "fix", root: str = "") -> dict[str, Any]:
    """从 MinerU 回补圈号页注，正文圈号改成 $^{n}$，注释前加横杠。"""
    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    mode = (apply or "fix").strip().lower()
    if mode not in {"scan", "fix"}:
        raise ValueError("apply must be scan or fix")

    book = _book_dir(sid, root)
    pages_dir = book / "pages"
    if not pages_dir.is_dir():
        raise FileNotFoundError(str(pages_dir))

    blocks = _mineru_blocks(book)
    by_page: dict[int, list[dict[str, Any]]] = {}
    for item in blocks:
        try:
            idx = int(item.get("page_idx"))
        except (TypeError, ValueError):
            continue
        by_page.setdefault(idx, []).append(item)

    page_files = sorted(p for p in pages_dir.glob("*.md") if p.stem.isdigit())
    if not page_files:
        return {"book_id": sid, "skipped": True, "reason": "no_pages", "changed": 0}

    has_shape = any(_collect_mineru_notes(items) for items in by_page.values())
    if not has_shape:
        for path in page_files:
            if any(_is_fn_content(ln) for ln in path.read_text(encoding="utf-8").splitlines()):
                has_shape = True
                break
    if not has_shape:
        return {
            "book_id": sid,
            "skipped": True,
            "reason": "no_circled_page_notes",
            "changed": 0,
        }

    changed_pages: list[str] = []
    mark_pages = 0
    note_pages = 0
    rule_pages = 0
    pending: list[tuple[Path, str]] = []

    for path in page_files:
        page_no = int(path.stem)
        original = path.read_text(encoding="utf-8")
        mined = _collect_mineru_notes(by_page.get(page_no - 1, []))
        restored, stats = _restore_page(original, mined)
        if restored == original:
            continue
        pending.append((path, restored))
        changed_pages.append(path.name)
        mark_pages += stats["marks"]
        note_pages += stats["notes"]
        rule_pages += stats["rules"]

    result: dict[str, Any] = {
        "book_id": sid,
        "apply": mode,
        "changed": len(changed_pages),
        "pages": changed_pages[:40],
        "mark_pages": mark_pages,
        "note_pages": note_pages,
        "rule_pages": rule_pages,
    }
    if mode != "fix":
        return result
    for path, text in pending:
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    result["written"] = len(pending)
    return result
