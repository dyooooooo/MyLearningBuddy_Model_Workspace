from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_LESSON_ONLY = re.compile(r"^\d{1,2}$")
_LI = re.compile(r"<li\b[^>]*kb-page-toc-row", re.I)
_FENCE = re.compile(r"::: kb-page-toc\r?\n([\s\S]*?)\r?\n:::")
_NAV_SRC = re.compile(r'<nav class="kb-page-toc"(?: data-toc-source="(\w+)")?')
_ROW = re.compile(
    r'<li class="kb-page-toc-row kb-page-toc-row--lv(\d+)[^"]*"[^>]*>'
    r'.*?<span class="kb-page-toc-title">([^<]*)</span>',
    re.S,
)
_ATX = re.compile(r"^#{1,6}\s+")
_SHRINK_RATIO = 0.6
# 反向护栏：涨到原来 3 倍以上说明分页算错了（小节全塞进来了）
_GROW_RATIO = 3.0
# 章与节一定印。更深的层级只有带印张页码时才印 —— 那说明它是从印刷目录读出来的
# （语文教材的篇名就是这种，level 2 但目录页上确实有）。`toc_extract` 从正文标题
# 扫出来的小节 print_page 恒为 None，它们只活在 toc.json 与左栏目录树里。
_PRINT_MAX_LEVEL = 1
_IMAGE_ONLY = re.compile(r"^!\[[^\]]*\]\([^)]*\)$")


