"""Original-size figure widths and same-row groups, from MinerU block geometry.

单一实现，见 doc/fig-code-ownership.md：

- textbook-agent: `app/fig_size.py` 是 shim，转调本文件
- page-body 插槽: `surfaces/page-body/lib/fig-size.mjs` 读本模块算出的宽度/同行表
- 不要 import `app.*`：buddy-api 容器里没有 textbook-agent 的 app 包

Nothing here writes to ``pages/`` or ``mid/``. Widths are handed to the
page-body surface through its render context.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_FIGURE_TYPES = frozenset({"image", "chart", "figure"})
_EQUATION_TYPES = frozenset({"equation", "equation_interline"})
_SIZED_TYPES = _FIGURE_TYPES | _EQUATION_TYPES
_BODY_TYPES = frozenset({"paragraph", "title", "text"})

_MIN_PCT = 4.0
_BODY_W_MIN = 500.0
_BODY_W_MAX = 1000.0
_BODY_W_FALLBACK = 800.0

_Layout = tuple[dict[int, dict[str, float]], dict[int, list[list[str]]]]
_cache: dict[str, tuple[tuple[float, int], _Layout]] = {}

_Y_OVERLAP = 0.55
_X_STACK = 0.35


def _textbooks_root() -> Path:
    raw = os.environ.get("AGENT_TEXTBOOKS_DIR", "").strip()
    if raw:
        return Path(raw)
    home = os.environ.get("AGENT_HOME", "").strip()
    if home:
        return Path(home) / "textbooks"
    return Path(__file__).resolve().parent.parent / "textbooks"


def _safe_id(book_id: str) -> str:
    return "".join(ch for ch in (book_id or "").strip() if ch.isalnum() or ch in "-_")


def _enabled_books() -> set[str]:
    raw = os.environ.get("TEXTBOOK_FIG_SIZE_BOOKS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def is_enabled(book_id: str) -> bool:
    allow = _enabled_books()
    return bool(allow) and ("*" in allow or str(book_id or "") in allow)


def _blocks_file(book_id: str) -> Path | None:
    sid = _safe_id(book_id)
    if not sid:
        return None
    mineru = _textbooks_root() / sid / "mineru"
    if not mineru.is_dir():
        return None
    direct = mineru / "blocks.json"
    if direct.is_file():
        return direct
    for pattern in ("*_content_list_v2.json", "*_content_list.json"):
        found = sorted(mineru.glob(pattern))
        if found:
            return found[0]
    return None


def _load_blocks(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        data = data.get("blocks") or data.get("content_list") or []
    if not isinstance(data, list):
        return []
    return [b for b in data if isinstance(b, dict)]


def _xyxy(block: dict[str, Any]) -> tuple[float, float, float, float] | None:
    bb = block.get("bbox")
    if not isinstance(bb, (list, tuple)) or len(bb) < 4:
        return None
    try:
        x0, y0, x1, y1 = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
    except (TypeError, ValueError):
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    if max(abs(x0), abs(y0), abs(x1), abs(y1)) <= 1.5:
        x0, y0, x1, y1 = x0 * 1000.0, y0 * 1000.0, x1 * 1000.0, y1 * 1000.0
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return x0, y0, x1, y1


def _bbox(block: dict[str, Any]) -> tuple[float, float] | None:
    xy = _xyxy(block)
    if xy is None:
        return None
    return xy[0], xy[2]


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return min(a1, b1) - max(a0, b0)


def _same_row(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    ha = a[3] - a[1]
    hb = b[3] - b[1]
    wa = a[2] - a[0]
    wb = b[2] - b[0]
    if ha <= 0 or hb <= 0 or wa <= 0 or wb <= 0:
        return False
    if _overlap(a[1], a[3], b[1], b[3]) / min(ha, hb) < _Y_OVERLAP:
        return False
    if _overlap(a[0], a[2], b[0], b[2]) / min(wa, wb) > _X_STACK:
        return False
    return True


def _cluster_rows(items: list[tuple[str, tuple[float, float, float, float]]]) -> list[list[str]]:
    if len(items) < 2:
        return []
    unused = list(range(len(items)))
    rows: list[list[str]] = []
    while unused:
        seed = unused.pop(0)
        group = [seed]
        changed = True
        while changed:
            changed = False
            for j in list(unused):
                if any(_same_row(items[k][1], items[j][1]) for k in group):
                    group.append(j)
                    unused.remove(j)
                    changed = True
        if len(group) < 2:
            continue
        group.sort(key=lambda k: (items[k][1][0], items[k][0]))
        names: list[str] = []
        seen: set[str] = set()
        for k in group:
            name = items[k][0]
            if name in seen:
                continue
            seen.add(name)
            names.append(name)
        if len(names) >= 2:
            rows.append(names)
    return rows


def _page_no(block: dict[str, Any]) -> int:
    for key in ("page_no", "page_idx", "page_index"):
        val = block.get(key)
        if isinstance(val, int):
            return val + 1 if key != "page_no" else val
    return 0


def _asset_name(block: dict[str, Any]) -> str:
    candidates: list[Any] = [block.get(k) for k in ("img_path", "image_path", "src")]
    content = block.get("content")
    if isinstance(content, dict):
        candidates += [content.get(k) for k in ("img_path", "image_path", "src", "path")]
        source = content.get("image_source")
        if isinstance(source, dict):
            candidates += [source.get(k) for k in ("path", "img_path", "image_path", "src")]
        elif isinstance(source, str):
            candidates.append(source)
    for cand in candidates:
        if isinstance(cand, str) and cand.strip():
            return cand.strip().replace("\\", "/").rsplit("/", 1)[-1]
    return ""


def _sized_name(block: dict[str, Any], xy: tuple[float, float, float, float]) -> str:
    """Filename shared with markdown / recrop: MinerU hash, else ``eq-p…``."""
    name = _asset_name(block)
    if name:
        return name
    if str(block.get("type") or "").lower() not in _EQUATION_TYPES:
        return ""
    try:
        from equation_display import diagram_asset_name, is_diagram_block
    except ImportError:
        return ""
    if not is_diagram_block(block):
        return ""
    return diagram_asset_name(_page_no(block), xy)


def _body_width(blocks: list[dict[str, Any]]) -> float:
    widths = sorted(
        x1 - x0
        for block in blocks
        if str(block.get("type") or "").lower() in _BODY_TYPES
        for x0, x1 in [_bbox(block) or (0.0, 0.0)]
        if x1 > x0
    )
    if not widths:
        return _BODY_W_FALLBACK
    p90 = widths[min(len(widths) - 1, int(len(widths) * 0.9))]
    return min(_BODY_W_MAX, max(_BODY_W_MIN, p90))


def _compute(path: Path) -> _Layout:
    blocks = _load_blocks(path)
    if not blocks:
        return {}, {}
    body_w = _body_width(blocks)
    widths: dict[int, dict[str, float]] = {}
    figs: dict[int, list[tuple[str, tuple[float, float, float, float]]]] = {}
    for block in blocks:
        if str(block.get("type") or "").lower() not in _SIZED_TYPES:
            continue
        xy = _xyxy(block)
        if xy is None:
            continue
        name = _sized_name(block, xy)
        if not name:
            continue
        x0, _y0, x1, _y1 = xy
        pct = round(min(100.0, max(_MIN_PCT, (x1 - x0) / body_w * 100.0)), 1)
        page = _page_no(block)
        bucket = widths.setdefault(page, {})
        bucket[name] = max(bucket.get(name, 0.0), pct)
        figs.setdefault(page, []).append((name, xy))
    rows = {page: _cluster_rows(items) for page, items in figs.items()}
    return widths, rows


def _book_layout(book_id: str) -> _Layout:
    empty: _Layout = ({}, {})
    if not is_enabled(book_id):
        return empty
    path = _blocks_file(book_id)
    if path is None:
        return empty
    try:
        stat = path.stat()
    except OSError:
        return empty
    stamp = (stat.st_mtime, stat.st_size)
    cached = _cache.get(book_id)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    layout = _compute(path)
    _cache[book_id] = (stamp, layout)
    return layout


def book_fig_widths(book_id: str) -> dict[int, dict[str, float]]:
    return _book_layout(book_id)[0]


def book_fig_rows(book_id: str) -> dict[int, list[list[str]]]:
    return _book_layout(book_id)[1]


def page_fig_widths(book_id: str, page_no: int) -> dict[str, float]:
    return book_fig_widths(book_id).get(int(page_no), {})


def page_fig_rows(book_id: str, page_no: int) -> list[list[str]]:
    return book_fig_rows(book_id).get(int(page_no), [])


def reset_for_tests() -> None:
    _cache.clear()
