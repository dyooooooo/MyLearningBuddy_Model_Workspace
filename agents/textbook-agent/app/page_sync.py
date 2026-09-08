"""Align official page markdown with the mid-layer file (fences stay in mid)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from app.mid_text import has_kb_fences, strip_skin

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"::: (kb-[\w-]+)\r?\n([\s\S]*?)\r?\n:::")

_lock_guard = threading.Lock()
_page_locks: dict[tuple[str, int], threading.Lock] = {}
_suppress: set[str] = set()


def reset_for_tests() -> None:
    _page_locks.clear()
    _suppress.clear()


def page_bytes(text: str) -> str:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in raw.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    body = "\n".join(lines)
    return body + ("\n" if body else "")


def content_sha(text: str) -> str:
    return hashlib.sha256(page_bytes(text).encode("utf-8")).hexdigest()


def same_body(mid: str | None, source: str) -> bool:
    if mid is None:
        return False
    return page_bytes(strip_skin(mid)) == page_bytes(source)


def _page_lock(book_id: str, page_no: int) -> threading.Lock:
    key = (book_id, int(page_no))
    with _lock_guard:
        lock = _page_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _page_locks[key] = lock
        return lock


def _key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def is_suppressed(path: Path | str) -> bool:
    return _key(Path(path)) in _suppress


@contextmanager
def suppressing(*paths: Path) -> Iterator[None]:
    keys = [_key(path) for path in paths if path]
    _suppress.update(keys)
    try:
        yield
    finally:
        for key in keys:
            _suppress.discard(key)


def stamp_path(book_id: str, page_no: int) -> Path:
    from app import library

    return library.book_dir(book_id) / "mid" / ".stamps" / f"{int(page_no):04d}.json"


def read_stamp(book_id: str, page_no: int) -> dict[str, str]:
    path = stamp_path(book_id, page_no)
    if not path.is_file():
        return {"source_sha": "", "mid_sha": "", "body_sha": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"source_sha": "", "mid_sha": "", "body_sha": ""}
    if not isinstance(data, dict):
        return {"source_sha": "", "mid_sha": "", "body_sha": "", "last_trace_id": "", "last_origin": ""}
    return {
        "source_sha": str(data.get("source_sha") or ""),
        "mid_sha": str(data.get("mid_sha") or ""),
        "body_sha": str(data.get("body_sha") or ""),
        "last_trace_id": str(data.get("last_trace_id") or ""),
        "last_origin": str(data.get("last_origin") or ""),
    }


def write_stamp(book_id: str, page_no: int, source: str, mid: str | None) -> dict[str, str]:
    path = stamp_path(book_id, page_no)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from app.logsetup import current_origin, current_trace

        last_trace = current_trace()
        last_origin = current_origin()
    except Exception:
        last_trace = ""
        last_origin = ""
    payload = {
        "source_sha": content_sha(source),
        "mid_sha": content_sha(mid) if mid is not None else "",
        "body_sha": content_sha(strip_skin(mid)) if mid is not None else "",
        "last_trace_id": last_trace,
        "last_origin": last_origin,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return payload


def extract_fences(mid: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in _FENCE.finditer(mid or "")]


def _segments(mid: str) -> list[tuple[str, str, str]]:
    text = mid or ""
    out: list[tuple[str, str, str]] = []
    pos = 0
    for match in _FENCE.finditer(text):
        if match.start() > pos:
            out.append(("prose", "", text[pos : match.start()]))
        out.append(("fence", match.group(1), match.group(2)))
        pos = match.end()
    if pos < len(text):
        out.append(("prose", "", text[pos:]))
    return out


def _block_needle(text: str) -> str:
    return page_bytes(text).strip("\n")


def _find_block(haystack: str, needle: str, start: int = 0) -> int:
    if not needle:
        return -1
    cursor = max(0, start)
    while True:
        pos = haystack.find(needle, cursor)
        if pos < 0:
            return -1
        before_ok = pos == 0 or haystack[pos - 1] == "\n"
        after = pos + len(needle)
        after_ok = after == len(haystack) or haystack[after] == "\n"
        if before_ok and after_ok:
            return pos
        cursor = pos + 1


def _join_segments(parts: list[str]) -> str:
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        if out and not out[-1].endswith("\n") and not part.startswith("\n"):
            out.append("\n")
        out.append(part)
    text = "".join(out)
    return page_bytes(text) if text.strip() else text


def _empty_insert_pos(
    source: str,
    segs: list[tuple[str, str, str]],
    idx: int,
    used: list[tuple[int, int, str, str]],
    after: int,
) -> int:
    """Place an empty fence after in-box prose, else before the next block."""

    def _search(needle: str, start: int) -> int:
        return _find_block(source, needle, start)

    for j in range(idx - 1, -1, -1):
        kind, _name, payload = segs[j]
        if kind == "prose" and payload.strip():
            needle = _block_needle(payload)
            candidates = [needle]
            lines = [line for line in needle.split("\n") if line.strip()]
            if lines:
                candidates.append(lines[-1])
            for cand in candidates:
                pos = _search(cand, after)
                if pos >= 0:
                    return pos + len(cand)
            break
        if kind == "fence" and (payload or "").strip():
            needle = page_bytes(payload).rstrip("\n")
            pos = _search(needle, after)
            if pos >= 0:
                return pos + len(needle)
            break
    for j in range(idx + 1, len(segs)):
        kind, _name, payload = segs[j]
        if kind == "prose" and payload.strip():
            needle = _block_needle(payload)
            candidates = [needle]
            lines = [line for line in needle.split("\n") if line.strip()]
            if lines:
                candidates.append(lines[0])
            for cand in candidates:
                pos = _search(cand, after)
                if pos >= 0:
                    return pos
            break
        if kind == "fence" and (payload or "").strip():
            needle = page_bytes(payload).rstrip("\n")
            pos = _search(needle, after)
            if pos >= 0:
                return pos
            break
    if used:
        return max(row[1] for row in used)
    return len(source)


def paste_fences(
    old_mid: str,
    new_source: str,
    tag_fn: Callable[[str], str] | None = None,
) -> str:
    source = new_source or ""
    fences = extract_fences(old_mid or "")
    ranked = sorted(enumerate(fences), key=lambda item: (-len((item[1][1] or "").strip()), item[0]))
    used: list[tuple[int, int, str, str]] = []

    def _overlaps(start: int, end: int) -> bool:
        return any(not (end <= left or right <= start) for left, right, _n, _i in used)

    for _idx, (name, inner) in ranked:
        needle = page_bytes(inner).rstrip("\n")
        if not needle:
            continue
        pos = _find_block(source, needle)
        if pos < 0:
            try:
                from app.logsetup import event

                event(
                    "sync.fence_drop",
                    fence=name,
                    needle_sha=content_sha(needle)[:16],
                    needle_chars=len(needle),
                )
            except Exception:
                pass
            continue
        end = pos + len(needle)
        if _overlaps(pos, end):
            continue
        used.append((pos, end, name, source[pos:end]))

    empties: list[tuple[int, int, str]] = []
    segs = _segments(old_mid or "")
    after = 0
    for idx, (kind, name, inner) in enumerate(segs):
        if kind != "fence" or (inner or "").strip():
            continue
        pos = _empty_insert_pos(source, segs, idx, used, after)
        if any(left < pos < right for left, right, _n, _i in used):
            pos = next(right for left, right, _n, _i in sorted(used) if left < pos < right)
        empties.append((pos, idx, name))
        after = pos

    if not used and not empties:
        tagged = tag_fn(source) if tag_fn and source.strip() else source
        return page_bytes(tagged)

    marks: list[tuple[int, int, int, str, str]] = []
    for start, end, name, inner in used:
        marks.append((start, end, start, name, inner))
    for pos, order, name in empties:
        marks.append((pos, pos, order, name, ""))
    marks.sort(key=lambda row: (row[0], 0 if row[1] > row[0] else 1, row[2]))

    chunks: list[str] = []
    cursor = 0
    for start, end, _order, name, inner in marks:
        if start > cursor:
            leftover = source[cursor:start]
            if tag_fn and leftover.strip():
                leftover = tag_fn(leftover)
            chunks.append(leftover)
        if end > start:
            chunks.append(f"::: {name}\n{inner.rstrip()}\n:::")
            cursor = end
        else:
            chunks.append(f"::: {name}\n\n:::")
            cursor = max(cursor, start)
    if cursor < len(source):
        leftover = source[cursor:]
        if tag_fn and leftover.strip():
            leftover = tag_fn(leftover)
        chunks.append(leftover)
    return _join_segments(chunks)


def try_tag(text: str, book_id: str = "") -> str:
    raw = text or ""
    if not raw.strip():
        return raw
    try:
        from app import library, surfaces

        if not library.surfaces_ready():
            return raw
        tagged = surfaces.tag_page(raw, book_id=book_id)
        return tagged if tagged is not None else raw
    except Exception:
        logger.warning("tag_page failed book=%s", book_id, exc_info=True)
        return raw


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _write_source(book_id: str, page_no: int, text: str) -> None:
    from app import library

    path = library.page_path(book_id, page_no)
    with suppressing(path):
        library.write_page(book_id, page_no, page_bytes(text), sync=False)


def _write_mid(book_id: str, page_no: int, text: str) -> None:
    from app import library

    path = library.mid_path(book_id, page_no)
    with suppressing(path):
        library.write_mid(book_id, page_no, page_bytes(text), sync=False)


def _tag_missing_mid(source: str, book_id: str) -> str | None:
    from app import library

    tagged = try_tag(source, book_id)
    if has_kb_fences(tagged):
        return tagged
    if library.surfaces_ready():
        return tagged or source
    return None


def retag_page(book_id: str, page_no: int) -> dict[str, Any]:
    """Re-run whole-page tagging, overwriting the mid file.

    `sync_page` 只在 `mid` 不存在时做整页判定（`tag_mid`），之后一律走 `paste_fences`
    按块补围栏 —— 它永远不会重问「这一整页是不是目录页」。已导入的书如果目录页围栏
    判错了，不会自愈，得走这里强制重判。
    """
    from app import library

    sid = library.safe_id(book_id)
    n = int(page_no)
    if n < 1:
        return {"ok": False, "action": "skip", "reason": "bad_page"}

    with _page_lock(sid, n):
        source_path = library.page_path(sid, n)
        if not source_path.is_file():
            return {"ok": False, "action": "skip", "reason": "no_source"}
        if not library.surfaces_ready():
            return {"ok": False, "action": "skip", "reason": "surfaces_missing"}
        source = source_path.read_text(encoding="utf-8")
        tagged = try_tag(source, sid)
        if tagged is None or not tagged.strip():
            return {"ok": False, "action": "skip", "reason": "tag_empty"}
        mid_file = library.mid_path(sid, n)
        before = mid_file.read_text(encoding="utf-8") if mid_file.is_file() else None
        changed = before is None or content_sha(before) != content_sha(tagged)
        if changed:
            _write_mid(sid, n, tagged)
        write_stamp(sid, n, source, tagged)
        try:
            from app.logsetup import event

            event(
                "sync.retag",
                book_id=sid,
                page_no=n,
                changed=changed,
                fences=[name for name, _inner in extract_fences(tagged)],
            )
        except Exception:
            pass
        return {
            "ok": True,
            "action": "retag_mid" if changed else "unchanged",
            "book_id": sid,
            "page_no": n,
            "changed": changed,
            "fences": [name for name, _inner in extract_fences(tagged)],
        }


def sync_page(book_id: str, page_no: int) -> dict[str, Any]:
    from app import library

    sid = library.safe_id(book_id)
    n = int(page_no)
    if n < 1:
        return {"ok": False, "action": "skip", "reason": "bad_page"}

    with _page_lock(sid, n):
        source_path = library.page_path(sid, n)
        mid_file = library.mid_path(sid, n)
        if not source_path.is_file():
            return {"ok": False, "action": "skip", "reason": "no_source"}

        source = source_path.read_text(encoding="utf-8")
        mid = mid_file.read_text(encoding="utf-8") if mid_file.is_file() else None
        stamp = read_stamp(sid, n)
        src_sha = content_sha(source)
        mid_sha = content_sha(mid) if mid is not None else ""
        aligned = same_body(mid, source)
        src_dirty = src_sha != stamp["source_sha"]
        mid_dirty = mid_sha != stamp["mid_sha"]

        def _done(action: str, **extra: Any) -> dict[str, Any]:
            row = {"ok": True, "action": action, "book_id": sid, "page_no": n, **extra}
            try:
                from app.logsetup import event

                event(
                    "sync.decide",
                    book_id=sid,
                    page_no=n,
                    action=action,
                    src_dirty=src_dirty,
                    mid_dirty=mid_dirty,
                    aligned=aligned,
                    src_sha=src_sha[:16],
                    mid_sha=(mid_sha[:16] if mid_sha else ""),
                    stamp_src=(stamp.get("source_sha") or "")[:16],
                    stamp_mid=(stamp.get("mid_sha") or "")[:16],
                    prev_trace=stamp.get("last_trace_id") or "",
                )
            except Exception:
                pass
            return row

        stamp_fresh = bool(stamp["source_sha"]) and src_sha == stamp["source_sha"] and mid_sha == stamp["mid_sha"]

        if aligned:
            if stamp_fresh:
                return _done("ok")
            write_stamp(sid, n, source, mid)
            return _done("stamp")

        if mid is not None and same_body(mid_file.read_text(encoding="utf-8"), source):
            current = mid_file.read_text(encoding="utf-8")
            if stamp_fresh:
                return _done("ok")
            write_stamp(sid, n, source, current)
            return _done("stamp")

        if mid is None:
            tagged = _tag_missing_mid(source, sid)
            if tagged is None:
                write_stamp(sid, n, source, None)
                return _done("wait_tag")
            _write_mid(sid, n, tagged)
            write_stamp(sid, n, source, tagged)
            return _done("tag_mid")

        if src_dirty and not mid_dirty:
            if same_body(mid, source):
                write_stamp(sid, n, source, mid)
                return _done("stamp")
            new_mid = paste_fences(mid, source, lambda chunk: try_tag(chunk, sid))
            if same_body(mid_file.read_text(encoding="utf-8"), source):
                current = mid_file.read_text(encoding="utf-8")
                write_stamp(sid, n, source, current)
                return _done("stamp")
            _write_mid(sid, n, new_mid)
            write_stamp(sid, n, source, new_mid)
            return _done("paste_mid")

        if mid_dirty and not src_dirty:
            _write_source(sid, n, strip_skin(mid))
            write_stamp(sid, n, strip_skin(mid), mid)
            return _done("write_source")

        mid_newer = _mtime(mid_file) >= _mtime(source_path)
        if mid_newer:
            _write_source(sid, n, strip_skin(mid))
            write_stamp(sid, n, strip_skin(mid), mid)
            return _done("write_source")

        if same_body(mid_file.read_text(encoding="utf-8"), source):
            current = mid_file.read_text(encoding="utf-8")
            write_stamp(sid, n, source, current)
            return _done("stamp")
        new_mid = paste_fences(mid, source, lambda chunk: try_tag(chunk, sid))
        _write_mid(sid, n, new_mid)
        write_stamp(sid, n, source, new_mid)
        return _done("paste_mid")