def _prints_on_page(entry: dict[str, Any]) -> bool:
    """Should this row appear on the printed TOC page?

    章（level 0）与节（level 1）总是印。更深的层只有本来就印在目录页上的才印 ——
    判据是 `from_body`（`build_toc` 给 `toc_extract` 扒来的小节打的标），不是有没有
    `print_page`：正文扒来的小节也会按 offset 反推出 `print_page`，光看页码分不出来源。
    """
    if entry.get("from_body"):
        return (_as_int(entry.get("level")) or 0) <= _PRINT_MAX_LEVEL
    return True


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def write_toc_pages(book_id: str, apply: str = "scan", force: bool = False) -> dict[str, Any]:
    """Write official toc pages from toc.json. apply=scan stats only; apply=fix writes pages/ + mid/."""
    from app import library, page_sync
    from app.logsetup import digest_text, dump_payload, event

    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    mode = (apply or "scan").strip().lower() or "scan"
    if mode not in {"scan", "fix"}:
        raise ValueError("apply must be scan or fix")
    forced = _as_bool(force)

    toc = library.read_toc(sid)
    all_entries = [row for row in (toc.get("entries") or []) if isinstance(row, dict)]
    # 写盘只用印刷目录形态；巡检（_scan_pages）仍拿完整 entries，左栏三层不受影响
    entries = [row for row in all_entries if _prints_on_page(row)]
    toc_pages = [int(n) for n in (toc.get("toc_pages") or []) if str(n).isdigit() and int(n) > 0]
    mapping = toc.get("print_to_pdf") if isinstance(toc.get("print_to_pdf"), dict) else {}
    empty_pages = (not toc_pages) and bool(entries)
    unmapped = _unmapped_rows(entries, mapping)

    if empty_pages:
        event(
            "toc.write",
            book_id=sid,
            apply=mode,
            warning="toc_pages_empty",
            entries=len(entries),
            unmapped=len(unmapped),
        )

    blocked: list[dict[str, Any]] = []
    wrote_pages = False
    split_failed = False
    texts: dict[int, str] = {}
    chunks: list[list[dict[str, Any]]] | None = None
    if mode == "fix" and toc_pages:
        texts = _page_texts(sid, toc_pages)
        chunks = _split_entries(toc_pages, entries, texts)
        if chunks is None:
            # 分不清哪条归哪页时宁可不动原文：全塞第一页会连插图一起覆盖掉
            split_failed = True
            event(
                "toc.write",
                book_id=sid,
                apply=mode,
                warning="split_failed",
                entries=len(entries),
                toc_pages=toc_pages,
                page_rows=[len(_labels_on_page(texts.get(n, ""))) for n in toc_pages],
            )
    if chunks is not None:
        for index, page_no in enumerate(toc_pages):
            rows = chunks[index] if index < len(chunks) else []
            # 带上原文：装饰插图要原位留住，否则整页重写会把它们一起丢掉
            body = _official_page(rows, heading=(index == 0), source=texts.get(page_no, ""))
            mid = f"::: kb-page-toc\n{body.rstrip()}\n:::\n"
            page_path = library.book_dir(sid) / "pages" / f"{page_no:04d}.md"
            mid_path = library.book_dir(sid) / "mid" / f"{page_no:04d}.md"
            before_page = _file_text(page_path)
            before_mid = _file_text(mid_path)
            existing_n = len(_labels_on_page(texts.get(page_no, "") or before_page))
            new_n = len(rows)
            guard = ""
            if not forced and existing_n > 0:
                if new_n < existing_n * _SHRINK_RATIO:
                    guard = "shrink_guard"
                elif new_n > existing_n * _GROW_RATIO:
                    guard = "grow_guard"
            if guard:
                dump_payload(f"toc-{guard}-before-{sid}-{page_no:04d}.md", before_page)
                dump_payload(f"toc-{guard}-after-{sid}-{page_no:04d}.md", body)
                event(
                    "toc.write",
                    book_id=sid,
                    page_no=page_no,
                    warning=guard,
                    existing_rows=existing_n,
                    new_rows=new_n,
                    ratio=_SHRINK_RATIO if guard == "shrink_guard" else _GROW_RATIO,
                    force=False,
                )
                blocked.append(
                    {
                        "page_no": page_no,
                        "existing_rows": existing_n,
                        "new_rows": new_n,
                        "warning": guard,
                    }
                )
                continue
            try:
                with page_sync.suppressing(page_path, mid_path):
                    library.write_page(sid, page_no, body, sync=False)
                    library.write_mid(sid, page_no, mid, sync=False)
                    page_sync.write_stamp(sid, page_no, body, mid)
            except Exception:
                dump_payload(f"toc-write-{sid}-{page_no:04d}.md", body)
                raise
            wrote_pages = True
            event(
                "toc.write",
                book_id=sid,
                page_no=page_no,
                rows=len(rows),
                pages_sha_before=digest_text(before_page)["sha"],
                pages_sha_after=digest_text(body)["sha"],
                mid_sha_before=digest_text(before_mid)["sha"],
                mid_sha_after=digest_text(mid)["sha"],
                unmapped=[item for item in unmapped if item.get("page_no") == page_no],
                bytes=len(body.encode("utf-8")),
                suppressing=True,
                force=forced,
            )

    stats = _scan_pages(sid, toc_pages, all_entries, mapping)
    stats.update(
        {
            "book_id": sid,
            "ok": True,
            "apply": mode,
            "entries": len(entries),
            "entries_all": len(all_entries),
            "toc_pages": toc_pages,
            "unmapped": unmapped,
            "unmapped_count": len(unmapped),
            "empty_toc_pages": empty_pages,
            "wrote": wrote_pages,
            "blocked": bool(blocked),
            "blocked_pages": blocked,
            "split_failed": split_failed,
            "force": forced,
        }
    )
    return stats


def _file_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _page_texts(book_id: str, toc_pages: list[int]) -> dict[int, str]:
    from app import library

    out: dict[int, str] = {}
    root = library.book_dir(book_id)
    for n in toc_pages:
        text = _file_text(root / "pages" / f"{n:04d}.md")
        if not text:
            text = _file_text(root / "mid" / f"{n:04d}.md")
        out[n] = text
    return out


def _labels_on_page(text: str) -> list[str]:
    if "kb-page-toc-title" in text:
        return re.findall(r'kb-page-toc-title">([^<]*)</span>', text)
    fence = _FENCE.search(text or "")
    body = fence.group(1) if fence else (text or "")
    labels: list[str] = []
    for raw in body.splitlines():
        line = _ATX.sub("", raw).strip()
        if not line or line in {"目录", "Contents", "CONTENTS"}:
            continue
        # 目录页常在章名旁配缩略图，纯插图行不是一条目录
        if _IMAGE_ONLY.match(line):
            continue
        label, _page = _peel_row(line)
        if label:
            labels.append(label)
    return labels


