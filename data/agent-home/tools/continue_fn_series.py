from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_CIRCLED = "\u2460-\u2473\u3251-\u325f\u32b1-\u32bf"
_FN_START = re.compile(rf"^([{_CIRCLED}])(\d*)\s*(?:〔|选自|节选)")
_HR = re.compile(r"^(?:---|\*\*\*|___)\s*$")
_SUP = re.compile(r"\$\^\{([^}]+)\}\$")
_LEAD = re.compile(rf"^([{_CIRCLED}])(\d*)")
_TOC = "kb-page-toc"


def _book_dir(book_id: str, root: str) -> Path:
    base = Path(root).resolve() if root else Path.cwd()
    if (base / "textbooks").is_dir():
        return base / "textbooks" / book_id
    return base / book_id


def _circled_val(ch: str) -> int | None:
    code = ord(ch)
    if 0x2460 <= code <= 0x2473:
        return code - 0x2460 + 1
    if 0x3251 <= code <= 0x325F:
        return code - 0x3251 + 21
    if 0x32B1 <= code <= 0x32BF:
        return code - 0x32B1 + 36
    return None


def _mark_num(tok: str) -> int | None:
    tok = (tok or "").strip()
    if not tok:
        return None
    first = _circled_val(tok[0])
    if first is not None:
        extra = tok[1:]
        if extra.isdigit():
            return first * (10 ** len(extra)) + int(extra)
        if extra:
            return None
        return first
    if tok.isdigit():
        return int(tok)
    return None


def _label(num: int) -> str:
    if 1 <= num <= 20:
        return chr(0x2460 + num - 1)
    if 21 <= num <= 35:
        return chr(0x3251 + num - 21)
    if 36 <= num <= 50:
        return chr(0x32B1 + num - 36)
    return str(num)


def _body_mark(num: int) -> str:
    token = _label(num) if num <= 50 else str(num)
    return "$^{" + token + "}$"


def _note_key(note: str) -> int | None:
    match = _FN_START.match(note.strip())
    if not match:
        return None
    return _mark_num(match.group(1) + (match.group(2) or ""))


def _note_head(note: str) -> str:
    text = note.strip()
    if re.match(rf"^[{_CIRCLED}]\d*\s*(?:选自|节选)", text):
        return ""
    match = re.match(rf"^[{_CIRCLED}]\d*\s*〔([^〕]{{1,40}})〕", text)
    if not match:
        return ""
    head = re.sub(r"（[^）]+）|\([^)]+\)", "", match.group(1))
    return re.split(r"[，,.。；;]", head, 1)[0].strip()


def _parse(text: str) -> tuple[str, list[str]] | None:
    if _TOC in text:
        return None
    lines = text.splitlines()
    body: list[str] = []
    notes: list[str] = []
    in_notes = False
    for index, line in enumerate(lines):
        if _HR.match(line.strip()) and index > 0 and not in_notes:
            rest = lines[index + 1 :]
            if any(_FN_START.match(item.strip()) for item in rest if item.strip()):
                in_notes = True
                continue
        if in_notes:
            if _FN_START.match(line.strip()):
                notes.append(line.strip())
        else:
            body.append(line)
    return "\n".join(body), notes


def _is_restart(prev_max: int, value: int) -> bool:
    if value == 1 and prev_max >= 1:
        return True
    return prev_max >= 10 and value <= 5 and value < prev_max


def _split_runs(vals: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    current: list[int] = []
    for value in vals:
        if current and _is_restart(current[-1], value):
            runs.append(current)
            current = [value]
        else:
            current.append(value)
    if current:
        runs.append(current)
    return runs


def _body_marks(body: str) -> list[tuple[int, int, int]]:
    found: list[tuple[int, int, int]] = []
    for match in _SUP.finditer(body):
        value = _mark_num(match.group(1))
        if value:
            found.append((match.start(), match.end(), value))
    return found


def _split_note_series(notes: list[str]) -> tuple[list[str], list[str]]:
    first: list[str] = []
    second: list[str] = []
    seen = False
    max_seen = 0
    series = 1
    for note in notes:
        key = _note_key(note)
        if key is None:
            continue
        if key == 1 and seen and max_seen >= 1:
            series = 2
        if series == 1:
            first.append(note)
            max_seen = max(max_seen, key)
            seen = True
        else:
            second.append(note)
    return first, second


def _set_note_num(note: str, num: int) -> str:
    match = _LEAD.match(note.strip())
    if not match:
        return _label(num) + note
    return _label(num) + note.strip()[match.end() :]


def _normalize_body(body: str) -> str:
    def repl(match: re.Match[str]) -> str:
        value = _mark_num(match.group(1))
        if not value:
            return match.group(0)
        return _body_mark(value)

    return _SUP.sub(repl, body)


def _normalize_notes(notes: list[str]) -> list[str]:
    out: list[str] = []
    for note in notes:
        key = _note_key(note)
        out.append(_set_note_num(note, key) if key else note)
    return out


def _write_page(path: Path, body: str, notes: list[str]) -> None:
    body = _normalize_body(body)
    notes = _normalize_notes(notes)
    text = body.rstrip() + "\n"
    if notes:
        text = text + "\n---\n\n" + "\n\n".join(notes) + "\n"
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"([^\n])\n---\n", r"\1\n\n---\n", text)
    if not text.endswith("\n"):
        text += "\n"
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _page_plan(body: str, notes: list[str]) -> dict[str, Any] | None:
    marks = _body_marks(body)
    if not marks:
        return None
    values = [item[2] for item in marks]
    runs = _split_runs(values)
    if len(runs) < 2:
        return None
    first_run, second_run = runs[0], runs[1]
    first_notes, second_notes = _split_note_series(notes)
    offset = max(first_run)
    first_keys = [key for key in (_note_key(item) for item in first_notes) if key]
    fix_twenty = first_run[-1] == 21 and 20 in first_keys and 21 not in first_keys
    if fix_twenty:
        offset = 20
    mapping = {old: offset + old for old in second_run}
    return {
        "marks": marks,
        "first_count": len(first_run),
        "offset": offset,
        "mapping": mapping,
        "first_notes": first_notes,
        "second_notes": second_notes,
        "fix_twenty": fix_twenty,
        "second_run": second_run,
    }


