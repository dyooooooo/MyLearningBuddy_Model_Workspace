from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_ATX = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*$")
_H2_ONLY = re.compile(r"^##(?!#)[ \t]+")
_IMG_HASH = re.compile(r"mineru%2F([a-f0-9]{64})")
_BARE_IMG = re.compile(r"^!\[[^\]]*\]\([^)]+\)$")
_BULLET = re.compile(r"^[\u25cf\u2022\u00b7]\s*")
_FENCE_OPEN = re.compile(r"^::: kb-[\w-]+\s*$")
_FENCE_CLOSE = re.compile(r"^:::\s*$")
_CENTER_CHECK = re.compile(
    r"::: kb-center\r?\n(\u221a [^\n]+)\r?\n:::\r?\n"
)
_CENTER_HEADING = re.compile(
    r"::: kb-center\r?\n(#{1,6}[^\n]+)\r?\n:::\r?\n"
)
_TOC_SECTION = re.compile(r"^(\d+)\.(\d+)\s+(.+)$")
_SECTION_ATX = re.compile(r"^#{1,6}[ \t]+\d+\.\d+[ \t]+\S")
_CHECK = "\u221a"
_FOCUS = "\u5b66\u4e60\u805a\u7126"
_REVIEW = "\u77e5\u8bc6\u56de\u653e"
_BANK = "\u8d44\u6599\u5e93"
_EXPERIMENT = "\u5b9e\u9a8c\u63a2\u7a76"
_LEGEND = "\u660e\u786e\u672c\u8282\u4e3b\u8981\u7684\u5b66\u4e60\u76ee\u6807"
_FOCUS_HEAD = "## \u5b66 \u4e60 \u805a \u7126"
_EXPERIMENT_HEAD = "## \u5b9e \u9a8c \u63a2 \u7a76"
_INNER_MAX = 16
_LINK = "\u94fe\u63a5\u5b66\u79d1"
_GUIDE = "\u5b66\u4e60\u6307\u5357"
_THINK = "\u60f3\u4e00\u60f3"
_WRITE = "\u4e66\u5199\u8868\u8fbe"
_EXPAND = "\u62d3\u5c55\u89c6\u91ce"
_HISTORY = "\u5316\u5b66\u53f2\u8bdd"
_HAT_W = (38.0, 55.0)
_HAT_H = (28.0, 42.0)
_THINK_W = (63.0, 72.0)
_THINK_H = (33.0, 39.0)
_WRITE_W = (76.0, 86.0)
_WRITE_H = (33.0, 39.0)
_HISTORY_W = (56.0, 60.0)
_HISTORY_H = (39.0, 43.0)
_COLUMNS = frozenset(
    {
        _FOCUS,
        _REVIEW,
        _THINK,
        _WRITE,
        _BANK,
        _EXPAND,
        _HISTORY,
        _EXPERIMENT,
        _LINK,
    }
)
_DASH_FENCE = ["::: kb-dash", "", ":::"]
_VERBS = (
    "\u4e86\u89e3",
    "\u77e5\u9053",
    "\u638c\u63e1",
    "\u7406\u89e3",
    "\u5b66\u4f1a",
    "\u8ba4\u8bc6",
    "\u611f\u609f",
    "\u5217\u4e3e",
    "\u8fd0\u7528",
    "\u80fd",
    "\u57fa\u4e8e",
    "\u7ed3\u5408",
)


def _book_dir(book_id: str, root: str) -> Path:
    base = Path(root).resolve() if root else Path.cwd()
    if (base / "textbooks").is_dir():
        return base / "textbooks" / book_id
    return base / book_id


def _read_meta(book: Path) -> dict[str, Any]:
    path = book / "meta.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def is_chemistry_textbook(meta: dict[str, Any] | None) -> bool:
    if not isinstance(meta, dict):
        return False
    subject = str(meta.get("subject") or "").strip().lower()
    category = str(meta.get("category") or "").strip().lower()
    return subject == "chemistry" and category in {"textbook", "\u6559\u6750"}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _heading_name(line: str) -> str | None:
    match = _ATX.match(line.strip())
    return _compact(match.group(2)) if match else None


def _line_hash(line: str) -> str | None:
    match = _IMG_HASH.search(line)
    return match.group(1) if match else None


def _looks_focus_item(line: str) -> bool:
    text = line.strip()
    if not text or text.startswith(("!", ":::", "<")):
        return False
    text = _ATX.sub(r"\2", text).lstrip("\u221a\u2713\u25cf\u2022\u00b7 \t")
    return text.startswith(_VERBS)


def _block_text(block: dict[str, Any]) -> str:
    content = block.get("content") or {}
    for key in ("title_content", "paragraph_content"):
        parts = content.get(key)
        if not parts:
            continue
        if isinstance(parts, list):
            return "".join(
                item.get("content", "") if isinstance(item, dict) else str(item)
                for item in parts
            )
        return str(parts)
    return ""


def _block_hash(block: dict[str, Any]) -> str:
    path = (((block.get("content") or {}).get("image_source") or {}).get("path") or "")
    return path.split("/")[-1].replace(".jpg", "")


def _load_blocks(book: Path) -> list[dict[str, Any]]:
    path = book / "mineru" / "blocks.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _collect_masks(blocks: list[dict[str, Any]]) -> dict[int, dict[str, set[str]]]:
    by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        by_page[int(block.get("page_idx") or 0)].append(block)
    out: dict[int, dict[str, set[str]]] = {}
    for idx, items in by_page.items():
        focus_box = None
        review_box = None
        for block in items:
            name = _compact(_block_text(block))
            if name == _FOCUS:
                focus_box = block.get("bbox")
            elif name.startswith(_REVIEW):
                review_box = block.get("bbox")
        checks: set[str] = set()
        titles: set[str] = set()
        low_icons: set[str] = set()
        for block in items:
            if block.get("type") != "image":
                continue
            box = block.get("bbox") or [0, 0, 0, 0]
            width, height = box[2] - box[0], box[3] - box[1]
            digest = _block_hash(block)
            if (
                digest
                and box[1] >= 380
                and width < 160
                and height < 80
                and not _is_think_box(width, height)
                and not _is_write_box(width, height)
            ):
                low_icons.add(digest)
            if width >= 40 or height >= 40:
                continue
            if not digest:
                continue
            is_title = False
            for title_box in (focus_box, review_box):
                if not title_box:
                    continue
                mid_y = (box[1] + box[3]) / 2
                title_y = (title_box[1] + title_box[3]) / 2
                if abs(mid_y - title_y) < 22 and box[0] <= title_box[0] + 8:
                    is_title = True
                    break
            if is_title:
                titles.add(digest)
                continue
            if focus_box:
                same_col = abs(box[0] - focus_box[0]) < 80 or box[0] >= 640
                y0 = focus_box[3] - 4
                y1 = review_box[1] if review_box else 400
                if same_col and y0 <= (box[1] + box[3]) / 2 <= y1 + 8:
                    checks.add(digest)
            if (not focus_box) and review_box and box[0] >= 640 and box[1] < review_box[1]:
                checks.add(digest)
        if not focus_box and checks:
            rights = []
            for block in items:
                if block.get("type") != "image":
                    continue
                digest = _block_hash(block)
                if digest in checks:
                    box = block.get("bbox") or [0, 0, 0, 0]
                    rights.append((box[1], digest))
            if rights:
                rights.sort()
                titles.add(rights[0][1])
                checks.discard(rights[0][1])
        out[idx + 1] = {"checks": checks, "titles": titles, "low_icons": low_icons}
    return out


def _peel_center_checks(text: str, check_hashes: set[str]) -> str:
    if not check_hashes:
        return text
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "::: kb-center":
            chunk = [lines[i]]
            j = i + 1
            while j < len(lines) and not _FENCE_CLOSE.match(lines[j].strip()):
                chunk.append(lines[j])
                j += 1
            if j < len(lines):
                chunk.append(lines[j])
            body = "\n".join(chunk[1:-1]) if len(chunk) >= 2 else ""
            hashes = set(_IMG_HASH.findall(body))
            only_check = hashes and hashes <= check_hashes and not re.search(
                r"^#{1,6} ", body, re.M
            )
            if only_check:
                i = j + 1
                continue
            out.extend(chunk)
            i = j + 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _drop_checkbox_lines(text: str, check_hashes: set[str]) -> str:
    if not check_hashes:
        return text
    kept = []
    for line in text.splitlines():
        digest = _line_hash(line)
        if digest and digest in check_hashes and _BARE_IMG.match(line.strip()):
            continue
        kept.append(line)
    return "\n".join(kept)


def _insert_missing_focus(text: str) -> str:
    lines = text.splitlines()
    names = [_heading_name(line) for line in lines]
    if _FOCUS in names or _REVIEW not in names:
        return text
    insert_at = None
    for i, line in enumerate(lines):
        if _looks_focus_item(line):
            insert_at = i
            break
        if _heading_name(line) == _REVIEW:
            break
    if insert_at is None:
        return text
    lines = lines[:insert_at] + [_FOCUS_HEAD, ""] + lines[insert_at:]
    return "\n".join(lines)


