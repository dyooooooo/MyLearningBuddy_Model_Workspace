from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from statistics import median
from types import ModuleType
from typing import Callable

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_PAGE_TOKEN = re.compile(r"\d+(?:-\d+)?")
_P_MARK = re.compile(r"\(p\.\s*(\d+)\)", re.I)
_NAMED_P = re.compile(r"^(.+?)\s*\(p\.\s*(\d+)\)\s*$", re.I)
_ATX = re.compile(r"^#{1,6}\s+")
_RUNNING = re.compile(r"^(\d{1,3})\s+(.+)$")
_CJK = re.compile(r"[一-鿿]")


def _load_toc_mod() -> ModuleType:
    for path in (
        Path(__file__).resolve().with_name("build_toc.py"),
        Path("/data/agent/tools/build_toc.py"),
    ):
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("_mark_toc_build_toc", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise ImportError("cannot load build_toc.py")


def _load_build_toc() -> Callable[..., dict]:
    return _load_toc_mod().build_toc


def _parse_pages(raw: str) -> list[int]:
    out: list[int] = []
    for token in _PAGE_TOKEN.findall(raw or ""):
        if "-" in token:
            a, b = token.split("-", 1)
            lo, hi = int(a), int(b)
            if lo > hi:
                lo, hi = hi, lo
            out.extend(range(lo, hi + 1))
        else:
            out.append(int(token))
    pages = sorted({n for n in out if n > 0})
    if not pages:
        raise ValueError("pages required, e.g. 6,7 or 6-7")
    return pages


def _book(base: Path, book_id: str) -> Path:
    if (base / "textbooks").is_dir():
        book = base / "textbooks" / book_id
    else:
        book = base / book_id
    if not book.is_dir():
        raise FileNotFoundError(str(book))
    return book


def _mineru_blocks(book: Path) -> list[dict]:
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


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _unit_key(text: str) -> str:
    cleaned = re.sub(r"[?？!！*∗·•]+$", "", _norm(text)).strip()
    return re.sub(r"^\d+\s+", "", cleaned).lower()


def _can_be_group_head(text: str, toc: ModuleType) -> bool:
    bare = re.sub(r"[?？!！*∗·•]+$", "", (text or "").strip()).strip()
    if toc._is_contents_title(bare) or toc.SELF_ASSESS.match(bare):
        return False
    if toc.LETTER_BODY_SECTION.match(bare):
        return False
    if toc.CHAPTER_LINE.match(bare) or toc.PART_LINE.match(bare):
        return True
    if toc._is_intro_heading(bare) or toc._is_appendix_heading(bare):
        return True
    if toc.LATIN_UNIT_LINE.match(bare):
        return True
    return bool(toc._is_latin_group_bare(bare))


def _strip_running(text: str, toc: ModuleType) -> str:
    running = _RUNNING.match(text)
    if not running:
        return text
    number = int(running.group(1))
    rest = _norm(running.group(2))
    if number > 20 and _can_be_group_head(rest, toc):
        return rest
    return text


def _classify_line(
    text: str, page: int | None, unused_keys: set[str], toc: ModuleType
) -> tuple[str, str, int | None] | None:
    if toc._is_contents_title(text):
        return None
    self_row = toc.SELF_ASSESS.match(text)
    if self_row:
        print_page = int(self_row.group(1)) if self_row.group(1) else page
        return ("self", "Self-assessment", print_page)
    letter = toc.LETTER_BODY_SECTION.match(text)
    if letter:
        return ("section", f"{letter.group(1)} {letter.group(2).strip()}", page)
    if toc.CHAPTER_LINE.match(text) or toc.PART_LINE.match(text):
        return ("chapter", text, page)
    if toc._is_intro_heading(text) or toc._is_appendix_heading(text):
        return ("chapter", text, page)
    bare = re.sub(r"[?？!！*∗·•]+$", "", (text or "").strip()).strip()
    unit = toc.LATIN_UNIT_LINE.match(bare)
    if unit:
        return ("chapter", f"{unit.group(1)} {unit.group(2).strip()}", page)
    if toc._is_latin_group_bare(bare) and _unit_key(bare) in unused_keys:
        return ("chapter", bare, page)
    dot3 = toc.TOC_DOT3.match(text)
    if dot3:
        return ("section", f"{dot3.group(2)} {dot3.group(3).strip()}", int(dot3.group(4)))
    section = toc.TOC_SECTION.match(text)
    if section:
        return (
            "section",
            f"{section.group(2)} {section.group(3).strip()}",
            int(section.group(4)),
        )
    simple = toc.TOC_SIMPLE_SUB.match(text)
    if simple:
        return (
            "section",
            f"{simple.group(2)} {simple.group(3).strip()}",
            int(simple.group(4)),
        )
    appendix_item = toc.APPENDIX_ITEM.match(text)
    if appendix_item:
        return (
            "named",
            f"附录{appendix_item.group(1)} {appendix_item.group(2).strip()}",
            int(appendix_item.group(3)),
        )
    named = toc.LATIN_NAMED_PAGE.match(text)
    if named and not toc.LETTER_BODY_SECTION.match(text):
        return ("named", named.group(1).strip(), int(named.group(2)))
    return None


def _attach_marks(
    blocks: list[dict], unused_keys: set[str], toc: ModuleType
) -> tuple[list[dict], list[tuple[str, int]]]:
    titles: list[dict] = []
    marks: list[tuple[float, float, int]] = []
    named: list[tuple[str, int]] = []
    for block in blocks:
        text = _strip_running(_norm(str(block.get("text") or "")), toc)
        if not text:
            continue
        bbox = block.get("bbox") or [0, 0, 0, 0]
        x0, y0 = float(bbox[0]), float(bbox[1])
        hit = _NAMED_P.match(text)
        if hit:
            named.append((_norm(hit.group(1)), int(hit.group(2))))
            continue
        only = _P_MARK.fullmatch(text)
        if only:
            marks.append((y0, x0, int(only.group(1))))
            continue
        if _classify_line(text, None, unused_keys, toc) is None:
            continue
        titles.append({"text": text, "x": x0, "y": y0, "page": None})
    if titles and marks:
        ys = sorted(t["y"] for t in titles)
        gaps = [ys[i] - ys[i - 1] for i in range(1, len(ys)) if ys[i] - ys[i - 1] > 2]
        band = max(12.0, min(40.0, float(median(gaps)) * 0.55)) if gaps else 24.0
    else:
        band = 24.0
    for my, mx, mp in marks:
        best = None
        score = 1e9
        for title in titles:
            if title["x"] > mx + 8:
                continue
            dy = abs(title["y"] - my)
            if dy > band:
                continue
            cur = dy * 3 + max(0.0, mx - title["x"]) * 0.01
            if cur < score:
                score = cur
                best = title
        if best is not None:
            best["page"] = mp
    return titles, named


def _columns(titles: list[dict]) -> list[list[dict]]:
    if not titles:
        return []
    xs = sorted(t["x"] for t in titles)
    gaps = [xs[i] - xs[i - 1] for i in range(1, len(xs))]
    small = [g for g in gaps if 1 < g < 100]
    typical = float(median(small)) if small else 30.0
    split = max(100.0, typical * 3)
    ordered = sorted(titles, key=lambda t: (t["x"], t["y"]))
    cols: list[list[dict]] = []
    cur: list[dict] = []
    last_x: float | None = None
    for title in ordered:
        if last_x is not None and title["x"] - last_x > split and cur:
            cols.append(cur)
            cur = []
        cur.append(title)
        last_x = title["x"]
    if cur:
        cols.append(cur)
    for col in cols:
        col.sort(key=lambda t: (t["y"], t["x"]))
    cols.sort(key=lambda col: min(t["x"] for t in col))
    return cols


def _catalog_rows(
    blocks: list[dict], unused: list[str], unused_keys: set[str], toc: ModuleType
) -> list[tuple[str, str, int | None]]:
    titles, named = _attach_marks(blocks, unused_keys, toc)
    rows: list[tuple[str, str, int | None]] = []
    for col in _columns(titles):
        classified = [
            (_classify_line(t["text"], t["page"], unused_keys, toc), t) for t in col
        ]
        has_letter = any(
            row and row[0] == "section" and row[1][:1] in "ABCDE" for row, _t in classified
        )
        has_unit = any(row and row[0] == "chapter" for row, _t in classified)
        if has_letter and not has_unit:
            insert = None
            for i, unit in enumerate(unused):
                if toc._is_appendix_heading(unit) or toc._is_intro_heading(unit):
                    continue
                insert = unused.pop(i)
                unused_keys.discard(_unit_key(insert))
                break
            if insert:
                rows.append(("chapter", insert, None))
        for row, _title in classified:
            if row:
                rows.append(row)
    for label, page in named:
        rows.append(("named", label, page))
    return rows


def _opener_idx(blocks: list[dict], toc_idx: set[int], toc: ModuleType) -> set[int]:
    letters: dict[int, set[str]] = {}
    for block in blocks:
        idx = int(block.get("page_idx") or -1)
        if idx < 0 or idx in toc_idx:
            continue
        match = toc.LETTER_BODY_SECTION.match(_norm(str(block.get("text") or "")))
        if match:
            letters.setdefault(idx, set()).add(match.group(1).upper())
    return {idx for idx, found in letters.items() if len(found) >= 3}


def _mineru_units(blocks: list[dict], toc_idx: set[int], toc: ModuleType) -> list[str]:
    openers = _opener_idx(blocks, toc_idx, toc)
    counts: dict[str, int] = {}
    pages: dict[str, set[int]] = {}
    order: list[str] = []
    keys: set[str] = set()
    for block in blocks:
        idx = int(block.get("page_idx") or -1)
        if idx in toc_idx:
            continue
        if str(block.get("type") or "") not in {"header", "footer"}:
            continue
        text = _strip_running(_norm(str(block.get("text") or "")), toc)
        if not text or toc._is_contents_title(text) or toc.SELF_ASSESS.match(text):
            continue
        if toc.LETTER_BODY_SECTION.match(text):
            continue
        if not _can_be_group_head(text, toc):
            continue
        key = _unit_key(text)
        counts[key] = counts.get(key, 0) + 1
        pages.setdefault(key, set()).add(idx)
        if key not in keys:
            keys.add(key)
            order.append(text)
    out: list[str] = []
    for title in order:
        key = _unit_key(title)
        if counts[key] < 3:
            continue
        seen = pages.get(key) or set()
        if seen and seen <= openers:
            continue
        out.append(title)
    return out


def _page_heads(path: Path) -> list[str]:
    heads: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = _ATX.sub("", raw).strip()
        if not line or line.startswith("!["):
            continue
        heads.append(line)
        if len(heads) >= 12:
            break
    return heads


def _mineru_page_heads(blocks: list[dict], toc_pages: set[int]) -> dict[int, list[str]]:
    extra: dict[int, list[str]] = {}
    for block in blocks:
        if str(block.get("type") or "") != "header":
            continue
        try:
            pdf = int(block.get("page_idx") or -1) + 1
        except (TypeError, ValueError):
            continue
        if pdf <= 0 or pdf in toc_pages:
            continue
        text = _norm(str(block.get("text") or ""))
        if text:
            extra.setdefault(pdf, []).append(text)
    return extra


def _letter_events(
    book: Path, toc_pages: set[int], toc: ModuleType, blocks: list[dict] | None = None
) -> list[tuple[int, str]]:
    extra = _mineru_page_heads(blocks or [], toc_pages)
    events: list[tuple[int, str]] = []
    for path in sorted((book / "pages").glob("*.md")):
        try:
            pdf = int(path.stem)
        except ValueError:
            continue
        if pdf in toc_pages:
            continue
        heads = _page_heads(path) + extra.get(pdf, [])
        letters = []
        for head in heads:
            match = toc.LETTER_BODY_SECTION.match(head)
            if match:
                letters.append(match.group(1).upper())
        if any(toc.SELF_ASSESS.match(head) for head in heads):
            events.append((pdf, "self"))
            continue
        if len(set(letters)) >= 3:
            events.append((pdf, "opener"))
            continue
        if letters:
            events.append((pdf, letters[0]))
    return events


def _fill_print_pages(
    rows: list[tuple[str, str, int | None]],
    events: list[tuple[int, str]],
    toc: ModuleType,
) -> list[tuple[str, str, int | None]]:
    if not any(kind == "section" and label[:1] in "ABCDE" for kind, label, _page in rows):
        return rows
    cursor = 0

    def take(kind: str) -> int:
        nonlocal cursor
        index = cursor
        while index < len(events):
            pdf, ev = events[index]
            if ev == "opener" and kind != "opener":
                return 0
            if ev == kind:
                cursor = index + 1
                return pdf
            index += 1
        return 0

    assigned: list[tuple[str, str, int | None, int]] = []
    pending_a = False
    last_opener = 0
    for kind, label, page in rows:
        pdf = 0
        if kind == "chapter" and not toc._is_appendix_heading(label):
            last_opener = take("opener")
            pending_a = True
            pdf = last_opener
        elif kind == "section":
            letter = label[0]
            if letter == "A" and pending_a:
                pending_a = False
                pdf = last_opener
            else:
                pdf = take(letter)
        elif kind == "self":
            pending_a = False
            pdf = take("self")
        assigned.append((kind, label, page, pdf))

    offsets = [
        pdf - page
        for kind, _label, page, pdf in assigned
        if page and pdf and kind != "named"
    ]
    offset = int(median(offsets)) if offsets else 0
    out: list[tuple[str, str, int | None]] = []
    for kind, label, page, pdf in assigned:
        if page is None and pdf and offset:
            page = pdf - offset
        if page is not None and page <= 0:
            page = None
        out.append((kind, label, page))
    return out


def _format_rows(rows: list[tuple[str, str, int | None]]) -> list[str]:
    lines: list[str] = []
    for kind, label, page in rows:
        if kind == "chapter":
            lines.append(label)
            continue
        if page:
            lines.append(f"{label} {page}")
        else:
            lines.append(label)
    return lines


def _toc_heading(rows: list[tuple[str, str, int | None]]) -> str:
    for _kind, label, _page in rows:
        if _CJK.search(label) and (
            label.startswith("第") or label in {"引论", "附录", "前言"}
        ):
            return "目录"
    return "Contents"


def _write_page(path: Path, lines: list[str], heading: str | None) -> None:
    body = [f"## {heading}", ""] if heading else []
    body.extend(lines)
    text = "\n".join(body).rstrip() + "\n"
    try:
        from source_text import write_source_atomic
    except ImportError:
        write_source_atomic = None
    if write_source_atomic is not None:
        write_source_atomic(path, text)
        return
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def mark_toc_pages(book_id: str, pages: str, root: str = "") -> dict[str, object]:
    """Manually mark printed TOC pages, recover header/footer page marks, rebuild toc.json."""
    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    wanted = _parse_pages(pages)
    base = Path(root).resolve() if root else Path.cwd()
    book = _book(base, sid)
    toc = _load_toc_mod()
    blocks = _mineru_blocks(book)
    unused = _mineru_units(blocks, {p - 1 for p in wanted}, toc)
    unused_keys = {_unit_key(u) for u in unused}
    written: list[str] = []
    catalog: list[str] = []
    all_rows: list[tuple[str, str, int | None]] = []

    per_page: list[tuple[int, list[tuple[str, str, int | None]]]] = []
    for pdf in wanted:
        page_blocks = [b for b in blocks if int(b.get("page_idx") or -1) == pdf - 1]
        rows = _catalog_rows(page_blocks, unused, unused_keys, toc)
        for kind, label, _page in rows:
            if kind == "chapter":
                unused_keys.discard(_unit_key(label))
                unused[:] = [u for u in unused if _unit_key(u) != _unit_key(label)]
        per_page.append((pdf, rows))
        all_rows.extend(rows)

    filled = _fill_print_pages(
        all_rows, _letter_events(book, set(wanted), toc, blocks), toc
    )
    heading = _toc_heading(filled)
    index = 0
    for pdf, rows in per_page:
        chunk = filled[index : index + len(rows)]
        index += len(rows)
        lines = _format_rows(chunk)
        catalog.extend(lines)
        _write_page(
            book / "pages" / f"{pdf:04d}.md",
            lines,
            heading if pdf == wanted[0] else None,
        )
        written.append(f"{pdf:04d}.md")

    mineru_toc = book / "mineru_toc.json"
    payload = {"toc_pages": wanted, "print_to_pdf": {}}
    if mineru_toc.is_file():
        try:
            old = json.loads(mineru_toc.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                payload["print_to_pdf"] = old.get("print_to_pdf") or {}
        except json.JSONDecodeError:
            pass
    mineru_toc.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (book / "toc_force.json").write_text(
        json.dumps({"toc_pages": wanted, "source": "manual"}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    built = toc.build_toc(str(base), sid)
    return {
        "book_id": sid,
        "pages": wanted,
        "written": written,
        "catalog_lines": catalog,
        "heading": heading,
        "toc_pages": built.get("toc_pages"),
        "entries": built.get("entries"),
        "levels": built.get("levels"),
        "ok": built.get("ok", True),
    }