def _apply_body(body: str, plan: dict[str, Any]) -> str:
    repl: list[tuple[int, int, str]] = []
    for index, (start, end, value) in enumerate(plan["marks"]):
        if index < plan["first_count"]:
            if plan["fix_twenty"] and value == 21 and index == plan["first_count"] - 1:
                repl.append((start, end, _body_mark(20)))
            continue
        if value in plan["mapping"]:
            repl.append((start, end, _body_mark(plan["mapping"][value])))
    text = body
    for start, end, neu in reversed(repl):
        text = text[:start] + neu + text[end:]
    return text


def _pull_from_next(
    plan: dict[str, Any], body: str, next_notes: list[str]
) -> tuple[list[str], list[str]]:
    marks = plan["marks"]
    first_count = plan["first_count"]
    after = body[marks[first_count][0] :] if first_count < len(marks) else ""
    have = {_note_key(item) for item in plan["second_notes"]}
    missing = set(plan["second_run"]) - have
    if not missing:
        return plan["second_notes"], next_notes
    keep: list[str] = []
    pulled: list[str] = []
    for note in next_notes:
        key = _note_key(note)
        head = _note_head(note)
        take = False
        if key in missing:
            if head and len(head) >= 2 and head in after:
                take = True
            elif (not head or len(head) <= 2) and key in plan["second_run"]:
                if not head or head in after:
                    take = True
        if take:
            pulled.append(note)
        else:
            keep.append(note)
    return plan["second_notes"] + pulled, keep


def continue_fn_series(
    book_id: str, apply: str = "fix", root: str = ""
) -> dict[str, Any]:
    """后页正文并进前页时，把从①起号的注释接到第一段最大号之后。"""
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

    files = sorted(path for path in pages_dir.glob("*.md") if path.stem.isdigit())
    if not files:
        return {"book_id": sid, "skipped": True, "reason": "no_pages", "changed": 0}

    parsed: dict[int, tuple[Path, str, list[str]]] = {}
    for path in files:
        original = path.read_text(encoding="utf-8")
        parts = _parse(original)
        if parts is None:
            continue
        parsed[int(path.stem)] = (path, parts[0], parts[1])

    hits: list[str] = []
    pending: list[tuple[Path, str, list[str]]] = []
    consumed_next: set[int] = set()

    for page_no in sorted(parsed):
        path, body, notes = parsed[page_no]
        if page_no in consumed_next:
            _p, body, notes = parsed[page_no]
        plan = _page_plan(body, notes)
        if not plan:
            continue
        hits.append(path.name)
        second = plan["second_notes"]
        nxt = page_no + 1
        if nxt in parsed:
            next_path, next_body, next_notes = parsed[nxt]
            second, kept = _pull_from_next(plan, body, next_notes)
            if kept != next_notes:
                parsed[nxt] = (next_path, next_body, kept)
                consumed_next.add(nxt)
                pending.append((next_path, next_body, kept))
        new_body = _normalize_body(_apply_body(body, plan))
        mapped = []
        for note in second:
            key = _note_key(note)
            if key in plan["mapping"]:
                mapped.append(_set_note_num(note, plan["mapping"][key]))
            else:
                mapped.append(note)
        new_notes = _normalize_notes(
            sorted(plan["first_notes"], key=lambda item: _note_key(item) or 99)
            + sorted(mapped, key=lambda item: _note_key(item) or 99)
        )
        pending.append((path, new_body, new_notes))
        parsed[page_no] = (path, new_body, new_notes)

    result: dict[str, Any] = {
        "book_id": sid,
        "apply": mode,
        "changed": len(hits),
        "pages": hits[:40],
    }
    if not hits:
        result["skipped"] = True
        result["reason"] = "no_restarted_series"
        return result
    if mode != "fix":
        return result
    written = 0
    seen: set[str] = set()
    for path, body, notes in pending:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        _write_page(path, body, notes)
        written += 1
    result["written"] = written
    return result