def _format_items(text: str) -> str:
    out: list[str] = []
    mode = None
    for line in text.splitlines():
        stripped = line.strip()
        name = _heading_name(stripped)
        if name == _FOCUS:
            mode = "focus"
            out.append(line)
            continue
        if name == _REVIEW:
            mode = "review"
            out.append(line)
            continue
        if _H2_ONLY.match(stripped) and name not in {_FOCUS, _REVIEW}:
            mode = None
            out.append(line)
            continue
        if mode == "focus" and stripped and not stripped.startswith((":::", "!")):
            if _looks_focus_item(stripped) or (
                _ATX.match(stripped) and name not in {_FOCUS, _REVIEW}
            ):
                body = _ATX.sub(r"\2", stripped).lstrip("\u221a\u2713 \t")
                out.append(f"{_CHECK} {body}")
                continue
        if mode == "review" and _BULLET.match(stripped):
            out.append(f"\u25cf {_BULLET.sub('', stripped)}")
            continue
        out.append(line)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text if text.endswith("\n") else text + "\n"


def _unwrap_center_checks(text: str) -> str:
    return _CENTER_CHECK.sub(r"\1\n\n", text)


def _wrap_focus_icon(text: str, title_hashes: set[str]) -> str:
    if not title_hashes:
        return text
    alt = "|".join(re.escape(item) for item in title_hashes)
    pattern = re.compile(
        r"::: kb-center\r?\n"
        r"(!\[[^\]]*\]\([^)]*(?:" + alt + r")[^)]*\))\r?\n"
        r":::\r?\n+"
        r"(##[^\n]*\u5b66[^\n]*\u805a[^\n]*\u7126[^\n]*)\r?\n"
    )
    return pattern.sub(
        lambda m: (
            "::: kb-section-icon\n"
            f"{m.group(1)}\n\n"
            f"{m.group(2)}\n"
            ":::\n\n"
        ),
        text,
    )