def _peel_row(text: str) -> tuple[str, int | None]:
    raw = _ATX.sub("", (text or "").strip())
    if not raw:
        return "", None
    try:
        import toc_lexicon

        return toc_lexicon.strip_page(raw)
    except Exception:
        match = re.search(r"\s*/?\s*(\d{1,4})$", raw)
        if match:
            return raw[: match.start()].strip(), int(match.group(1))
        return raw, None


def _entry_print(item: dict[str, Any]) -> int | None:
    page = item.get("print_page")
    if page in (None, ""):
        return None
    try:
        return int(page)
    except (TypeError, ValueError):
        return None


def _match_entry_index(entries: list[dict[str, Any]], label: str, page: int | None) -> int:
    key = _norm_label(label)
    if not key:
        return -1
    hits: list[int] = []
    for index, item in enumerate(entries):
        if _norm_label(str(item.get("label") or "")) != key:
            continue
        item_page = _entry_print(item)
        if page is not None and item_page is not None and item_page != page:
            continue
        hits.append(index)
    if page is not None:
        return hits[0] if hits else -1
    return hits[0] if len(hits) == 1 else -1


def _count_rows(text: str) -> int:
    if "kb-page-toc-row" in text:
        return len(_LI.findall(text))
    return len(_labels_on_page(text))


def _norm_label(text: str) -> str:
    """归一到能跨「第一章 / 第1章」和「附 录 / 附录」比对的键，比对失败就分不出页。"""
    raw = _ATX.sub("", (text or "").strip())
    try:
        import toc_lexicon

        raw, _page = toc_lexicon.strip_page(raw)
    except Exception:
        raw = re.sub(r"\s*/?\s*\d{1,4}$", "", raw).strip()
    raw = re.sub(r"[.…·•、]+$", "", raw).strip()
    try:
        from build_toc import _chapter_key

        key = _chapter_key(raw)
        if key:
            return key
    except Exception:
        pass
    return re.sub(r"\s+", "", raw)


def _split_entries(
    toc_pages: list[int],
    entries: list[dict[str, Any]],
    texts: dict[int, str],
) -> list[list[dict[str, Any]]] | None:
    """按印刷页把 entries 切块。切不出来返回 None，调用方不写盘。"""
    if not toc_pages:
        return []
    if len(toc_pages) == 1:
        return [entries]
    starts: list[int] = []
    for index, n in enumerate(toc_pages):
        if index == 0:
            starts.append(0)
            continue
        earliest = -1
        body = texts.get(n, "")
        fence = _FENCE.search(body or "")
        raw_body = fence.group(1) if fence else (body or "")
        if "kb-page-toc-title" in body:
            for title in re.findall(r'kb-page-toc-title">([^<]*)</span>', body):
                pos = _match_entry_index(entries, title, None)
                if pos >= 0 and (earliest < 0 or pos < earliest):
                    earliest = pos
        else:
            for raw in raw_body.splitlines():
                line = _ATX.sub("", raw).strip()
                if not line or line in {"目录", "Contents", "CONTENTS"} or _IMAGE_ONLY.match(line):
                    continue
                label, page = _peel_row(line)
                pos = _match_entry_index(entries, label, page)
                if pos >= 0 and (earliest < 0 or pos < earliest):
                    earliest = pos
        starts.append(earliest)
    if (
        starts
        and all(item >= 0 for item in starts)
        and starts == sorted(starts)
        and len(set(starts)) == len(starts)
    ):
        chunks: list[list[dict[str, Any]]] = []
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(entries)
            chunks.append(entries[start:end])
        return chunks
    counts = [_count_rows(texts.get(n, "")) for n in toc_pages]
    if counts and sum(counts) == len(entries):
        out: list[list[dict[str, Any]]] = []
        cur = 0
        for n in counts:
            out.append(entries[cur : cur + n])
            cur += n
        return out
    return None


def _official_line(entry: dict[str, Any]) -> str:
    """One printed TOC row: `标签` or `标签 页码`.

    章行原来无条件不带页码，于是整条链**不幂等**：书上印的是
    `第一章 运动的描述 / 1`，第一遍 `build_toc` 从原文读到 `print_page=1`，
    `write_toc_pages` 把这一页重写成不带页码的 `第1章 运动的描述`；第二遍
    `build_toc` 读到的就是重写后的版本，四个章的 `print_page` 全变 null，
    `print_to_pdf` 也跟着掉一条。跑一遍看不出来，跑两遍数据退化一层。

    只有本来就没有页码的章（`print_page` 为空）才照旧只写标签。
    """
    label = str(entry.get("label") or "").strip()
    if not label:
        return ""
    if _LESSON_ONLY.match(label):
        return label
    page = entry.get("print_page")
    if page not in (None, ""):
        return f"{label} {int(page)}"
    return label