def _tidy(text: str) -> str:
    text = re.sub(r"::: kb-center\n+:::\n*", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text if text.endswith("\n") else text + "\n"


def apply_focus_review(
    source: str, mid: str, masks: dict[str, set[str]] | None = None
) -> tuple[str, str]:
    """\u5b66\u4e60\u805a\u7126\u6761\u76ee\u5199\u6210\u5bf9\u53f7\uff0c\u77e5\u8bc6\u56de\u653e\u4fdd\u6301\u5b9e\u5fc3\u5706\u70b9\u3002"""
    if _LEGEND in source:
        return source, mid
    compact = _compact(source)
    if _FOCUS not in compact and _REVIEW not in compact:
        return source, mid
    masks = masks or {}
    checks = masks.get("checks") or set()
    titles = masks.get("titles") or set()
    new_src = _drop_checkbox_lines(source, checks)
    new_src = _insert_missing_focus(new_src)
    new_src = _format_items(new_src)
    new_mid = _peel_center_checks(mid, checks)
    new_mid = _drop_checkbox_lines(new_mid, checks)
    new_mid = _insert_missing_focus(new_mid)
    new_mid = _format_items(new_mid)
    new_mid = _unwrap_center_checks(new_mid)
    new_mid = _wrap_focus_icon(new_mid, titles)
    new_mid = _dash_after_focus_review(new_mid)
    return _tidy(new_src), _tidy(new_mid)


def _looks_review_item(line: str) -> bool:
    return bool(_BULLET.match(line.strip()))


def _copy_items_and_dash(
    lines: list[str], start: int, out: list[str], kind: str
) -> int:
    n = len(lines)
    i = start
    pred = _looks_focus_item if kind == "focus" else _looks_review_item
    while i < n and not lines[i].strip():
        out.append(lines[i])
        i += 1
    if i < n and lines[i].strip() == "::: kb-dash":
        return i
    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            out.append(lines[i])
            i += 1
            continue
        if stripped == "::: kb-dash":
            return i
        if _FENCE_OPEN.match(stripped) or _heading_name(stripped):
            break
        if pred(lines[i]) or (kind == "focus" and stripped.startswith(_CHECK)):
            out.append(lines[i])
            i += 1
            continue
        break
    while out and not out[-1].strip():
        out.pop()
    if out and out[-1].strip():
        out.append("")
    out.extend(_DASH_FENCE)
    out.append("")
    return i


def _dash_after_focus_review(text: str) -> str:
    """学习聚焦、知识回放条目后加框底虚线；栏目说明页不改。"""
    if _LEGEND in (text or ""):
        return text
    compact = _compact(text)
    if _FOCUS not in compact and _REVIEW not in compact:
        return text
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if stripped == "::: kb-section-icon":
            close = _fence_close(lines, i)
            inner = _compact("\n".join(lines[i + 1 : close]))
            kind = None
            if _FOCUS in inner:
                kind = "focus"
            elif _REVIEW in inner:
                kind = "review"
            out.extend(lines[i : close + 1])
            i = close + 1
            if kind:
                i = _copy_items_and_dash(lines, i, out, kind)
            continue
        name = _heading_name(stripped)
        if name == _FOCUS:
            out.append(lines[i])
            i = _copy_items_and_dash(lines, i + 1, out, "focus")
            continue
        if name == _REVIEW:
            out.append(lines[i])
            i = _copy_items_and_dash(lines, i + 1, out, "review")
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _is_stop_heading(name: str | None) -> bool:
    if not name:
        return False
    if name in _COLUMNS and name != _BANK:
        return True
    if re.match(r"^\d+\.\d+", name):
        return True
    if name.startswith("\u7b2c") and ("\u7ae0" in name or "\u8282" in name):
        return True
    return False


def _in_bank_col(title_box: list[float], box: list[float]) -> bool:
    tx0, _ty0, tx1, _ty1 = title_box
    x0, _y0, x1, _y1 = box
    mid = (x0 + x1) / 2
    if tx0 >= 600:
        return mid >= 620 or x0 >= 640
    if tx1 < 400:
        return mid <= 300 or x1 <= 340
    return False


def _soft(text: str) -> str:
    return re.sub(r"[<^>/$\\—–\-_{}]", "", _compact(text))


def _inbox_spec(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    title_box = None
    for block in items:
        name = _compact(_block_text(block))
        if name == _BANK and block.get("type") in {"title", "paragraph"}:
            title_box = block.get("bbox")
            break
    if not title_box:
        return None
    last_y1 = title_box[3]
    texts: list[str] = []
    has_table = False
    has_image = False
    has_eq = False
    started = False
    for block in sorted(
        items, key=lambda x: ((x.get("bbox") or [0, 0])[1], (x.get("bbox") or [0])[0])
    ):
        box = block.get("bbox") or [0, 0, 0, 0]
        if block.get("type") in {"page_header", "page_number"}:
            continue
        if box[3] < title_box[1] - 10:
            continue
        if not _in_bank_col(title_box, box):
            continue
        raw = _block_text(block).strip()
        name = _compact(raw)
        if name == _BANK:
            started = True
            last_y1 = max(last_y1, box[3])
            continue
        if not started and box[1] < title_box[1] - 4:
            continue
        started = True
        if name in _COLUMNS and name != _BANK:
            break
        gap = box[1] - last_y1
        if texts and gap > 80:
            break
        if name and re.match(r"^\d+\.", name) and len(name) > 10:
            break
        kind = block.get("type")
        if kind == "table":
            has_table = True
            last_y1 = max(last_y1, box[3])
            continue
        if kind == "equation_interline":
            has_eq = True
            last_y1 = max(last_y1, box[3])
            continue
        if kind == "image":
            width, height = box[2] - box[0], box[3] - box[1]
            if (texts or has_table) and (width > 200 or height > 150) and gap > 30:
                break
            if not texts and not has_table:
                has_image = True
            last_y1 = max(last_y1, box[3])
            continue
        if raw:
            texts.append(raw)
        last_y1 = max(last_y1, box[3])
    titles = {_compact(item) for item in texts if 1 <= len(_compact(item)) <= _INNER_MAX}
    prefs = []
    for item in texts:
        key = re.sub(r"<[^>]+>", "", _compact(item))
        if len(key) >= 8:
            prefs.append(key[:18])
    return {
        "titles": titles,
        "prefs": prefs,
        "has_table": has_table,
        "has_image": has_image and not texts and not has_table,
        "has_eq": has_eq,
    }


def _collect_inboxes(blocks: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        by_page[int(block.get("page_idx") or 0)].append(block)
    out: dict[int, dict[str, Any]] = {}
    for idx, items in by_page.items():
        spec = _inbox_spec(items)
        if spec:
            out[idx + 1] = spec
    return out


def _is_inner_title(name: str | None) -> bool:
    if not name or name in _COLUMNS or _is_stop_heading(name):
        return False
    return 1 <= len(name) <= _INNER_MAX


def _fence_close(lines: list[str], start: int) -> int:
    j = start + 1
    while j < len(lines) and not _FENCE_CLOSE.match(lines[j].strip()):
        j += 1
    return j if j < len(lines) else start


def _preceded_by_figure(lines: list[str], idx: int) -> bool:
    p = idx - 1
    while p >= 0 and not lines[p].strip():
        p -= 1
    if p < 0:
        return False
    if _FENCE_CLOSE.match(lines[p].strip()):
        q = p - 1
        while q >= 0 and not _FENCE_OPEN.match(lines[q].strip()):
            q -= 1
        if q < 0:
            return False
        opened = lines[q].strip()
        if opened in {"::: kb-section-icon", "::: kb-dash"}:
            return False
        body = "\n".join(lines[q + 1 : p])
        return bool(_IMG_HASH.search(body)) and not any(
            _heading_name(row) for row in lines[q + 1 : p]
        )
    return bool(_BARE_IMG.match(lines[p].strip()))


def _index_before_figure(lines: list[str], idx: int) -> int:
    p = idx
    while p > 0 and not lines[p - 1].strip():
        p -= 1
    q = p - 1
    if q >= 0 and _FENCE_CLOSE.match(lines[q].strip()):
        open_at = q - 1
        while open_at >= 0 and not _FENCE_OPEN.match(lines[open_at].strip()):
            open_at -= 1
        if open_at >= 0 and "kb-center" in lines[open_at]:
            body = "\n".join(lines[open_at + 1 : q])
            if _IMG_HASH.search(body) and not _heading_name(body):
                return open_at
    if q >= 0 and _BARE_IMG.match(lines[q].strip()):
        return q
    return p


def _line_key(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("**") and stripped.endswith("**"):
        return _compact(stripped.strip("*"))
    name = _heading_name(stripped)
    if name:
        return name
    return _compact(stripped)


def _is_formula(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("$") or stripped.startswith("$$")


def _matches_inbox(key: str, spec: dict[str, Any]) -> bool:
    if not key:
        return False
    key = _soft(key)
    titles = spec.get("titles") or set()
    if key in titles or _compact(key) in titles:
        return True
    for pref in spec.get("prefs") or []:
        pref_s = _soft(pref)
        if len(pref_s) >= 8 and (key.startswith(pref_s) or pref_s.startswith(key[:14])):
            return True
    return False


def _is_bank_section(name: str | None, spec: dict[str, Any] | None) -> bool:
    if not name:
        return False
    if spec and name in (spec.get("titles") or set()):
        return False
    if name in _COLUMNS or name == _BANK:
        return True
    if re.match(r"^\d+\.", name):
        return True
    return _is_stop_heading(name)


def _take_fence(lines: list[str], idx: int) -> tuple[list[str], int]:
    close = _fence_close(lines, idx)
    return lines[idx : close + 1], close + 1


def _find_bank_head(lines: list[str]) -> int | None:
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "::: kb-section-icon":
            close = _fence_close(lines, i)
            inner = _compact("\n".join(lines[i + 1 : close]))
            if _BANK in inner:
                return close
            i = close + 1
            continue
        if _heading_name(stripped) == _BANK or _compact(stripped) == _BANK:
            return i
        if _FENCE_OPEN.match(stripped):
            i = _fence_close(lines, i) + 1
        else:
            i += 1
    return None


def _demote_title(line: str) -> str:
    match = _ATX.match(line.strip())
    return f"**{match.group(2).strip()}**" if match else line


def _promote_bank_head(text: str, spec: dict[str, Any] | None) -> str:
    """把正文里的「资料库」收成栏名，栏内篇名（如钠）留在框里。"""
    lines = text.splitlines()
    bank_at = None
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "::: kb-section-icon":
            close = _fence_close(lines, i)
            if _BANK in _compact("\n".join(lines[i + 1 : close])):
                return text
            i = close + 1
            continue
        if _heading_name(stripped) == _BANK or _compact(stripped) == _BANK:
            bank_at = i
            break
        i += 1
    if bank_at is None:
        return text
    titles = (spec or {}).get("titles") or set()
    q = bank_at - 1
    title_at = None
    while q >= 0:
        name = _heading_name(lines[q].strip())
        if name and name in titles:
            title_at = q
            break
        if name and (_is_stop_heading(name) or name in _COLUMNS):
            break
        q -= 1
    if title_at is None:
        if _heading_name(lines[bank_at].strip()) != _BANK:
            lines[bank_at] = "## 资 料 库"
        return "\n".join(lines)
    del lines[bank_at]
    lines[title_at:title_at] = ["## 资 料 库", ""]
    return "\n".join(lines)


def _split_bank(text: str, spec: dict[str, Any] | None, *, mid: bool) -> str:
    text = _promote_bank_head(text, spec)
    lines = text.splitlines()
    head_end = _find_bank_head(lines)
    if head_end is None:
        return text
    rest = lines[head_end + 1 :]
    kept: list[str] = []
    leftover: list[str] = []
    k = 0
    collecting = True
    while k < len(rest):
        stripped = rest[k].strip()
        if not stripped:
            if not collecting:
                leftover.append(rest[k])
            k += 1
            continue
        if stripped == "::: kb-dash":
            chunk, k = _take_fence(rest, k)
            if collecting:
                continue
            leftover.extend(chunk)
            continue
        if _FENCE_OPEN.match(stripped):
            chunk, k = _take_fence(rest, k)
            if spec and collecting and "kb-table-wrap" in stripped and spec.get("has_table"):
                kept.extend(chunk)
                continue
            if spec and collecting and spec.get("has_image") and "kb-center" in stripped:
                body = "\n".join(chunk)
                if "![" in body and not re.search(r"!\[\s*\u56fe\s*\d", body):
                    kept.extend(chunk)
                    continue
            if spec and collecting and spec.get("has_eq") and "kb-center" in stripped:
                body = "\n".join(chunk)
                if "$$" in body or "$" in body:
                    kept.extend(chunk)
                    continue
            collecting = False
            leftover.extend(chunk)
            continue
        if stripped.startswith("<table"):
            if collecting and spec and spec.get("has_table"):
                kept.append(rest[k])
            else:
                collecting = False
                leftover.append(rest[k])
            k += 1
            continue
        if (
            spec
            and collecting
            and spec.get("has_image")
            and _BARE_IMG.match(stripped)
            and not re.search(r"!\[\s*\u56fe\s*\d", stripped)
        ):
            kept.append(rest[k])
            k += 1
            continue
        key = _line_key(rest[k])
        name = _heading_name(rest[k])
        if spec and collecting and spec.get("has_eq") and _is_formula(stripped):
            kept.append(rest[k])
            k += 1
            continue
        if (
            collecting
            and spec
            and _is_formula(stripped)
            and kept
            and kept[-1].strip().endswith("：")
        ):
            kept.append(rest[k])
            k += 1
            continue
        if spec:
            if collecting and _matches_inbox(key, spec) and not _is_bank_section(name, spec):
                if name and name in (spec.get("titles") or set()):
                    kept.append(_demote_title(rest[k]))
                else:
                    kept.append(rest[k])
                k += 1
                continue
            collecting = False
            leftover.append(rest[k])
            k += 1
            continue
        if _is_bank_section(name, None):
            leftover.extend(rest[k:])
            break
        if name and _is_inner_title(name):
            kept.append(_demote_title(rest[k]))
        else:
            kept.append(rest[k])
        k += 1

    if spec:
        stolen: list[str] = []
        remain: list[str] = []
        for line in leftover:
            stripped = line.strip()
            key = _line_key(line)
            name = _heading_name(stripped)
            if (
                stripped
                and not _FENCE_OPEN.match(stripped)
                and not stripped.startswith("<")
                and _matches_inbox(key, spec)
                and not _is_bank_section(name, spec)
            ):
                if name and name in (spec.get("titles") or set()):
                    stolen.append(_demote_title(line))
                else:
                    stolen.append(line)
                continue
            remain.append(line)
        leftover = remain
        if stolen:
            kept.extend(stolen)
        if spec.get("has_table"):
            still: list[str] = []
            idx = 0
            while idx < len(leftover):
                item = leftover[idx].strip()
                if item.startswith("::: kb-table-wrap") or item.startswith("<table"):
                    if item.startswith(":::"):
                        chunk, nxt = _take_fence(leftover, idx)
                        kept.extend(chunk)
                        idx = nxt
                        continue
                    kept.append(leftover[idx])
                    idx += 1
                    continue
                still.append(leftover[idx])
                idx += 1
            leftover = still
        if spec.get("has_eq"):
            still = []
            idx = 0
            while idx < len(leftover):
                item = leftover[idx].strip()
                if item.startswith("::: kb-center"):
                    chunk, nxt = _take_fence(leftover, idx)
                    body = "\n".join(chunk)
                    if "$$" in body or re.search(r"^\s*\$", body, re.M):
                        kept.extend(chunk)
                        idx = nxt
                        continue
                still.append(leftover[idx])
                idx += 1
            leftover = still

    out = lines[: head_end + 1]
    if kept:
        if out and out[-1].strip():
            out.append("")
        out.extend(kept)
        if out[-1].strip():
            out.append("")
    if mid:
        out.extend(["::: kb-dash", "", ":::"])
        if leftover:
            out.append("")
    while leftover and not leftover[0].strip():
        leftover.pop(0)
    out.extend(leftover)
    return "\n".join(out)


def _next_nonempty(lines: list[str], start: int) -> str:
    j = start
    while j < len(lines) and not lines[j].strip():
        j += 1
    return lines[j].strip() if j < len(lines) else ""


def _looks_body_paragraph(nxt: str) -> bool:
    if not nxt or nxt.startswith(("#", "!", ":", "<", "*")):
        return False
    if _heading_name(nxt):
        return False
    compact = _compact(nxt)
    if re.match(r"^\d+(\.\d+)*$", compact):
        return False
    return len(compact) >= 12


def _keep_leading_image(body: str, nxt: str) -> bool:
    if _heading_name(nxt) == _BANK:
        return True
    if nxt.startswith("*") and "\u56fe" in nxt:
        return True
    if re.search(r"!\[\s*\u56fe", body):
        return True
    if not _looks_body_paragraph(nxt):
        return True
    return False


def _peel_leading_low_icons(text: str, low_hashes: set[str]) -> str:
    if not low_hashes:
        return text
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return text
    if lines[i].strip() == "::: kb-center":
        close = _fence_close(lines, i)
        body = "\n".join(lines[i + 1 : close])
        hashes = set(_IMG_HASH.findall(body))
        nxt = _next_nonempty(lines, close + 1)
        if (
            hashes
            and hashes <= low_hashes
            and not re.search(r"^#{1,6} ", body, re.M)
            and not _keep_leading_image(body, nxt)
        ):
            return "\n".join(lines[close + 1 :]).lstrip("\n")
    if _BARE_IMG.match(lines[i].strip()):
        digest = _line_hash(lines[i])
        nxt = _next_nonempty(lines, i + 1)
        if (
            digest
            and digest in low_hashes
            and not _keep_leading_image(lines[i], nxt)
        ):
            return "\n".join(lines[i + 1 :]).lstrip("\n")
    return text


def _preceding_image_hashes(lines: list[str], idx: int) -> set[str]:
    p = idx - 1
    while p >= 0 and not lines[p].strip():
        p -= 1
    if p < 0:
        return set()
    if _FENCE_CLOSE.match(lines[p].strip()):
        q = p - 1
        while q >= 0 and not _FENCE_OPEN.match(lines[q].strip()):
            q -= 1
        if q < 0:
            return set()
        return set(_IMG_HASH.findall("\n".join(lines[q + 1 : p])))
    digest = _line_hash(lines[p])
    return {digest} if digest else set()


def _insert_experiment(text: str, low_hashes: set[str] | None = None) -> str:
    if _EXPERIMENT in _compact(text) or _BANK not in _compact(text):
        return text
    low_hashes = low_hashes or set()
    if not low_hashes:
        return text
    lines = text.splitlines()
    seen_bank = False
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "::: kb-section-icon":
            close = _fence_close(lines, i)
            inner = "\n".join(lines[i + 1 : close])
            if _BANK in _compact(inner):
                seen_bank = True
            i = close + 1
            continue
        if _FENCE_OPEN.match(stripped):
            i = _fence_close(lines, i) + 1
            continue
        name = _heading_name(stripped)
        if name == _BANK:
            seen_bank = True
            i += 1
            continue
        if seen_bank and name == _EXPERIMENT:
            return text
        if (
            seen_bank
            and name
            and name not in _COLUMNS
            and not _is_stop_heading(name)
            and _preceded_by_figure(lines, i)
        ):
            hashes = _preceding_image_hashes(lines, i)
            if hashes and hashes <= low_hashes:
                insert_at = _index_before_figure(lines, i)
                lines[insert_at:insert_at] = [_EXPERIMENT_HEAD, ""]
                return "\n".join(lines)
        i += 1
    return text


def apply_databank(
    source: str,
    mid: str,
    masks: dict[str, set[str]] | None = None,
    inbox: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """\u8d44\u6599\u5e93\u53ea\u6536\u865a\u7ebf\u6846\u91cc\u7684\u5b57\uff0c\u4e2d\u95f4\u6587\u4ef6\u5728\u6846\u5e95\u63d2\u5165 kb-dash\u3002"""
    if _LEGEND in source:
        return source, mid
    if _BANK not in _compact(source) and _BANK not in _compact(mid or ""):
        return source, mid
    masks = masks or {}
    low = masks.get("low_icons") or set()
    new_src = _peel_leading_low_icons(source, low)
    new_src = _split_bank(new_src, inbox, mid=False)
    new_src = _insert_experiment(new_src, low)
    new_mid = _peel_leading_low_icons(mid or source, low)
    new_mid = _unwrap_centered_headings(new_mid)
    new_mid = _split_bank(new_mid, inbox, mid=True)
    new_mid = _insert_experiment(new_mid, low)
    return _tidy(new_src), _tidy(new_mid)


def _unwrap_centered_headings(text: str) -> str:
    """Keep centered X.Y section titles; unwrap other centered headings."""

    def keep(match: re.Match[str]) -> str:
        inner = match.group(1)
        if _SECTION_ATX.match(inner.strip()):
            return match.group(0)
        return inner + "\n\n"

    return _CENTER_HEADING.sub(keep, text)


def _load_section_heads(book: Path) -> dict[int, tuple[str, str]]:
    path = book / "toc.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return {}
    out: dict[int, tuple[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("from_body"):
            continue
        label = str(entry.get("label") or "").strip()
        match = _TOC_SECTION.match(label)
        if not match:
            continue
        page = int(entry.get("pdf_page") or 0)
        if page <= 0:
            continue
        out[page] = (f"{match.group(1)}.{match.group(2)}", match.group(3).strip())
    return out


def _plain_heading_text(line: str) -> str:
    raw = line.strip()
    match = _ATX.match(raw)
    body = match.group(2) if match else raw
    return re.sub(r"^!\[[^\]]*\]\([^)]+\)\s*", "", body)


def _is_hat_heading(line: str) -> bool:
    raw = re.sub(r"^#{1,6}\s+", "", line.strip())
    return raw.startswith("![") and bool(re.search(r"\)\s+\S", raw))


def _is_caption_line(line: str) -> bool:
    text = line.strip()
    return bool(text) and text.startswith("*") and text.endswith("*")


def _head_kind(line: str, num: str, name: str) -> str:
    if _is_hat_heading(line):
        return "other"
    body = _compact(_plain_heading_text(line))
    if not body:
        return "blank" if not line.strip() else "other"
    if body == _compact(num):
        return "num"
    if body == _compact(name):
        return "name"
    if body in {_compact(num + name), _compact(f"{num} {name}")}:
        return "full"
    return "other"


def _inner_is_heads(lines: list[str], num: str, name: str) -> bool:
    kinds = [_head_kind(item, num, name) for item in lines if item.strip()]
    return bool(kinds) and all(kind in {"num", "name", "full"} for kind in kinds)


def _inner_is_media(lines: list[str]) -> bool:
    seen = False
    for item in lines:
        text = item.strip()
        if not text:
            continue
        if _BARE_IMG.match(text) or _is_caption_line(item):
            seen = True
            continue
        return False
    return seen


def _normalize_section_head(text: str, num: str, name: str, *, mid: bool) -> str:
    full = f"{num} {name}"
    lines = (text or "").splitlines()
    leftover: list[str] = []
    media: list[str] = []
    consumed = False
    idx = 0
    total = len(lines)
    while idx < total:
        raw = lines[idx]
        stripped = raw.strip()
        if not stripped:
            idx += 1
            continue
        if _FENCE_OPEN.match(stripped):
            chunk, nxt = _take_fence(lines, idx)
            kind = stripped.split()[1] if len(stripped.split()) > 1 else ""
            inner = chunk[1:-1] if len(chunk) >= 2 else chunk
            if kind == "kb-page-toc":
                for item in inner:
                    if not item.strip():
                        continue
                    if _head_kind(item, num, name) in {"num", "name", "full"}:
                        consumed = True
                        continue
                    leftover.append(item)
                idx = nxt
                continue
            if kind == "kb-center" and _inner_is_heads(inner, num, name):
                consumed = True
                idx = nxt
                continue
            if (
                not consumed
                and kind == "kb-center"
                and _inner_is_media(inner)
            ):
                if media and media[-1] != "":
                    media.append("")
                media.extend(chunk)
                idx = nxt
                continue
            break
        kind = _head_kind(raw, num, name)
        if kind in {"num", "name", "full"}:
            consumed = True
            idx += 1
            continue
        if not consumed and (
            _BARE_IMG.match(stripped) or _is_caption_line(raw)
        ):
            media.append(raw)
            idx += 1
            continue
        break
    rest = lines[idx:]
    while rest and not rest[0].strip():
        rest.pop(0)
    title = f"## {full}"
    head = ["::: kb-center", title, ":::"] if mid else [title]
    body = media + ([""] if media else []) + leftover + ([""] if leftover else []) + rest
    while body and not body[0].strip():
        body.pop(0)
    return _tidy("\n".join(head + [""] + body))


def apply_section_heads(
    source: str, mid: str, head: tuple[str, str] | None = None
) -> tuple[str, str]:
    """Section openers are ## X.Y title, centered in mid; not page-toc."""
    if _LEGEND in (source or "") or not head:
        return source, mid
    num, name = head
    if not num or not name:
        return source, mid
    new_src = _normalize_section_head(source, num, name, mid=False)
    new_mid = _normalize_section_head(mid or source, num, name, mid=True)
    return _tidy(new_src), _tidy(new_mid)


def _is_hat_box(width: float, height: float) -> bool:
    return _HAT_W[0] <= width <= _HAT_W[1] and _HAT_H[0] <= height <= _HAT_H[1]


def _hat_partner(items: list[dict[str, Any]], box: list[float]) -> str | None:
    mid_y = (box[1] + box[3]) / 2
    found: list[tuple[float, str]] = []
    for block in items:
        if block.get("type") not in {"title", "paragraph"}:
            continue
        name = _compact(_block_text(block))
        if not name or name in _COLUMNS or name == _GUIDE:
            continue
        if any(col in name for col in _COLUMNS):
            continue
        other = block.get("bbox") or [0, 0, 0, 0]
        gap = abs((other[1] + other[3]) / 2 - mid_y)
        if gap < 28 and other[0] >= box[0] - 5 and other[0] <= box[2] + 90:
            found.append((gap, name))
    found.sort()
    return found[0][1] if found else None


def _collect_hats(blocks: list[dict[str, Any]]) -> dict[int, list[tuple[str, str]]]:
    by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        by_page[int(block.get("page_idx") or 0)].append(block)
    out: dict[int, list[tuple[str, str]]] = {}
    for idx, items in by_page.items():
        if any(_LEGEND in _compact(_block_text(block)) for block in items):
            continue
        pairs: list[tuple[str, str]] = []
        for block in items:
            if block.get("type") != "image":
                continue
            box = block.get("bbox") or [0, 0, 0, 0]
            width, height = box[2] - box[0], box[3] - box[1]
            digest = _block_hash(block)
            if not digest or not _is_hat_box(width, height):
                continue
            name = _hat_partner(items, box)
            if name:
                pairs.append((digest, name))
        if pairs:
            out[idx + 1] = pairs
    return out


def _image_markdown(text: str, digest: str) -> str | None:
    match = re.search(rf"!\[[^\]]*\]\([^)]*{re.escape(digest)}[^)]*\)", text)
    return match.group(0) if match else None


def _heading_title(text: str, title: str) -> str:
    want = _compact(title)
    for line in text.splitlines():
        name = _heading_name(line)
        if name and _compact(re.sub(r"^!\[[^\]]*\]\([^)]+\)\s*", "", name)) == want:
            raw = _ATX.match(line.strip())
            if raw:
                return re.sub(r"^!\[[^\]]*\]\([^)]+\)\s*", "", raw.group(2)).strip()
    return title


def _hat_line(image: str, title: str) -> str:
    return f"## {image} {title}"


def _attach_hat(text: str, digest: str, title: str) -> str:
    label = _heading_title(text, title)
    image = _image_markdown(text, digest)
    if not image:
        return text
    if re.search(
        rf"^##[ \t]+!\[[^\]]*\]\([^)]*{re.escape(digest)}[^)]*\)[ \t]+{re.escape(label)}[ \t]*$",
        text,
        re.M,
    ):
        return text
    img = rf"!\[[^\]]*\]\([^)]*{re.escape(digest)}[^)]*\)"
    head = rf"##[ \t]+(?:{img}[ \t]+)?{re.escape(label)}[ \t]*"
    icon = re.compile(
        rf"::: kb-section-icon\r?\n({img})\r?\n+{head}\r?\n:::\r?\n*"
    )
    if icon.search(text):
        return icon.sub(_hat_line(image, label) + "\n\n", text, count=1)
    near = re.compile(
        rf"(?:::: kb-center\r?\n)?({img})\r?\n(?::::\r?\n+)?(?:::: kb-dash\r?\n+:::\r?\n+)?"
        rf"(?:::: kb-center\r?\n)?({head})\r?\n(?::::\r?\n+)?"
    )
    match = near.search(text)
    if match:
        kept_dash = "::: kb-dash\n\n:::\n\n" if "kb-dash" in match.group(0) else ""
        return near.sub(kept_dash + _hat_line(image, label) + "\n\n", text, count=1)
    stripped = re.sub(
        rf"::: kb-center\r?\n{img}\r?\n:::\r?\n*",
        "",
        text,
        count=1,
    )
    if stripped == text:
        stripped = re.sub(rf"{img}\r?\n+", "", text, count=1)
    if stripped == text:
        return text
    center_h = re.compile(rf"::: kb-center\r?\n{head}\r?\n:::\r?\n*")
    bare_h = re.compile(rf"^{head}\r?\n", re.M)
    found = center_h.search(stripped) or bare_h.search(stripped)
    if not found:
        return text
    return stripped[: found.start()] + _hat_line(image, label) + "\n\n" + stripped[found.end() :]


def apply_topic_hats(
    source: str, mid: str, hats: list[tuple[str, str]] | None = None
) -> tuple[str, str]:
    """\u6b63\u6587\u5c0f\u8282\u5e3d\u6807\u8d34\u5728\u666e\u901a\u6807\u9898\u524d\uff0c\u4e0d\u8981\u6536\u6210\u680f\u76ee\u56f4\u680f\u3002"""
    if _LEGEND in (source or "") or not hats:
        return source, mid
    new_src = source or ""
    new_mid = mid or source
    for digest, title in hats:
        new_mid = _attach_hat(new_mid, digest, title)
        new_src = _attach_hat(new_src, digest, title)
    return new_src, _tidy(new_mid)


_IMG_LINE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
_EM_LINE = re.compile(r"^\*(.+)\*\s*$")
_FIG_HEAD = re.compile(r"^图\s*\d+\s*[.\-]\s*\d+")
_GLUED_CAP = re.compile(
    r"^(?P<plate>.+?)(?P<fig>图\s*\d+\s*[.\-]\s*\d+[\s\S]*)$"
)
_SKIP_NAMED = frozenset(
    {
        "kb-section-icon",
        "kb-page-toc",
        "kb-list-item",
        "kb-dash",
        "kb-table-wrap",
        "kb-title",
        "kb-subtitle",
        "kb-author",
        "kb-section",
        "kb-section-center",
        "kb-verse",
        "kb-verse-center",
        "kb-date",
        "kb-lead",
        "kb-solution",
        "kb-solution-tail",
        "kb-drama",
        "kb-stage",
    }
)


def _load_content_list(book: Path) -> list[dict[str, Any]]:
    mineru = book / "mineru"
    found = [p for p in sorted(mineru.glob("*_content_list.json")) if "_v2" not in p.name]
    if not found:
        found = sorted(mineru.glob("*_content_list.json"))
    if not found:
        return []
    try:
        data = json.loads(found[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _split_mineru_caps(caps: list[str]) -> tuple[list[str], list[str]]:
    plates: list[str] = []
    figs: list[str] = []
    for raw in caps:
        text = re.sub(r"<[^>]+>", "", str(raw or "")).strip()
        if not text:
            continue
        if _FIG_HEAD.match(text):
            figs.append(text)
            continue
        if "。" in text or len(_compact(text)) > 24:
            continue
        plates.append(text)
    return plates, figs


def _collect_image_captions(
    blocks: list[dict[str, Any]],
) -> dict[str, tuple[list[str], list[str]]]:
    out: dict[str, tuple[list[str], list[str]]] = {}
    for block in blocks:
        if str(block.get("type") or "") != "image":
            continue
        path = str(block.get("img_path") or "")
        digest = path.rsplit("/", 1)[-1].replace(".jpg", "")
        if len(digest) != 64:
            continue
        raw = block.get("image_caption") or []
        if not isinstance(raw, list):
            raw = [raw]
        plates, figs = _split_mineru_caps([str(c) for c in raw if c])
        if plates or figs:
            out[digest] = (plates, figs)
    return out


def _split_glued(*texts: str) -> tuple[list[str], list[str]]:
    for text in texts:
        match = _GLUED_CAP.match((text or "").strip())
        if not match:
            continue
        plate = match.group("plate").strip()
        fig = match.group("fig").strip()
        if not plate or "图" in plate or "。" in plate:
            continue
        if not (0 < len(_compact(plate)) <= 24):
            continue
        if not _FIG_HEAD.match(fig):
            continue
        return [plate], [fig]
    return [], []


def _resolve_caps(
    digest: str,
    alt: str,
    caption: str,
    capmap: dict[str, tuple[list[str], list[str]]],
) -> tuple[list[str], list[str]]:
    glued_p, glued_f = _split_glued(alt, caption)
    mapped = capmap.get(digest) if digest else None
    if mapped:
        plates, figs = mapped
        if plates and figs:
            return plates, figs
        if figs and glued_p:
            return glued_p, figs
        if plates and glued_f:
            return plates, glued_f
    return glued_p, glued_f


def _take_skip_fence(lines: list[str], idx: int) -> int | None:
    stripped = lines[idx].strip()
    if not _FENCE_OPEN.match(stripped):
        return None
    name = stripped[4:].strip()
    if name not in _SKIP_NAMED:
        return None
    j = idx + 1
    while j < len(lines) and not _FENCE_CLOSE.match(lines[j].strip()):
        j += 1
    return j + 1 if j < len(lines) else j


def _take_figure(lines: list[str], idx: int) -> tuple[int, dict[str, Any] | None]:
    n = len(lines)
    if idx >= n:
        return idx, None
    start = idx
    fenced = False
    if lines[idx].strip() == "::: kb-center":
        fenced = True
        idx += 1
        while idx < n and not lines[idx].strip():
            idx += 1
    if idx >= n:
        return start, None
    img = _IMG_LINE.match(lines[idx].strip())
    if not img:
        return start, None
    img_line = lines[idx]
    alt, url = img.group(1), img.group(2)
    idx += 1
    plates: list[str] = []
    inline_figs: list[str] = []
    while idx < n:
        look = idx
        while look < n and not lines[look].strip():
            look += 1
        if look >= n:
            break
        stripped = lines[look].strip()
        if fenced and _FENCE_CLOSE.match(stripped):
            break
        em = _EM_LINE.match(stripped)
        if not em:
            break
        text = em.group(1)
        idx = look + 1
        if _FIG_HEAD.match(text):
            inline_figs.append(text)
        else:
            plates.append(text)
    if fenced:
        while idx < n and not lines[idx].strip():
            idx += 1
        if idx < n and _FENCE_CLOSE.match(lines[idx].strip()):
            idx += 1
        elif not plates and not inline_figs:
            return start, None
    return idx, {
        "start": start,
        "end": idx,
        "alt": alt,
        "url": url,
        "caption": plates[0] if plates else None,
        "fenced": fenced,
        "digest": _line_hash(img_line) or "",
        "inline_figs": inline_figs,
    }


def _render_unit(unit: dict[str, Any], plates: list[str], fenced: bool) -> list[str]:
    alt = plates[0] if plates else unit["alt"]
    lines = [f"![{alt}]({unit['url']})", ""]
    labels = plates or ([unit["caption"]] if unit["caption"] else [])
    for label in labels:
        lines.extend([f"*{label}*", ""])
    if fenced or unit["fenced"]:
        return ["::: kb-center", *lines, ":::", ""]
    return lines


def _render_fig(fig: str, fenced: bool) -> list[str]:
    body = [f"*{fig}*", ""]
    if fenced:
        return ["::: kb-center", *body, ":::", ""]
    return body


def _skip_existing_fig(lines: list[str], idx: int, figs: list[str]) -> tuple[int, list[str]]:
    left = list(figs)
    while left and idx < len(lines):
        j = idx
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            break
        fenced = False
        if lines[j].strip() == "::: kb-center":
            fenced = True
            j += 1
            while j < len(lines) and not lines[j].strip():
                j += 1
        if j >= len(lines):
            break
        em = _EM_LINE.match(lines[j].strip())
        if not em or em.group(1) not in left:
            break
        left.remove(em.group(1))
        j += 1
        if fenced:
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines) or not _FENCE_CLOSE.match(lines[j].strip()):
                break
            j += 1
        idx = j
    return idx, left


def _unique_keep(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _needs_peel(alt: str, caption: str, figs: list[str]) -> bool:
    if not figs:
        return False
    for fig in figs:
        in_alt = fig in (alt or "")
        in_cap = fig in (caption or "")
        if in_alt and (alt or "").strip() != fig:
            return True
        if in_cap and (caption or "").strip() != fig:
            return True
    return False


def _rewrite_figures(
    text: str,
    capmap: dict[str, tuple[list[str], list[str]]],
    fenced: bool,
) -> str:
    if _LEGEND in (text or "") or not capmap and "图" not in (text or ""):
        return text
    lines = (text or "").splitlines()
    out: list[str] = []
    mutated = False
    i = 0
    n = len(lines)
    while i < n:
        skip = _take_skip_fence(lines, i)
        if skip is not None:
            out.extend(lines[i:skip])
            i = skip
            continue
        nxt, unit = _take_figure(lines, i)
        if unit is None:
            out.append(lines[i])
            i += 1
            continue
        run = [unit]
        i = nxt
        while True:
            look = i
            while look < n and not lines[look].strip():
                look += 1
            later, extra = _take_figure(lines, look)
            if extra is None:
                break
            run.append(extra)
            i = later
        resolved: list[tuple[dict[str, Any], list[str], list[str]]] = []
        pending: list[str] = []
        peel = False
        for item in run:
            plates, figs = _resolve_caps(
                item["digest"], item["alt"], item["caption"] or "", capmap
            )
            resolved.append((item, plates, figs))
            pending.extend(figs)
            if _needs_peel(item["alt"], item["caption"] or "", figs):
                peel = True
        pending = _unique_keep(pending)
        after, leftover = _skip_existing_fig(lines, i, pending)
        seen = {
            fig
            for item, _plates, _figs in resolved
            for fig in (item.get("inline_figs") or [])
        }
        leftover = [fig for fig in leftover if fig not in seen]
        if not peel and not leftover:
            out.extend(lines[run[0]["start"] : run[-1]["end"]])
            i = run[-1]["end"]
            continue
        for item, plates, figs in resolved:
            if _needs_peel(item["alt"], item["caption"] or "", figs):
                out.extend(_render_unit(item, plates, fenced or item["fenced"]))
            else:
                out.extend(lines[item["start"] : item["end"]])
        i = after
        for fig in leftover:
            out.extend(_render_fig(fig, fenced))
        mutated = True
    if not mutated:
        return text
    return _tidy("\n".join(out))


def apply_group_captions(
    source: str,
    mid: str,
    captions: dict[str, tuple[list[str], list[str]]] | None = None,
) -> tuple[str, str]:
    """\u5206\u56fe\u77ed\u6807\u7559\u5728\u5404\u56fe\u4e0b\uff0c\u300c\u56feN.N \u9898\u300d\u653e\u5728\u6574\u884c\u4e4b\u540e\uff0c\u4e0d\u8981\u7c98\u6210\u4e00\u53e5\u3002"""
    if _LEGEND in (source or ""):
        return source, mid
    capmap = captions or {}
    new_src = _rewrite_figures(source, capmap, False)
    new_mid = _rewrite_figures(mid or source, capmap, True)
    return new_src, new_mid


def _in_span(value: float, span: tuple[float, float]) -> bool:
    return span[0] <= value <= span[1]


def _is_think_box(width: float, height: float) -> bool:
    return _in_span(width, _THINK_W) and _in_span(height, _THINK_H)


def _is_write_box(width: float, height: float) -> bool:
    return _in_span(width, _WRITE_W) and _in_span(height, _WRITE_H)


def _is_history_box(width: float, height: float) -> bool:
    return _in_span(width, _HISTORY_W) and _in_span(height, _HISTORY_H)


def _near_figure_caption(items: list[dict[str, Any]], box: list[float]) -> bool:
    y1 = box[3]
    for block in items:
        raw = _block_text(block).strip()
        if not raw:
            continue
        other = block.get("bbox") or [0, 0, 0, 0]
        if other[1] < y1 - 8 or other[1] > y1 + 90:
            continue
        if _FIG_HEAD.match(raw) or raw.startswith("\u56fe"):
            return True
    return False


def _next_prompt_text(items: list[dict[str, Any]], box: list[float]) -> str:
    y1 = box[3]
    mid_x = (box[0] + box[2]) / 2
    found: list[tuple[float, str]] = []
    for block in items:
        if block.get("type") not in {"title", "paragraph"}:
            continue
        raw = _block_text(block).strip()
        key = _compact(raw)
        if len(key) < 8 or key in _COLUMNS or key == _GUIDE:
            continue
        other = block.get("bbox") or [0, 0, 0, 0]
        if other[1] < y1 - 6:
            continue
        other_mid = (other[0] + other[2]) / 2
        if abs(other_mid - mid_x) > 280 and other[0] > box[2] + 80:
            continue
        found.append((other[1], raw))
    found.sort()
    return found[0][1] if found else ""


def _collect_prompt_icons(
    blocks: list[dict[str, Any]],
) -> dict[int, list[tuple[str, str, str]]]:
    """\u6309\u680f\u6761\u5c0f\u56fe\u5c3a\u5bf8\u6536\u60f3\u4e00\u60f3 / \u4e66\u5199\u8868\u8fbe\uff0c\u6392\u9664\u56fe\u9898\u3001\u5e3d\u6807\u548c\u5316\u5b66\u53f2\u8bdd\u3002"""
    by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        by_page[int(block.get("page_idx") or 0)].append(block)
    out: dict[int, list[tuple[str, str, str]]] = {}
    for idx, items in by_page.items():
        if any(_LEGEND in _compact(_block_text(block)) for block in items):
            continue
        pairs: list[tuple[str, str, str]] = []
        for block in items:
            if block.get("type") != "image":
                continue
            box = block.get("bbox") or [0, 0, 0, 0]
            width, height = box[2] - box[0], box[3] - box[1]
            digest = _block_hash(block)
            if not digest or _is_hat_box(width, height) or _is_history_box(width, height):
                continue
            if _near_figure_caption(items, box):
                continue
            if _is_think_box(width, height):
                col = _THINK
            elif _is_write_box(width, height):
                col = _WRITE
            else:
                continue
            pairs.append((digest, col, _next_prompt_text(items, box)))
        if pairs:
            out[idx + 1] = pairs
    return out


def _text_has_col_head(text: str, col: str) -> bool:
    for line in (text or "").splitlines():
        name = _heading_name(line)
        if name == col:
            return True
        if name and _compact(re.sub(r"^!\[[^\]]*\]\([^)]+\)\s*", "", name)) == col:
            return True
    return False


def _proxy_img(text: str, digest: str, alt: str = "\u63d2\u56fe") -> str | None:
    found = _image_markdown(text, digest)
    if found:
        return found
    sample = re.search(
        r"!\[[^\]]*\]\(([^)]+mineru%2F)[a-f0-9]{64}(\.jpg[^)]*)\)",
        text or "",
    )
    if sample:
        return f"![{alt}]({sample.group(1)}{digest}{sample.group(2)})"
    return None


def _digest_used_as_figure(text: str, digest: str) -> bool:
    found = _image_markdown(text, digest)
    if not found:
        return False
    alt = re.match(r"!\[([^\]]*)\]", found)
    label = alt.group(1) if alt else ""
    return bool(_FIG_HEAD.match(label) or (label or "").startswith("\u56fe"))


def _find_hash_line(lines: list[str], digest: str) -> int | None:
    for i, line in enumerate(lines):
        if digest in line and _IMG_HASH.search(line):
            return i
    return None


def _after_image_block(lines: list[str], idx: int) -> int:
    if idx > 0 and lines[idx - 1].strip() == "::: kb-center":
        close = _fence_close(lines, idx - 1)
        return close + 1
    j = idx + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    if j < len(lines) and _FENCE_CLOSE.match(lines[j].strip()):
        return j + 1
    return idx + 1


def _find_follow_line(lines: list[str], follow: str) -> int | None:
    want = _soft(follow)[:16]
    if len(want) < 8:
        return None
    for i, line in enumerate(lines):
        key = _soft(line)
        if key and (key.startswith(want) or want.startswith(key[:14])):
            return i
    return None


def _digest_in_section_icon(text: str, digest: str) -> bool:
    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip() == "::: kb-section-icon":
            close = _fence_close(lines, i)
            if digest in "\n".join(lines[i : close + 1]):
                return True
            i = close + 1
            continue
        i += 1
    return False


def _heading_after_digest(lines: list[str], digest: str, col: str) -> bool:
    idx = _find_hash_line(lines, digest)
    if idx is None:
        return False
    p = idx
    while p >= 0 and not _FENCE_OPEN.match(lines[p].strip()):
        p -= 1
    if p >= 0 and lines[p].strip() == "::: kb-section-icon":
        close = _fence_close(lines, p)
        return col in _compact("\n".join(lines[p + 1 : close]))
    insert_at = _after_image_block(lines, idx)
    return _heading_name(_next_nonempty(lines, insert_at)) == col


def _restore_prompt_heads(
    text: str, icons: list[tuple[str, str, str]] | None
) -> str:
    """缺栏名时按小图逐栏补回栏条和图标；已在 kb-section-icon 里的不改判。"""
    if _LEGEND in (text or "") or not icons:
        return text
    lines = (text or "").splitlines()
    mutated = False
    for digest, col, follow in icons:
        joined = "\n".join(lines)
        if _digest_in_section_icon(joined, digest):
            continue
        if _heading_after_digest(lines, digest, col):
            continue
        if _digest_used_as_figure(joined, digest):
            continue
        img = _proxy_img(joined, digest)
        if not img:
            continue
        idx = _find_hash_line(lines, digest)
        heading = f"## {col}"
        if idx is not None:
            insert_at = _after_image_block(lines, idx)
            nxt = _next_nonempty(lines, insert_at)
            if _heading_name(nxt) == col:
                continue
            lines[insert_at:insert_at] = [heading, ""]
            mutated = True
            continue
        at = _find_follow_line(lines, follow)
        if at is None:
            continue
        lines[at:at] = [img, "", heading, ""]
        mutated = True
    if not mutated:
        return text
    return _tidy("\n".join(lines))


def apply_prompt_columns(
    source: str,
    mid: str,
    icons: list[tuple[str, str, str]] | None = None,
) -> tuple[str, str]:
    """\u60f3\u4e00\u60f3\u3001\u4e66\u5199\u8868\u8fbe\uff1a\u7f3a\u680f\u540d\u6309\u680f\u6761\u5c0f\u56fe\u8865\u56de\uff0c\u518d\u6536\u6210\u680f\u6846\u3002"""
    if _LEGEND in (source or "") or _LEGEND in (mid or ""):
        return source, mid
    new_src = _restore_prompt_heads(source, icons)
    new_mid = _restore_prompt_heads(mid or source, icons)
    return new_src, new_mid


_BOX_COLS = frozenset({_EXPERIMENT, _GUIDE, _THINK, _WRITE})
_GUIDE_INNER = frozenset(
    {
        "\u7ec3\u4e60\u5de9\u56fa",
        "\u4f8b\u9898\u5bfc\u5f15",
        "\u7d20\u517b\u63d0\u5347",
        "\u6838\u5fc3\u6846\u56fe",
        "\u4f53\u9a8c\u5206\u4eab",
        "\u5206\u6790",
        "\u95ee\u9898",
    }
)


def _center_is_bare_icon(chunk: list[str]) -> bool:
    imgs = [ln for ln in chunk if _IMG_LINE.match(ln.strip())]
    if len(imgs) != 1:
        return False
    alt = _IMG_LINE.match(imgs[0].strip()).group(1)
    if _FIG_HEAD.match(alt or "") or (alt or "").startswith("\u56fe"):
        return False
    if any(_heading_name(ln) for ln in chunk):
        return False
    for ln in chunk:
        em = _EM_LINE.match(ln.strip())
        if em and "\u56fe" in em.group(1):
            return False
    return True


def _img_from_chunk(chunk: list[str]) -> str:
    for ln in chunk:
        if _IMG_LINE.match(ln.strip()):
            return ln.strip()
    return ""


def _bare_icon_line(line: str) -> bool:
    img = _IMG_LINE.match(line.strip())
    if not img:
        return False
    alt = img.group(1) or ""
    if _FIG_HEAD.match(alt) or alt.startswith("\u56fe"):
        return False
    return True


def _find_icon_after(lines: list[str], head_idx: int) -> tuple[int, int, list[str]] | None:
    j = head_idx + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    if j >= len(lines):
        return None
    if _bare_icon_line(lines[j]):
        return j, j + 1, [lines[j]]
    if lines[j].strip() != "::: kb-center":
        return None
    chunk, nxt = _take_fence(lines, j)
    if _center_is_bare_icon(chunk):
        return j, nxt, chunk
    return None


def _find_icon_before(lines: list[str], head_idx: int) -> tuple[int, int, list[str]] | None:
    p = head_idx - 1
    while p >= 0 and not lines[p].strip():
        p -= 1
    if p < 0:
        return None
    if _bare_icon_line(lines[p]):
        return p, p + 1, [lines[p]]
    if not _FENCE_CLOSE.match(lines[p].strip()):
        return None
    q = p
    while q >= 0 and not _FENCE_OPEN.match(lines[q].strip()):
        q -= 1
    if q < 0 or lines[q].strip() != "::: kb-center":
        return None
    chunk = lines[q : p + 1]
    if _center_is_bare_icon(chunk):
        return q, p + 1, chunk
    return None


def _is_box_stop(name: str | None, col: str) -> bool:
    if not name or name == col:
        return False
    bare = _compact(re.sub(r"^!\[[^\]]*\]\([^)]+\)\s*", "", name))
    if bare == col:
        return False
    if name in _BOX_COLS or name in _COLUMNS or name == _GUIDE or name == _LINK:
        return True
    if bare in _BOX_COLS or bare in _COLUMNS or bare == _GUIDE or bare == _LINK:
        return True
    if _is_stop_heading(name) or _is_stop_heading(bare):
        return True
    if col == _GUIDE and (name in _GUIDE_INNER or bare in _GUIDE_INNER):
        return False
    if col in {_THINK, _WRITE}:
        return True
    return False


def _looks_question_chunk(chunk: list[str] | str) -> bool:
    text = chunk if isinstance(chunk, str) else "\n".join(chunk)
    for raw in text.splitlines():
        key = _compact(_ATX.sub(r"\2", raw.strip()).lstrip("*"))
        if key.startswith("\u95ee\u9898"):
            return True
    return False


def _looks_prompt_line(line: str) -> bool:
    text = line.strip()
    if not text or text.startswith(("!", ":::", "<")):
        return False
    compact = _compact(_ATX.sub(r"\2", text).lstrip("*"))
    if re.match(r"^[（(]\d+", compact) or re.match(r"^\d+[\.．、]", compact):
        return True
    if "？" in text or "?" in text:
        return True
    starters = (
        "写出",
        "填写",
        "完成",
        "试",
        "根据",
        "阅读",
        "观察",
        "分析",
        "比较",
        "归纳",
        "讨论",
        "判断",
        "推测",
        "已知",
        "有一",
        "请用",
        "请写",
        "如何",
        "为什么",
    )
    return any(compact.startswith(item) for item in starters)


def _has_col_content(kept: list[str]) -> bool:
    for line in kept:
        stripped = line.strip()
        if not stripped or stripped.startswith(":::"):
            continue
        if _BARE_IMG.match(stripped):
            continue
        em = _EM_LINE.match(stripped)
        if em and "\u56fe" in em.group(1):
            continue
        return True
    return False


def _next_is_dash(lines: list[str], idx: int) -> bool:
    j = idx
    while j < len(lines) and not lines[j].strip():
        j += 1
    return j < len(lines) and lines[j].strip() == "::: kb-dash"


def _render_col_icon(img: str, heading: str) -> list[str]:
    return ["::: kb-section-icon", img, "", heading, ":::"]


def _collect_col_body(lines: list[str], start: int, col: str) -> tuple[list[str], int]:
    kept: list[str] = []
    i = start
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            kept.append(lines[i])
            i += 1
            continue
        if stripped == "::: kb-dash":
            break
        if stripped == "::: kb-section-icon":
            close = _fence_close(lines, i)
            inner = _compact("\n".join(lines[i + 1 : close]))
            if any(name in inner for name in _BOX_COLS | _COLUMNS if name != col):
                break
            if _GUIDE in inner and col != _GUIDE:
                break
            chunk, i = _take_fence(lines, i)
            kept.extend(chunk)
            continue
        if _FENCE_OPEN.match(stripped):
            chunk, i = _take_fence(lines, i)
            kept.extend(chunk)
            if col == _EXPERIMENT and _looks_question_chunk(chunk):
                break
            continue
        name = _heading_name(stripped)
        if _is_box_stop(name, col):
            break
        if (
            col in {_THINK, _WRITE}
            and _has_col_content(kept)
            and not _looks_prompt_line(stripped)
            and not _BARE_IMG.match(stripped)
            and not stripped.startswith("<table")
            and not (_EM_LINE.match(stripped) and "\u56fe" in stripped)
        ):
            break
        kept.append(lines[i])
        i += 1
        if col == _EXPERIMENT and _looks_question_chunk(stripped):
            break
    while kept and not kept[-1].strip():
        kept.pop()
    return kept, i


def _emit_col_tail(
    out: list[str],
    body: list[str],
    lines: list[str],
    after: int,
) -> bool:
    if body:
        if out and out[-1].strip():
            out.append("")
        out.extend(body)
        if out[-1].strip():
            out.append("")
    if _next_is_dash(lines, after):
        return False
    out.extend(_DASH_FENCE)
    if after < len(lines):
        out.append("")
    return True


def _col_from_inner(inner: str) -> str | None:
    for col in (_EXPERIMENT, _GUIDE, _THINK, _WRITE):
        if col in inner:
            return col
    return None


def _pop_preceding_icon(out: list[str]) -> str:
    while out and not out[-1].strip():
        out.pop()
    if not out:
        return ""
    if _bare_icon_line(out[-1]):
        img = out.pop().strip()
        while out and not out[-1].strip():
            out.pop()
        return img
    if out[-1].strip() == ":::":
        q = len(out) - 1
        while q >= 0 and out[q].strip() != "::: kb-center":
            q -= 1
        if q >= 0:
            img = _img_from_chunk(out[q:])
            del out[q:]
            return img
    return ""


def apply_guide_experiment(source: str, mid: str) -> tuple[str, str]:
    """\u5b9e\u9a8c\u63a2\u7a76\u3001\u5b66\u4e60\u6307\u5357\u3001\u60f3\u4e00\u60f3\u3001\u4e66\u5199\u8868\u8fbe\uff1a\u56fe\u6807\u548c\u680f\u540d\u6536\u6210 kb-section-icon\uff0c\u6846\u5e95\u52a0 kb-dash\u3002"""
    if _LEGEND in (source or "") or _LEGEND in (mid or ""):
        return source, mid
    compact = _compact(mid or "")
    if not any(col in compact for col in _BOX_COLS):
        return source, mid
    lines = (mid or source).splitlines()
    out: list[str] = []
    mutated = False
    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if stripped == "::: kb-page-toc":
            chunk, i = _take_fence(lines, i)
            out.extend(chunk)
            continue
        if stripped == "::: kb-section-icon":
            close = _fence_close(lines, i)
            inner = _compact("\n".join(lines[i + 1 : close]))
            col = _col_from_inner(inner)
            if col:
                out.extend(lines[i : close + 1])
                body, after = _collect_col_body(lines, close + 1, col)
                if _emit_col_tail(out, body, lines, after):
                    mutated = True
                i = after
                continue
            out.extend(lines[i : close + 1])
            i = close + 1
            continue
        name = _heading_name(stripped)
        if name in _BOX_COLS:
            head_line = f"## {name}"
            after_icon = _find_icon_after(lines, i)
            before_icon = None if after_icon else _find_icon_before(lines, i)
            if after_icon:
                icon_img = _img_from_chunk(after_icon[2])
                body_from = after_icon[1]
            elif before_icon:
                icon_img = _img_from_chunk(before_icon[2])
                popped = _pop_preceding_icon(out)
                icon_img = icon_img or popped
                body_from = i + 1
            else:
                icon_img = _pop_preceding_icon(out)
                body_from = i + 1
            if icon_img:
                out.extend(_render_col_icon(icon_img, head_line))
            else:
                out.append(head_line)
            mutated = True
            body, after = _collect_col_body(lines, body_from, name)
            _emit_col_tail(out, body, lines, after)
            i = after
            continue
        if _FENCE_OPEN.match(stripped):
            chunk, i = _take_fence(lines, i)
            out.extend(chunk)
            continue
        out.append(lines[i])
        i += 1
    if not mutated:
        return source, mid
    return source, _tidy("\n".join(out))


def apply_experiment_continue(source: str, mid: str) -> tuple[str, str]:
    """跨页实验探究、化学史话：后页只补框底虚线，不重写栏名。"""
    if _LEGEND in (source or "") or _LEGEND in (mid or ""):
        return source, mid
    text = mid or source
    compact = _compact(text)
    if any(name in compact for name in (_EXPERIMENT, _HISTORY, _GUIDE, _LINK)):
        return source, mid
    has_record = "现象记录" in text or "实验结论" in text
    has_question = "问题" in text and ("如图" in text or "实验" in compact)
    if not has_record and not has_question:
        return source, mid
    lines = text.splitlines()
    hit = None
    for i, ln in enumerate(lines):
        key = _compact(_ATX.sub(r"\2", ln.strip()).lstrip("*"))
        if key.startswith("问题"):
            hit = i
    if hit is None:
        for i, ln in enumerate(lines):
            key = _compact(_ATX.sub(r"\2", ln.strip()).lstrip("*"))
            if key.startswith("实验结论") or key.startswith("现象记录"):
                hit = i
    if hit is None:
        return source, mid
    if _next_is_dash(lines, hit + 1):
        return source, mid
    insert = hit + 1
    new_lines = lines[:insert]
    if new_lines and new_lines[-1].strip():
        new_lines.append("")
    new_lines.extend(_DASH_FENCE)
    new_lines.append("")
    new_lines.extend(lines[insert:])
    return source, _tidy("\n".join(new_lines))


def apply_chemistry_shapes(
    source: str,
    mid: str,
    masks: dict[str, set[str]] | None = None,
    inbox: dict[str, Any] | None = None,
    hats: list[tuple[str, str]] | None = None,
    captions: dict[str, tuple[list[str], list[str]]] | None = None,
    prompts: list[tuple[str, str, str]] | None = None,
    section: tuple[str, str] | None = None,
) -> tuple[str, str]:
    """\u5316\u5b66\u6559\u6750\u7248\u5f0f\u5165\u53e3\u3002\u540e\u9762\u65b0\u5f62\u72b6\u63a5\u5728\u8fd9\u91cc\uff0c\u4e0d\u8981\u53e6\u8d77\u7279\u6307\u680f\u76ee\u7684\u5de5\u5177\u3002"""
    source, mid = apply_focus_review(source, mid, masks)
    mid = _unwrap_centered_headings(mid or source)
    source, mid = apply_databank(source, mid, masks, inbox)
    source, mid = apply_topic_hats(source, mid, hats)
    source, mid = apply_group_captions(source, mid, captions)
    source, mid = apply_prompt_columns(source, mid, prompts)
    source, mid = apply_guide_experiment(source, mid)
    source, mid = apply_experiment_continue(source, mid)
    source, mid = apply_section_heads(source, mid, section)
    mid = _tidy(mid or "")
    try:
        from app.mid_text import strip_skin
        from app.page_sync import page_bytes

        source = page_bytes(strip_skin(mid))
    except Exception:
        pass
    return source, mid


def _page_stems(book: Path) -> list[str]:
    pages_dir = book / "pages"
    mid_dir = book / "mid"
    if pages_dir.is_dir():
        return [p.stem for p in sorted(pages_dir.glob("*.md")) if p.name[:4].isdigit()]
    if mid_dir.is_dir():
        return [p.stem for p in sorted(mid_dir.glob("*.md")) if p.name[:4].isdigit()]
    return []


def tag_chemistry(book_id: str, apply: str = "fix", root: str = "") -> dict[str, Any]:
    """\u5316\u5b66\u6559\u6750\u7248\u5f0f\uff1a\u5b66\u4e60\u805a\u7126\u3001\u77e5\u8bc6\u56de\u653e\u3001\u8d44\u6599\u5e93\u9694\u7ebf\u3001\u5206\u56fe\u77ed\u6807\u4e0e\u603b\u56fe\u6ce8\u3001\u5b9e\u9a8c\u63a2\u7a76\u3001\u5b66\u4e60\u6307\u5357\u3001\u60f3\u4e00\u60f3\u4e0e\u4e66\u5199\u8868\u8fbe\u680f\u6846\u3001\u8282\u9996\u4e3b\u6807\u9898\uff1b\u540e\u7eed\u5316\u5b66\u4e13\u5c5e\u5f62\u72b6\u4e5f\u8fdb\u8fd9\u4e2a\u51fd\u6570\u3002"""
    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    mode = (apply or "fix").strip().lower()
    if mode not in {"scan", "fix"}:
        raise ValueError("apply must be scan or fix")

    book = _book_dir(sid, root)
    meta = _read_meta(book)
    if not is_chemistry_textbook(meta):
        return {
            "book_id": sid,
            "apply": mode,
            "skipped": True,
            "reason": "not chemistry textbook",
            "subject": str(meta.get("subject") or ""),
            "category": str(meta.get("category") or ""),
        }

    from app import library, page_sync

    stems = _page_stems(book)
    if not stems:
        raise FileNotFoundError(str(book / "pages"))
    blocks = _load_blocks(book)
    masks_by_page = _collect_masks(blocks)
    inbox_by_page = _collect_inboxes(blocks)
    hats_by_page = _collect_hats(blocks)
    prompts_by_page = _collect_prompt_icons(blocks)
    captions = _collect_image_captions(_load_content_list(book))
    heads_by_page = _load_section_heads(book)
    hits: list[str] = []
    pending: list[tuple[int, str, str]] = []
    pages = 0
    for stem in stems:
        page_no = int(stem)
        pages += 1
        source = library.get_page_markdown(sid, page_no) or ""
        if not source:
            path = book / "pages" / f"{page_no:04d}.md"
            if path.is_file():
                source = path.read_text(encoding="utf-8")
        mid = library.get_page_mid(sid, page_no) or ""
        if not mid:
            path = book / "mid" / f"{page_no:04d}.md"
            if path.is_file():
                mid = path.read_text(encoding="utf-8")
        if not source and not mid:
            continue
        updated_src, updated_mid = apply_chemistry_shapes(
            source,
            mid or source,
            masks_by_page.get(page_no),
            inbox_by_page.get(page_no),
            hats_by_page.get(page_no),
            captions,
            prompts_by_page.get(page_no),
            heads_by_page.get(page_no),
        )
        if updated_src == source and updated_mid == (mid or source):
            continue
        hits.append(f"{page_no:04d}.md")
        pending.append((page_no, updated_src, updated_mid))

    result: dict[str, Any] = {
        "book_id": sid,
        "apply": mode,
        "pages": pages,
        "changed": len(hits),
        "files": hits[:40],
        "shapes": [
            "focus_review",
            "databank",
            "topic_hat",
            "group_caption",
            "guide_experiment",
            "prompt_columns",
            "experiment_continue",
            "section_head",
        ],
    }
    if not hits:
        result["skipped"] = True
        result["reason"] = "already_ok"
        return result
    if mode != "fix":
        return result
    written = 0
    for page_no, source, mid in pending:
        page_path = library.page_path(sid, page_no)
        mid_path = library.mid_path(sid, page_no)
        with page_sync.suppressing(page_path, mid_path):
            library.write_page(sid, page_no, source, sync=False)
            library.write_mid(sid, page_no, mid, sync=False)
            page_sync.write_stamp(sid, page_no, source, mid)
        written += 1
    result["written"] = written
    return result