def _keep_figures(source: str) -> tuple[list[str], list[str]]:
    """Image lines from the source page, split into before-the-list and after-the-list.

    目录页常配装饰插图（本册第 6、7 页各两张）。整页按 `toc.json` 重写会把它们一起
    丢掉 —— 用户报的「插图丢失」就是这个。按插图在原文里落在第一条目录行之前还是
    最后一条之后，原位保留。夹在目录行中间的插图归到后面，不打断目录列表。
    """
    lines = [raw.strip() for raw in (source or "").splitlines()]
    first_row = -1
    for index, line in enumerate(lines):
        if not line or _IMAGE_ONLY.match(line):
            continue
        text = _ATX.sub("", line).strip()
        if not text or text in {"目录", "Contents", "CONTENTS"}:
            continue
        first_row = index
        break
    lead: list[str] = []
    tail: list[str] = []
    for index, line in enumerate(lines):
        if not _IMAGE_ONLY.match(line):
            continue
        if first_row >= 0 and index < first_row:
            lead.append(line)
        else:
            tail.append(line)
    return lead, tail


def _official_page(rows: list[dict[str, Any]], heading: bool, source: str = "") -> str:
    lead, tail = _keep_figures(source)
    lines = list(lead)
    if lead:
        lines.append("")
    if heading:
        lines.append("目录")
    body = [line for line in (_official_line(row) for row in rows) if line]
    lines.extend(body)
    if not body and not heading:
        lines.append("目录")
    if tail:
        lines.append("")
        lines.extend(tail)
    return "\n".join(lines).rstrip() + "\n"


def _unmapped_rows(entries: list[dict[str, Any]], mapping: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        page = entry.get("print_page")
        if page in (None, ""):
            continue
        key = str(int(page))
        if key in mapping and int(mapping[key] or 0) > 0:
            continue
        out.append(
            {
                "index": index,
                "label": str(entry.get("label") or ""),
                "print_page": int(page),
            }
        )
    return out


def _scan_pages(
    book_id: str,
    toc_pages: list[int],
    entries: list[dict[str, Any]],
    mapping: dict[str, Any],
) -> dict[str, Any]:
    from app import library, surfaces

    rows = 0
    guess = 0
    miss = 0
    toc_hit = 0
    level_mismatch = 0
    sources: list[str] = []
    mismatches: list[dict[str, Any]] = []
    expected = {(str(item.get("label") or "").strip(), _as_int(item.get("print_page"))): item for item in entries}
    for page_no in toc_pages:
        mid_path = library.book_dir(book_id) / "mid" / f"{page_no:04d}.md"
        page_path = library.book_dir(book_id) / "pages" / f"{page_no:04d}.md"
        raw = _file_text(mid_path) or _file_text(page_path)
        html = surfaces.prepare_page(
            raw,
            print_to_pdf=mapping,
            book_id=book_id,
            toc_entries=entries,
        )
        nav = _NAV_SRC.search(html)
        source = (nav.group(1) if nav and nav.group(1) else "guess") if nav else "miss"
        sources.append(source)
        if source == "guess":
            guess += 1
        elif source == "miss":
            miss += 1
        elif source == "toc":
            toc_hit += 1
        for level, title in _ROW.findall(html):
            rows += 1
            label = _unescape(title).strip()
            hit = None
            for key, item in expected.items():
                if key[0] == label:
                    hit = item
                    break
            if hit is None:
                continue
            want = _as_int(hit.get("level"))
            got = int(level)
            if want is not None and want != got:
                level_mismatch += 1
                mismatches.append({"label": label, "want": want, "got": got, "page_no": page_no})
    return {
        "rows": rows,
        "level_mismatch": level_mismatch,
        "guess": guess,
        "miss": miss,
        "toc_hit": toc_hit,
        "mismatches": mismatches[:40],
        "nav_sources": sources,
    }


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _unescape(text: str) -> str:
    return (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]
