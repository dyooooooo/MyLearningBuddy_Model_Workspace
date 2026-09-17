"""页级竖轨 + 递归切带，产出 layout.tree。

零写盘、零书名判断。入参是一页 MinerU 块 + 页宽高 + 阈值。
阈值在 workspace/ir/band_rules.yaml，改完下次调用即生效。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from statistics import median
from typing import Any

FORMAT = "nested_bands_v3"
_RULES_NAME = "band_rules.yaml"

_RULE_KEYS = (
    "COL_GAP",
    "SPAN_FRAC",
    "ABSORB_GAP",
    "ABSORB_MAX_H",
    "WIDE_IMAGE_FRAC",
    "WIDE_PACK_FRAC",
    "TIGHT_PACK",
    "PAIR_GAP",
    "ICON_MAX",
    "CHROME_ICON_W",
    "CAPTION_SHARE_FRAC",
    "LINE_EM_RATIO",
    "BLANK_MIN",
    "INNER_GAP",
    "TRACK_IN_FRAC",
    "CONTAIN_FRAC",
    "SAME_ROW_Y",
    "FIG_NEAR_X",
    "CAPTION_MAX_H",
    "MIN_FIG_H",
    "TRACK_CROSS_FRAC",
    "CLIP_PAD",
    "SNAP",
    "BAND_SNAP",
    "INDENT_PX",
)

# 一条带里两列之间至少要空多少像素，才算两列而不是一列
COL_GAP = 14.0
# 宽度占版心这个比例以上的块算通栏
SPAN_FRAC = 0.85
# 通栏块向上吸附同栏目小块时允许的垂直间隙
ABSORB_GAP = 28.0
# 通栏向上吞并的总高度上限，防止一串段落被链式吞完
ABSORB_MAX_H = 56.0
# 图段占页宽这个比例以上才当横切带
WIDE_IMAGE_FRAC = 0.20
# 小包里够宽的块，用来切断后面的紧贴
WIDE_PACK_FRAC = 0.35
# 垂直间隙小于这个数，两块叠成一个小包
TIGHT_PACK = 8.0
# 相邻小包并成一条带的最大垂直间隙
PAIR_GAP = 80.0
# 小图标：宽高都小于这个数。图段里的面板不算
ICON_MAX = 60.0
# 页眉带里、第一行正文之上的小图，窄于这个数做出占宽
CHROME_ICON_W = 120.0
# 题注宽度占图组这个比例以上，算整行题
CAPTION_SHARE_FRAC = 0.45
# 行高中位数打这个折，当作一个字宽
LINE_EM_RATIO = 0.88
# 格内上下空档大于这个数才补空白段
BLANK_MIN = 12.0
# 行内两端空白小于这个数就不单独留一列
INNER_GAP = 8.0
# 判「完全落在一轨里」的重叠比例
TRACK_IN_FRAC = 0.85
# 小框算「落在大框里」的面积占比
CONTAIN_FRAC = 0.8
# 竖向重叠占矮框这个比例以上，算同一行
SAME_ROW_Y = 0.5
# 图和旁边的图横向只隔这么远，仍算同一图段
FIG_NEAR_X = 40.0
# 矮过这个高度的字块才当题注并进图
CAPTION_MAX_H = 24.0
# 图组裁完之后矮过这个高度，不当横切带
MIN_FIG_H = 20.0
# 图并集同时盖住两轨各这么多，才算跨轨
TRACK_CROSS_FRAC = 0.6
# 兄弟重叠时把大块顶下去的间隙
CLIP_PAD = 6.0
# 坐标对齐用的像素容差
SNAP = 2.0
# 切带时对通栏顶底的对齐容差
BAND_SNAP = 4.0
# 缩进一级写成像素，render-page.mjs 用 >= 12 判「缩两字」
INDENT_PX = 12


def _rules_path() -> Path:
    return Path(__file__).resolve().parent.parent / "ir" / _RULES_NAME


def load_band_rules(path: Path | None = None) -> dict[str, float]:
    """读 band_rules.yaml。缺文件或缺键时用模块里的默认值。"""
    out = {key: float(globals()[key]) for key in _RULE_KEYS}
    target = path or _rules_path()
    if not target.is_file():
        return out
    try:
        import yaml

        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception:
        return out
    if not isinstance(data, dict):
        return out
    for key in _RULE_KEYS:
        if key in data:
            out[key] = float(data[key])
    return out


def apply_band_rules(rules: dict[str, float] | None = None) -> dict[str, float]:
    loaded = load_band_rules() if rules is None else {**load_band_rules(), **rules}
    g = globals()
    for key, val in loaded.items():
        if key in g:
            g[key] = int(val) if key == "INDENT_PX" else float(val)
    return loaded


_RULES_LOCK = threading.RLock()


@contextlib.contextmanager
def band_rules_applied(rules: dict[str, float] | None = None) -> Iterator[dict[str, float]]:
    """把阈值装进模块全局，用完还原。

    阈值被模块里几十处按名字直读，改成层层传参不现实，所以只能落在全局上。
    那就把「装载 → 用 → 还原」圈进一把锁：
    - 显式传 rules 的调优／排障调用不会漏到后面的页（以前 --explain 调一次，
      同进程后续每一页都跟着用那个值）；
    - FastAPI 线程池里两个请求不会互相改对方正在用的阈值。
    """
    g = globals()
    with _RULES_LOCK:
        saved = {key: g[key] for key in _RULE_KEYS if key in g}
        try:
            yield apply_band_rules(rules)
        finally:
            g.update(saved)


def _area(b: list[float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _intersect(a: list[float], b: list[float]) -> list[float] | None:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _contained(inner: list[float], outer: list[float], frac: float | None = None) -> bool:
    # 阈值不能写成默认参数：默认值在 import 时就求值冻死，band_rules.yaml 改不动它
    frac = CONTAIN_FRAC if frac is None else frac
    hit = _intersect(inner, outer)
    if hit is None:
        return False
    den = _area(inner)
    return den > 0 and _area(hit) / den >= frac


def _clip(inner: list[float], outer: list[float]) -> list[float]:
    """框落到外框里。不相交就退回外框。"""
    hit = _intersect(inner, outer)
    if hit is None:
        return list(outer)
    return hit


def _walk(node: dict, parent: int | None, out: list[dict], discarded: bool) -> None:
    """块、行、span 都收成一条占位。图的孩子同样收。"""
    bbox = node.get("bbox")
    if not (isinstance(bbox, list) and len(bbox) == 4):
        return
    idx = len(out)
    typ = str(node.get("type") or "block")
    out.append(
        {
            "id": idx,
            "parent": parent,
            "bbox": [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])],
            "type": typ,
            "kind": "block",
            "discarded": discarded,
            "cross_page": bool(node.get("cross_page")),
            "text": "",
        }
    )
    for child in node.get("blocks") or []:
        if isinstance(child, dict):
            _walk(child, idx, out, discarded)
    for line in node.get("lines") or []:
        if not isinstance(line, dict):
            continue
        lb = line.get("bbox")
        if not (isinstance(lb, list) and len(lb) == 4):
            continue
        lid = len(out)
        out.append(
            {
                "id": lid,
                "parent": idx,
                "bbox": [float(lb[0]), float(lb[1]), float(lb[2]), float(lb[3])],
                "type": "line",
                "kind": "line",
                "discarded": discarded,
                "cross_page": False,
                "text": "",
            }
        )
        for span in line.get("spans") or []:
            if not isinstance(span, dict):
                continue
            sb = span.get("bbox")
            if not (isinstance(sb, list) and len(sb) == 4):
                continue
            out.append(
                {
                    "id": len(out),
                    "parent": lid,
                    "bbox": [float(sb[0]), float(sb[1]), float(sb[2]), float(sb[3])],
                    "type": str(span.get("type") or "span"),
                    "kind": "span",
                    "discarded": discarded,
                    "cross_page": bool(span.get("cross_page")),
                    "text": str(span.get("content") or ""),
                }
            )


def collect_boxes(page: dict, extra_blocks: list[dict] | None = None) -> list[dict]:
    """从一页 layout.json 抽出全部占位框。升格栏目框走 extra_blocks，下标接在 para_blocks 后面。"""
    out: list[dict] = []
    for block in page.get("discarded_blocks") or []:
        if isinstance(block, dict):
            _walk(block, None, out, True)
    body = [b for b in (page.get("para_blocks") or []) if isinstance(b, dict)]
    for src_i, block in enumerate(body):
        start = len(out)
        _walk(block, None, out, False)
        for item in out[start:]:
            item["src_i"] = src_i
    extras = [b for b in (extra_blocks or []) if isinstance(b, dict)]
    for i, block in enumerate(extras):
        start = len(out)
        _walk(block, None, out, False)
        for item in out[start:]:
            item["src_i"] = len(body) + i
    return out


def drop_cross_page(boxes: list[dict], page_h: float | None = None) -> list[dict]:
    """行或 span 必须落在父块框里。标了 cross_page 的丢掉。父块框裁到本页。"""
    by_id = {b["id"]: b for b in boxes}
    kept: list[dict] = []
    for box in boxes:
        if box.get("cross_page"):
            continue
        if box["kind"] in {"line", "span"}:
            parent = by_id.get(box["parent"])
            if parent is not None and not _contained(box["bbox"], parent["bbox"], SAME_ROW_Y):
                continue
        kept.append(box)
    if page_h is None:
        return kept
    kids: dict[int, list[list[float]]] = {}
    for box in kept:
        parent = box.get("parent")
        if parent is not None:
            kids.setdefault(parent, []).append(box["bbox"])
    for box in kept:
        if box["kind"] != "block" or box["parent"] is not None:
            continue
        bb = list(box["bbox"])
        if bb[3] > page_h:
            bb[3] = float(page_h)
        own = kids.get(box["id"]) or []
        if own:
            bb[1] = max(bb[1], min(c[1] for c in own))
            bb[3] = min(bb[3], max(c[3] for c in own))
        if bb[3] > bb[1]:
            box["bbox"] = bb
    return kept


def isolate_chrome(boxes: list[dict], page_h: float) -> tuple[list[dict], list[dict]]:
    """discarded 整支出占宽。页眉带里、且在第一行正文之上的小图也出占宽。"""
    chrome = [b for b in boxes if b["discarded"] and b["kind"] == "block" and b["parent"] is None]
    live = [b for b in boxes if not b["discarded"]]
    texts = [
        b
        for b in live
        if b["kind"] == "block"
        and b["parent"] is None
        and b["type"] in {"text", "title", "interline_equation"}
    ]
    first_text = min((b["bbox"][1] for b in texts), default=page_h)
    extra: list[dict] = []
    rest: list[dict] = []
    for box in live:
        if (
            box["kind"] == "block"
            and box["parent"] is None
            and box["type"] == "image"
            and box["bbox"][3] <= first_text
            and (box["bbox"][2] - box["bbox"][0]) < CHROME_ICON_W
        ):
            extra.append(box)
        else:
            rest.append(box)
    return chrome + extra, rest


def _is_icon(box: dict) -> bool:
    """小图标：宽高都小于 ICON_MAX。图段里的面板不算。"""
    if box["type"] != "image":
        return False
    bbox = box["bbox"]
    return (bbox[2] - bbox[0]) < ICON_MAX and (bbox[3] - bbox[1]) < ICON_MAX


# ------------------------------------------------------------------ 几何工具


def _overlap_x(a: list[float], col: list[float]) -> float:
    return max(0.0, min(a[2], col[1]) - max(a[0], col[0]))


def _y_overlap_ratio(a: list[float], b: list[float]) -> float:
    """两框竖向重叠占矮的那个的比例。判「是不是同一行」用它。"""
    lo = min(a[3] - a[1], b[3] - b[1])
    if lo <= 0:
        return 0.0
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1])) / lo


def _ink(members: list[dict]) -> float:
    return sum(_area(m["bbox"]) for m in members)


def _pack_box(pack: list[dict]) -> list[float]:
    return [
        min(m["bbox"][0] for m in pack),
        min(m["bbox"][1] for m in pack),
        max(m["bbox"][2] for m in pack),
        max(m["bbox"][3] for m in pack),
    ]


def _unique_items(items: list[dict], key: str = "id") -> list[dict]:
    seen: set = set()
    out: list[dict] = []
    for item in items:
        kid = item.get(key)
        if kid in seen:
            continue
        if kid is not None:
            seen.add(kid)
        out.append(item)
    return out


def _src_ids(members: list[dict]) -> list:
    out: list = []
    seen: set = set()
    for mem in members:
        sid = mem.get("src_i")
        if sid is None or sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
    return out


def page_frame(boxes: list[dict]) -> list[float]:
    """版心左右缘：全部根块 x 的并集。"""
    roots = [b["bbox"] for b in boxes if b["kind"] == "block" and b["parent"] is None]
    if not roots:
        return [0.0, 1.0]
    return [min(b[0] for b in roots), max(b[2] for b in roots)]


def band_columns(
    members: list[dict], frame: list[float], gap: float | None = None
) -> list[list[float]]:
    """这条带自己的列：把成员 bbox 投到 x 轴，按空白断开聚簇。投栏轨时用。"""
    gap = COL_GAP if gap is None else gap
    spans = sorted((m["bbox"][0], m["bbox"][2]) for m in members)
    if not spans:
        return [[float(frame[0]), float(frame[1])]]
    cols: list[list[float]] = [[float(spans[0][0]), float(spans[0][1])]]
    for x0, x1 in spans[1:]:
        if x0 - cols[-1][1] > gap:
            cols.append([float(x0), float(x1)])
        else:
            cols[-1][1] = max(cols[-1][1], float(x1))
    return cols


def _em_of(boxes: list[dict]) -> float:
    """一个字宽的估计：全页行高的中位数打个折。量化缩进台阶用它。"""
    hs = [b["bbox"][3] - b["bbox"][1] for b in boxes if b["kind"] == "line"]
    hs = [h for h in hs if h > 4]
    if not hs:
        return 12.0
    return max(8.0, float(median(hs)) * LINE_EM_RATIO)


def page_tracks(rows: list[dict], roots: list[dict], frame: list[float]) -> dict:
    """整页栏轨。正文轨＝墨最重 pair 带里最宽的一列；侧栏取另一侧实测缘。

    不扫白缝。投不出第二轨时退回单轨，行为等同只有一列。
    """
    pairs = [r for r in rows if r.get("kind") == "pair" and r.get("members")]
    if not pairs:
        return {"frame": list(frame), "main": list(frame), "aside": None, "aside_side": None}
    heavy = max(pairs, key=lambda r: _ink(r["members"]))
    cols = band_columns(heavy["members"], frame)
    main = max(cols, key=lambda c: c[1] - c[0])
    left_gap = main[0] - frame[0]
    right_gap = frame[1] - main[1]
    if left_gap <= COL_GAP and right_gap <= COL_GAP:
        return {"frame": list(frame), "main": list(main), "aside": None, "aside_side": None}
    side = "left" if left_gap > right_gap else "right"
    if side == "left":
        pool = [m for m in roots if m["bbox"][2] <= main[0] + SNAP]
        aside = [frame[0], max(m["bbox"][2] for m in pool)] if pool else [frame[0], main[0]]
    else:
        pool = [m for m in roots if m["bbox"][0] >= main[1] - SNAP]
        aside = [min(m["bbox"][0] for m in pool), frame[1]] if pool else [main[1], frame[1]]
    return {
        "frame": [float(frame[0]), float(frame[1])],
        "main": [float(main[0]), float(main[1])],
        "aside": [float(aside[0]), float(aside[1])],
        "aside_side": side,
    }


# ------------------------------------------------------------------ 包含关系


def _roots(boxes: list[dict]) -> list[dict]:
    return [b for b in boxes if b["kind"] == "block" and b["parent"] is None]


def _descendants(root: dict, boxes: list[dict]) -> list[dict]:
    ids = {root["id"]}
    out = [root]
    changed = True
    while changed:
        changed = False
        for box in boxes:
            if box["id"] in ids:
                continue
            if box["parent"] in ids:
                ids.add(box["id"])
                out.append(box)
                changed = True
    return out


def _near_boxes(a: list[float], b: list[float], x_gap: float | None = None) -> bool:
    """相交，或同一横条上只隔一小段空白，算同一图段。"""
    x_gap = FIG_NEAR_X if x_gap is None else x_gap
    if _intersect(a, b) is not None:
        return True
    if _y_overlap_ratio(a, b) < SAME_ROW_Y:
        return False
    gap = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    return gap <= x_gap


def union_overlapping(roots: list[dict], boxes: list[dict]) -> list[dict]:
    """重叠的图并成一团，子孙题注、图身一并算进大块。"""
    images = [b for b in roots if b["type"] == "image" and not _is_icon(b)]
    used: set[int] = set()
    groups: list[dict] = []
    for img in images:
        if img["id"] in used:
            continue
        members = list(_descendants(img, boxes))
        box = _pack_box(members)
        used.add(img["id"])
        changed = True
        while changed:
            changed = False
            for other in images:
                if other["id"] in used:
                    continue
                if not _near_boxes(box, other["bbox"]):
                    continue
                members.extend(_descendants(other, boxes))
                used.add(other["id"])
                box = _pack_box(members)
                changed = True
        groups.append({"bbox": box, "members": members, "type": "figure_group"})
    return groups


def absorb_captions(groups: list[dict], roots: list[dict]) -> None:
    """矮的题注块并进图段。正文碰到图边不并。"""
    taken = {m["id"] for g in groups for m in g["members"]}
    for group in groups:
        for root in roots:
            if root["id"] in taken or root["type"] in {"image", "image_body", "image_caption"}:
                continue
            if root["bbox"][3] - root["bbox"][1] >= CAPTION_MAX_H:
                continue
            if _intersect(group["bbox"], root["bbox"]) is None:
                continue
            group["members"].append(root)
            taken.add(root["id"])
            group["bbox"] = _pack_box(group["members"])


def clip_to_siblings(group: dict, siblings: list[dict]) -> list[float]:
    """大块不包含没被它吃进去的兄弟。重叠的兄弟把大块顶下去。"""
    box = list(group["bbox"])
    for sib in siblings:
        if _contained(sib["bbox"], box, CONTAIN_FRAC):
            continue
        if _intersect(sib["bbox"], box) is None:
            continue
        if sib["bbox"][3] > box[1] and sib["bbox"][1] < box[3]:
            box[1] = max(box[1], sib["bbox"][3] + CLIP_PAD)
    return box


def _is_wide_image(box: dict, page_w: float) -> bool:
    return box["type"] in {"image", "image_body", "image_caption"} and (
        box["bbox"][2] - box["bbox"][0]
    ) >= page_w * WIDE_IMAGE_FRAC


def is_span_block(box: dict, frame: list[float]) -> bool:
    """通栏：宽度占版心 SPAN_FRAC 以上。图块不当通栏，由图组横切。"""
    if box["type"] in {"image", "image_body", "image_caption"}:
        return False
    frame_w = max(1.0, frame[1] - frame[0])
    return (box["bbox"][2] - box["bbox"][0]) / frame_w >= SPAN_FRAC


def _side_by_side(a: dict, b: dict) -> bool:
    return (
        _y_overlap_ratio(a["bbox"], b["bbox"]) >= SAME_ROW_Y
        and _overlap_x(a["bbox"], [b["bbox"][0], b["bbox"][2]]) <= SNAP
    )


def _has_side_neighbor(cand: dict, roots: list[dict], skip: set[int]) -> bool:
    for other in roots:
        if other["id"] == cand["id"] or other["id"] in skip:
            continue
        if _side_by_side(cand, other):
            return True
    return False


def absorb_above(
    span: dict,
    roots: list[dict],
    skip: set[int],
    frame: list[float] | None = None,
) -> tuple[list[dict], list[float]]:
    """通栏块向上吞并紧贴、且 x 完全落在它范围内的小块。

    已经和旁边并排的块不吞。上方若已经是两列，整段不吞。
    总高度有上限，避免一串段落被链式吞完。
    """
    box = list(span["bbox"])
    members = [span]
    ids = {span["id"]}
    frame = frame or [box[0], box[2]]
    window = [r for r in roots if r["id"] not in skip and r["bbox"][3] > box[1] - ABSORB_GAP * 2]
    window = [r for r in window if r["bbox"][1] < box[1] and r["id"] != span["id"]]
    if window and len(band_columns(window, frame)) >= 2:
        return members, box
    while True:
        picked: list[dict] = []
        for root in roots:
            if root["id"] in ids or root["id"] in skip:
                continue
            rb = root["bbox"]
            if rb[0] < box[0] - SNAP or rb[2] > box[2] + SNAP:
                continue
            if rb[3] > box[1] + SNAP:
                continue
            if box[1] - rb[3] > ABSORB_GAP:
                continue
            if box[3] - rb[1] > ABSORB_MAX_H + (span["bbox"][3] - span["bbox"][1]):
                continue
            if _has_side_neighbor(root, roots, skip | ids):
                continue
            picked.append(root)
        if not picked:
            break
        for root in picked:
            members.append(root)
            ids.add(root["id"])
        box[0] = min(box[0], min(m["bbox"][0] for m in picked))
        box[1] = min(m["bbox"][1] for m in picked)
        box[2] = max(box[2], max(m["bbox"][2] for m in picked))
    return members, box


# ------------------------------------------------------------------ 切横带


def _pair_members(
    roots: list[dict],
    y0: float,
    y1: float,
    page_w: float,
    skip: set[int],
) -> list[dict]:
    items: list[dict] = []
    for root in roots:
        if root["id"] in skip:
            continue
        if root["type"] in {"image_body", "image_caption"}:
            continue
        if _is_wide_image(root, page_w):
            continue
        if root["bbox"][1] >= y1 - SNAP or root["bbox"][3] <= y0 + SNAP:
            continue
        items.append(root)
    items.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
    return items


def _atomic_packs(
    items: list[dict], page_w: float, tight: float | None = None
) -> list[list[dict]]:
    tight = TIGHT_PACK if tight is None else tight
    if not items:
        return []
    packs = [[items[0]]]
    for item in items[1:]:
        prev = packs[-1]
        gap_y = item["bbox"][1] - max(m["bbox"][3] for m in prev)
        wide_end = max(
            (m["bbox"][3] for m in prev if (m["bbox"][2] - m["bbox"][0]) >= page_w * WIDE_PACK_FRAC),
            default=None,
        )
        if wide_end is not None and item["bbox"][1] - wide_end >= 3:
            packs.append([item])
        elif gap_y < tight:
            prev.append(item)
        else:
            packs.append([item])
    return packs


def _split_pair_groups(
    items: list[dict],
    frame: list[float],
    page_w: float,
    gap: float,
) -> list[list[dict]]:
    """先按紧贴叠成小包，再按「列结构合不合得来」决定要不要并成一条带。

    合得来有三种：列结构一模一样；两包横向并排（竖向重叠过半）；
    或者两包左右错开、可以坐进同一条带的两轨（史话头：左图标加标、右小节题）。
    """
    packs = _atomic_packs(items, page_w)
    if not packs:
        return []
    groups = [list(packs[0])]
    for nxt in packs[1:]:
        prev = groups[-1]
        gap_y = min(m["bbox"][1] for m in nxt) - max(m["bbox"][3] for m in prev)
        n_prev = len(band_columns(prev, frame))
        n_next = len(band_columns(nxt, frame))
        n_union = len(band_columns(prev + nxt, frame))
        same = n_prev == n_next == n_union
        side = _y_overlap_ratio(_pack_box(prev), _pack_box(nxt)) >= SAME_ROW_Y
        staggered = _overlap_x(_pack_box(prev), _pack_box(nxt)) <= SNAP
        continues = gap_y < TIGHT_PACK and n_union == n_prev
        if gap_y < gap and (same or side or staggered or continues):
            prev.extend(nxt)
        else:
            groups.append(list(nxt))
    return groups


def _collect_interrupters(
    roots: list[dict],
    groups: list[dict],
    frame: list[float],
    page_w: float,
    taken: set[int],
) -> list[tuple[float, float, list[dict]]]:
    """通栏图组和通栏正文，用来横切横带。"""
    siblings = [r for r in roots if r["type"] not in {"image", "image_body", "image_caption"}]
    interrupters: list[tuple[float, float, list[dict]]] = []
    claimed: set[int] = set(taken)
    for group in groups:
        if group["bbox"][2] - group["bbox"][0] < page_w * WIDE_IMAGE_FRAC:
            continue
        layout = clip_to_siblings(group, siblings)
        if layout[3] - layout[1] < MIN_FIG_H:
            continue
        imgs = [m for m in group["members"] if m["kind"] == "block" and m["parent"] is None]
        interrupters.append((layout[1], layout[3], imgs))
    for root in roots:
        if root["id"] in claimed or not is_span_block(root, frame):
            continue
        members, box = absorb_above(root, roots, claimed, frame)
        for mem in members:
            claimed.add(mem["id"])
        interrupters.append((box[1], box[3], members))
    interrupters.sort(key=lambda t: t[0])
    return interrupters


def cut_rows(
    roots: list[dict],
    groups: list[dict],
    frame: list[float],
    page_w: float,
    gap: float | None = None,
    taken: set[int] | None = None,
) -> list[dict]:
    """先用通栏大块切开；其余段再按列结构切开。行高贴成员框。"""
    gap = PAIR_GAP if gap is None else gap
    taken = taken or set()
    interrupters = _collect_interrupters(roots, groups, frame, page_w, taken)
    claimed = {m["id"] for _a, _b, mem in interrupters for m in mem}
    claimed.update(taken)

    y_min = min(
        (r["bbox"][1] for r in roots if not _is_wide_image(r, page_w) or r["type"] == "image"),
        default=0,
    )
    y_max = max((r["bbox"][3] for r in roots), default=0)
    points = [y_min]
    for a, b, _mem in interrupters:
        points.extend([a, b])
    points.append(y_max)
    points = sorted(set(round(p, 1) for p in points))

    raw_bands: list[tuple[float, float, str]] = []
    for y0, y1 in zip(points, points[1:]):
        if y1 - y0 < TIGHT_PACK:
            continue
        hit_span = False
        for a, b, _mem in interrupters:
            if abs(a - y0) <= BAND_SNAP and abs(b - y1) <= BAND_SNAP:
                hit_span = True
            elif a <= y0 + BAND_SNAP and b >= y1 - BAND_SNAP:
                hit_span = True
        raw_bands.append((y0, y1, "span" if hit_span else "pair"))

    rows: list[dict] = []
    for y0, y1, kind in raw_bands:
        if kind == "span":
            members: list[dict] = []
            for a, b, mem in interrupters:
                if a < y1 - SNAP and b > y0 + SNAP:
                    members.extend(mem)
            uniq = _unique_items(members)
            if not uniq:
                continue
            by0 = min(m["bbox"][1] for m in uniq)
            by1 = max(m["bbox"][3] for m in uniq)
            for a, b, _mem in interrupters:
                if abs(a - y0) <= BAND_SNAP:
                    by0 = a
                    by1 = b
                    break
            rows.append({"kind": "span", "bbox": [frame[0], by0, frame[1], by1], "members": uniq})
            continue
        items = _pair_members(roots, y0, y1, page_w, claimed)
        for grp in _split_pair_groups(items, frame, page_w, gap):
            gy0 = min(m["bbox"][1] for m in grp)
            gy1 = max(m["bbox"][3] for m in grp)
            rows.append({"kind": "pair", "bbox": [frame[0], gy0, frame[1], gy1], "members": grp})
    return rows


def _fully_in(bbox: list[float], track: list[float]) -> bool:
    w = max(1.0, bbox[2] - bbox[0])
    return _overlap_x(bbox, track) >= w * TRACK_IN_FRAC


def _assign_members(members: list[dict], tracks: dict) -> dict[str, list[dict]]:
    """块归哪一轨。完全落在侧栏、且和正文轨之间真有一列空隙的，进侧栏。

    骑在缝上的（练习左列原文从 88 起叠进空着的左轨）仍归正文轨。
    """
    main = tracks["main"]
    aside = tracks.get("aside")
    side = tracks.get("aside_side")
    if not aside:
        return {"main": list(members), "aside": []}
    buckets: dict[str, list[dict]] = {"main": [], "aside": []}
    for mem in members:
        if _fully_in(mem["bbox"], aside) and not _fully_in(mem["bbox"], main):
            gap = main[0] - mem["bbox"][2] if side == "left" else mem["bbox"][0] - main[1]
            if gap > COL_GAP:
                buckets["aside"].append(mem)
                continue
        buckets["main"].append(mem)
    return buckets


def _figure_slot(members: list[dict], tracks: dict) -> str:
    """图段占一轨还是跨两轨。两轨都有图面板才算跨。"""
    aside = tracks.get("aside")
    images = [
        m
        for m in members
        if m["type"] in {"image", "image_body"} and not _is_icon(m)
    ]
    if not images:
        union = _pack_box(members) if members else tracks["main"]
        if aside and _fully_in(union, aside):
            return "aside"
        if aside and _overlap_x(union, aside) >= (aside[1] - aside[0]) * TRACK_CROSS_FRAC:
            if _overlap_x(union, tracks["main"]) >= (tracks["main"][1] - tracks["main"][0]) * TRACK_CROSS_FRAC:
                return "span"
        return "main"
    if not aside:
        return "main"
    in_aside = any(_overlap_x(m["bbox"], aside) > (m["bbox"][2] - m["bbox"][0]) * 0.5 for m in images)
    in_main = any(
        _overlap_x(m["bbox"], tracks["main"]) > (m["bbox"][2] - m["bbox"][0]) * 0.5 for m in images
    )
    if in_aside and in_main:
        return "span"
    return "aside" if in_aside else "main"


def _cell(slot: str, x0: float, x1: float, y0: float, y1: float, members: list[dict]) -> dict:
    return {
        "slot": slot,
        "bbox": [x0, y0, x1, y1],
        "blank": not members,
        "members": _src_ids(members),
    }


def fill_band(row: dict, tracks: dict) -> dict:
    """一条横带：按整页栏轨发格子。一轨一格，空轨就是对齐用的空格子。

    真跨两轨的图或通栏正文发一格 span，不再拆进正文轨。
    """
    frame = tracks["frame"]
    y0, y1 = row["bbox"][1], row["bbox"][3]
    members = list(row["members"] or [])
    aside = tracks.get("aside")
    main = tracks["main"]

    place = "pair"
    if row["kind"] == "span":
        place = _figure_slot(members, tracks) if members else "span"
    if place == "span":
        if members:
            ux0 = min(m["bbox"][0] for m in members)
            ux1 = max(m["bbox"][2] for m in members)
        else:
            ux0, ux1 = frame[0], frame[1]
        cells = [_cell("span", ux0, ux1, y0, y1, members)]
        return {
            "kind": "span",
            "bbox": [frame[0], y0, frame[1], y1],
            "cols": [[ux0, ux1]],
            "cells": cells,
        }

    buckets = _assign_members(members, tracks)
    cells: list[dict] = []
    if aside and tracks.get("aside_side") == "left":
        cells.append(_cell("aside", aside[0], aside[1], y0, y1, buckets["aside"]))
        cells.append(_cell("main", main[0], main[1], y0, y1, buckets["main"]))
    elif aside:
        cells.append(_cell("main", main[0], main[1], y0, y1, buckets["main"]))
        cells.append(_cell("aside", aside[0], aside[1], y0, y1, buckets["aside"]))
    else:
        cells.append(_cell("main", main[0], main[1], y0, y1, buckets["main"]))
    for cell in cells:
        mine = [m for m in members if m.get("src_i") in set(cell["members"])]
        if mine:
            cell.update(_crop_note(_pack_box(mine), cell["bbox"], cell["slot"]))
    return {
        "kind": "pair",
        "bbox": [frame[0], y0, frame[1], y1],
        "cols": [c["bbox"][0:3:2] for c in cells],
        "cells": cells,
    }


# ------------------------------------------------------------------ 格内再套


def _by_id(boxes: list[dict]) -> dict[int, dict]:
    return {b["id"]: b for b in boxes}


def _root_of(box: dict, by_id: dict[int, dict]) -> dict:
    cur = box
    while cur.get("parent") is not None and cur["parent"] in by_id:
        cur = by_id[cur["parent"]]
    return cur


def _first_line(block: dict, boxes: list[dict]) -> dict | None:
    lines = [b for b in boxes if b["parent"] == block["id"] and b["kind"] == "line"]
    if not lines:
        return None
    return min(lines, key=lambda b: b["bbox"][1])


def title_align(block: dict, col: list[float], boxes: list[dict], em: float) -> str:
    """标题在它那一格里是不是真的居中。只有居中和靠左两种。"""
    line = _first_line(block, boxes)
    box = line["bbox"] if line else block["bbox"]
    left_gap = box[0] - col[0]
    right_gap = col[1] - box[2]
    pad = em * 1.5
    if left_gap > pad and right_gap > pad and abs(left_gap - right_gap) <= em * 2:
        return "center"
    return "left"


def _mode_left(lefts: list[float], tol: float = 6.0) -> float:
    """一列正文的左缘：把左缘聚簇，取人数最多的那簇；打平取更靠左的。"""
    if not lefts:
        return 0.0
    xs = sorted(lefts)
    clusters: list[list[float]] = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] <= tol:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    best = max(clusters, key=lambda c: (len(c), -c[0]))
    return sum(best) / len(best)


def column_indent(members: list[dict], boxes: list[dict], em: float) -> dict[int, int]:
    """一列里的正文缩进台阶。进了正文就不再用坐标定位，只把坐标换算成缩进级数。"""
    texts = [m for m in members if m["type"] in {"text", "list"}]
    if not texts:
        return {}
    base = _mode_left([m["bbox"][0] for m in texts])
    offsets: dict[int, float] = {}
    for mem in texts:
        line = _first_line(mem, boxes)
        head = max(0.0, line["bbox"][0] - mem["bbox"][0]) if line is not None else 0.0
        body = max(0.0, mem["bbox"][0] - base)
        offsets[mem["id"]] = max(head, body)

    tol = max(6.0, em * 0.7)
    order = sorted(offsets.values())
    clusters: list[list[float]] = [[order[0]]]
    for off in order[1:]:
        if off - clusters[-1][-1] <= tol:
            clusters[-1].append(off)
        else:
            clusters.append([off])
    centers = [sum(c) / len(c) for c in clusters]
    first = 0 if centers[0] <= tol else 1
    levels = [min(2, first + i) for i in range(len(centers))]

    out: dict[int, int] = {}
    for bid, off in offsets.items():
        idx = min(range(len(centers)), key=lambda i: abs(centers[i] - off))
        out[bid] = levels[idx]

    line_h = em / LINE_EM_RATIO
    singles = [m for m in texts if (m["bbox"][3] - m["bbox"][1]) <= line_h * 1.6]
    if singles:
        ranks = sorted(out[m["id"]] for m in singles)
        conv = ranks[len(ranks) // 2]
        for mem in texts:
            wrapped = (mem["bbox"][3] - mem["bbox"][1]) > line_h * 1.6
            if wrapped and out[mem["id"]] == 0:
                out[mem["id"]] = conv
    return out


def sub_rows(members: list[dict]) -> list[list[dict]]:
    """把一格里的成员分成上下若干行；竖向重叠过半且横向不相撞的算同一行。"""
    rows: list[list[dict]] = []
    for mem in sorted(members, key=lambda m: (m["bbox"][1], m["bbox"][0])):
        placed = False
        for row in rows:
            if all(
                _y_overlap_ratio(mem["bbox"], o["bbox"]) >= SAME_ROW_Y
                and _overlap_x(mem["bbox"], [o["bbox"][0], o["bbox"][2]]) <= SNAP
                for o in row
            ):
                row.append(mem)
                placed = True
                break
        if not placed:
            rows.append([mem])
    for row in rows:
        row.sort(key=lambda m: m["bbox"][0])
    rows.sort(key=lambda r: min(m["bbox"][1] for m in r))
    return rows


def layout_figure(group: dict, boxes: list[dict]) -> dict:
    """图大块内部：各张图身、各张自己的说明、整行题注。不改传入盒子。"""
    by_id = _by_id(boxes)
    bodies = [m for m in group["members"] if m["type"] == "image_body"]
    if not bodies:
        bodies = [m for m in group["members"] if m["type"] == "image" and m["parent"] is None]
    bodies = sorted(bodies, key=lambda b: b["bbox"][0])
    captions = [m for m in group["members"] if m["type"] == "image_caption"]
    extras = [
        m
        for m in group["members"]
        if m["kind"] == "block" and m["parent"] is None and m["type"] in {"text", "title"}
    ]
    group_w = max(group["bbox"][2] - group["bbox"][0], 1.0)
    body_bottom = max((b["bbox"][3] for b in bodies), default=group["bbox"][1])
    panels = []
    for body in bodies:
        root = _root_of(body, by_id)
        panels.append({"body": body, "captions": [], "src_i": root.get("src_i")})
    shared: list[dict] = []
    for cap in captions + extras:
        src_i = cap.get("src_i")
        if src_i is None:
            src_i = _root_of(cap, by_id).get("src_i")
        cap_w = cap["bbox"][2] - cap["bbox"][0]
        hits = []
        for i, panel in enumerate(panels):
            pb = panel["body"]["bbox"]
            if _overlap_x(cap["bbox"], [pb[0], pb[2]]) >= INNER_GAP:
                hits.append(i)
        below = cap["bbox"][1] >= body_bottom - BAND_SNAP
        wide = cap_w >= group_w * CAPTION_SHARE_FRAC
        whole = below and (wide or (len(panels) == 1 and cap["type"] == "image_caption"))
        item = {"box": cap, "src_i": src_i, "text": cap.get("text") or ""}
        if len(hits) >= 2 or whole:
            shared.append(item)
        elif len(hits) == 1:
            panels[hits[0]]["captions"].append(item)
        else:
            shared.append(item)
    for panel in panels:
        panel["captions"].sort(key=lambda c: c["box"]["bbox"][1])
    shared.sort(key=lambda c: c["box"]["bbox"][1])
    return {"panels": panels, "shared": shared}


def _crop_note(raw: list[float], clipped: list[float], slot: str) -> dict:
    extra: dict = {}
    if [round(x, 1) for x in raw] != [round(x, 1) for x in clipped]:
        extra["raw_union"] = list(raw)
        extra["crop"] = slot
    return extra


def nest_figure(cell: dict, figure_inner: dict) -> dict:
    """图按面板拆。图身框不裁到栏轨——真面板可以伸进侧栏。"""
    copy = dict(cell)
    parts: list[dict] = []
    panel_cols = []
    for panel in figure_inner["panels"]:
        body = panel["body"]
        texts = [c.get("text") or "" for c in panel["captions"]]
        panel_cols.append(
            {
                "bbox": list(body["bbox"]),
                "members": [panel.get("src_i")],
                "caption": "\n".join(t for t in texts if t),
                "caption_srcs": [
                    c["box"].get("src_i") for c in panel["captions"] if c.get("box")
                ],
            }
        )
    # 题注的原块被吸进图里了，别再单独画一遍。但 MinerU 的 image_caption 是图块自己的
    # 子框，src_i 跟图身相同；把它算成「已吸收」会连图一起剪掉，所以先把图身自己排除。
    bodies = {p["members"][0] for p in panel_cols if p["members"][0] is not None}
    for col in panel_cols:
        col["caption_srcs"] = [s for s in col["caption_srcs"] if s is not None and s not in bodies]
    if panel_cols:
        parts.append(
            {
                "role": "panel_row",
                "bbox": [
                    min(p["bbox"][0] for p in panel_cols),
                    min(p["bbox"][1] for p in panel_cols),
                    max(p["bbox"][2] for p in panel_cols),
                    max(p["bbox"][3] for p in panel_cols),
                ],
                "members": [p["members"][0] for p in panel_cols],
                "cols": panel_cols,
            }
        )
    slot = str(cell.get("slot") or "main")
    for cap in figure_inner.get("shared") or []:
        raw = list(cap["box"]["bbox"])
        clipped = _clip(raw, cell["bbox"]) if slot != "span" else raw
        part = {
            "role": "shared_caption",
            "bbox": clipped,
            "members": [],
            "text": cap.get("text") or "",
            "align": "center",
            "caption_srcs": [
                s
                for s in ([cap["box"].get("src_i")] if cap.get("box") else [])
                if s is not None and s not in bodies
            ],
        }
        part.update(_crop_note(raw, clipped, slot))
        parts.append(part)
    copy["parts"] = parts
    return copy


def _blank_part(cell_box: list[float], y0: float, y1: float) -> dict:
    return {"role": "blank", "bbox": [cell_box[0], y0, cell_box[2], y1], "members": []}


def _title_part(
    row: list[dict], cell_box: list[float], slot: str, boxes: list[dict], em: float
) -> dict:
    """同一行的标题合成一段。文字裁到轨，越界记 raw_union / crop。"""
    raw = _pack_box(row)
    clipped = _clip(raw, cell_box)
    piece = {
        "role": "title",
        "bbox": [cell_box[0], clipped[1], cell_box[2], clipped[3]],
        "members": [m.get("src_i") for m in row],
        "align": title_align(row[0], [cell_box[0], cell_box[2]], boxes, em),
    }
    piece.update(_crop_note(raw, clipped, slot))
    return piece


def _row_part(row: list[dict], cell_box: list[float], y0: float, y1: float) -> dict:
    """一行里并排的几块：块之间够宽的空当也单独占一列。"""
    inner: list[dict] = []
    cursor = cell_box[0]
    for mem in row:
        if mem["bbox"][0] - cursor >= INNER_GAP:
            inner.append({"bbox": [cursor, y0, mem["bbox"][0], y1], "members": []})
        inner.append({"bbox": _clip(mem["bbox"], cell_box), "members": [mem.get("src_i")]})
        cursor = max(cursor, mem["bbox"][2])
    if cell_box[2] - cursor >= INNER_GAP:
        inner.append({"bbox": [cursor, y0, cell_box[2], y1], "members": []})
    return {
        "role": "row",
        "bbox": [cell_box[0], y0, cell_box[2], y1],
        "members": [m.get("src_i") for m in row],
        "cols": inner,
    }


def _single_part(
    mem: dict,
    cell_box: list[float],
    slot: str,
    indents: dict[int, int],
    boxes: list[dict],
    em: float,
) -> dict:
    raw = list(mem["bbox"])
    clipped = _clip(raw, cell_box)
    piece = {
        "role": mem["type"],
        "bbox": [cell_box[0], clipped[1], cell_box[2], clipped[3]],
        "members": [mem.get("src_i")],
    }
    if mem["type"] == "title":
        piece["align"] = title_align(mem, [cell_box[0], cell_box[2]], boxes, em)
    if mem["id"] in indents:
        piece["indent"] = indents[mem["id"]]
    piece.update(_crop_note(raw, clipped, slot))
    return piece


def nest_flow(cell: dict, members: list[dict], boxes: list[dict], em: float) -> dict:
    """格内按坐标分行。文字、标题裁进格子；同一行的标题合成一段。"""
    copy = dict(cell)
    box = cell["bbox"]
    slot = str(cell.get("slot") or "main")
    indents = column_indent(members, boxes, em)
    parts: list[dict] = []
    y_cursor = box[1]
    for row in sub_rows(members):
        ry0 = min(m["bbox"][1] for m in row)
        ry1 = max(m["bbox"][3] for m in row)
        if ry0 - y_cursor > BLANK_MIN:
            parts.append(_blank_part(box, y_cursor, ry0))
        if all(m["type"] == "title" for m in row):
            parts.append(_title_part(row, box, slot, boxes, em))
        elif len(row) >= 2:
            parts.append(_row_part(row, box, ry0, ry1))
        else:
            parts.append(_single_part(row[0], box, slot, indents, boxes, em))
        y_cursor = ry1
    if box[3] - y_cursor > BLANK_MIN:
        parts.append(_blank_part(box, y_cursor, box[3]))
    if parts:
        copy["parts"] = parts
    return copy


def nest_cell(
    cell: dict,
    members: list[dict],
    boxes: list[dict] | None = None,
    figure_inner: dict | None = None,
    em: float = 12.0,
) -> dict:
    """格内再套：图按包含关系拆；其余按坐标分上下行。"""
    if cell.get("blank") or not members:
        return cell
    if figure_inner and figure_inner.get("panels"):
        return nest_figure(cell, figure_inner)
    return nest_flow(cell, members, boxes or [], em)


def _fill_page(rows: list[dict], frame: list[float]) -> list[dict]:
    """横带上下接满，中间不留洞。"""
    if not rows:
        return rows
    rows = sorted(rows, key=lambda r: r["bbox"][1])
    for i, row in enumerate(rows):
        row["bbox"][0] = frame[0]
        row["bbox"][2] = frame[1]
        if i + 1 < len(rows):
            row["bbox"][3] = rows[i + 1]["bbox"][1]
    return rows


def _span_text(box: dict, boxes: list[dict]) -> str:
    bits = [b.get("text") or "" for b in boxes if b["parent"] == box["id"] and b["kind"] == "span"]
    if bits:
        return "".join(bits)
    kids = [b for b in boxes if b["parent"] == box["id"]]
    return "".join(_span_text(k, boxes) for k in kids)


def _src_root(src_i: object, boxes: list[dict]) -> dict | None:
    if src_i is None:
        return None
    for box in boxes:
        if box.get("src_i") == src_i and box["kind"] == "block" and box["parent"] is None:
            return box
    return None


def _src_text(src_i: object, boxes: list[dict], src: dict | None = None) -> str:
    """src 是摄取环节传进来的块索引：{"texts": {下标: 文本}, "types": {下标: 类型}}。"""
    root = _src_root(src_i, boxes)
    if not root or root["type"] in {"image", "image_body"}:
        return ""
    # 五源合块的文本才是权威的（行间公式已包 $$…$$）；span 缺 bbox 的页只有它拿得到字
    texts = (src or {}).get("texts")
    if texts is not None and src_i in texts:
        return str(texts[src_i] or "")
    return _span_text(root, boxes) or root.get("text") or ""


def plain_text(text: str) -> str:
    """扫描稿里的制表符不是收口两栏。抽字时换成空格。

    收口块的 text 字段只能经这里出（摄取时走 _line_from_pieces，读页重生成时走
    ir_lib._sync_close_blocks）。两边各写一套就会出现磁盘上是空格、接口上是制表符。
    """
    return (text or "").replace("\t", " ")


def _members_text(members: list[object], boxes: list[dict], src: dict | None = None) -> str:
    return plain_text("".join(_src_text(i, boxes, src) for i in members or [] if i is not None))


def _members_bbox(members: list[object], boxes: list[dict], fallback: list[float]) -> list[float]:
    roots = []
    for item in members or []:
        root = _src_root(item, boxes)
        if root is None:
            continue
        if root["type"] in {"image", "image_body"} and _is_icon(root):
            continue
        if root["type"] in {"image", "image_body"}:
            continue
        roots.append(root)
    if not roots:
        return [float(v) for v in fallback]
    return _pack_box(roots)


def _src_is_title(src_i: object, boxes: list[dict], src: dict | None = None) -> bool:
    types = (src or {}).get("types")
    if types is not None and src_i in types:
        return str(types[src_i] or "") == "title"
    root = _src_root(src_i, boxes)
    return bool(root and root["type"] == "title")


def _src_is_icon(src_i: object, boxes: list[dict]) -> bool:
    root = _src_root(src_i, boxes)
    return bool(root and _is_icon(root))


def _part_members(part: dict) -> list[object]:
    out: list[object] = []
    for item in part.get("members") or []:
        if item is not None:
            out.append(item)
    for col in part.get("cols") or []:
        for item in col.get("members") or []:
            if item is not None:
                out.append(item)
    return out


def _lane_of(slot: str, aside_side: str | None) -> str:
    if slot == "aside":
        return "左" if aside_side == "left" else "右"
    return "正文"


def _cell_aside_is_talk(cell: dict, boxes: list[dict], src: dict | None = None) -> bool:
    """侧栏格有标题，或小图标加字，算对话栏目。"""
    if cell.get("slot") != "aside" or cell.get("blank"):
        return False
    has_title = False
    has_icon = False
    has_text = False
    for part in cell.get("parts") or []:
        if part.get("role") in {"blank", "panel_row", "shared_caption"}:
            continue
        mems = _part_members(part)
        if part.get("role") == "title" or any(_src_is_title(i, boxes, src) for i in mems):
            has_title = True
        if part.get("role") == "image" or any(_src_is_icon(i, boxes) for i in mems):
            has_icon = True
        if _members_text(mems, boxes, src).strip():
            has_text = True
    return has_title or (has_icon and has_text)


def _cell_has_long_prose(cell: dict, boxes: list[dict], src: dict | None = None) -> bool:
    """正文格里有没有长段落。只有标题不算长。"""
    for part in cell.get("parts") or []:
        role = part.get("role") or ""
        if role in {"text", "list"} and _members_text(
            part.get("members") or [], boxes, src
        ).strip():
            return True
        if role == "row" and any(c.get("members") for c in part.get("cols") or []):
            return True
    return False


def _band_has_figure(row: dict) -> bool:
    for cell in row.get("cells") or []:
        for part in cell.get("parts") or []:
            if part.get("role") == "panel_row":
                return True
    return False


def _iter_part_pieces(
    part: dict, boxes: list[dict], fallback: list[float], src: dict | None = None
):
    """从一个格子片段里抽出有字的小段。图和空白不要。"""
    role = str(part.get("role") or "")
    if role in {"blank", "panel_row", "shared_caption", "image"}:
        return
    if role == "row" and part.get("cols"):
        for col in part["cols"]:
            mems = [m for m in (col.get("members") or []) if m is not None]
            text = _members_text(mems, boxes, src).strip()
            if not text:
                continue
            box = _members_bbox(mems, boxes, col.get("bbox") or fallback)
            yield {
                "text": text,
                "bbox": box,
                "raw_union": list(box),
                "members": mems,
                "role": "title" if any(_src_is_title(i, boxes, src) for i in mems) else "text",
            }
        return
    mems = [m for m in (part.get("members") or []) if m is not None]
    text = (
        _members_text(mems, boxes, src).strip()
        or plain_text(str(part.get("text") or "")).strip()
    )
    if not text:
        return
    box = _members_bbox(mems, boxes, part.get("raw_union") or part.get("bbox") or fallback)
    yield {
        "text": text,
        "bbox": box,
        "raw_union": list(part.get("raw_union") or box),
        "members": mems,
        "role": (
            "title"
            if role == "title" or any(_src_is_title(i, boxes, src) for i in mems)
            else "text"
        ),
    }


def _hugs_talk(bbox: list[float], talk_union: list[float] | None, tracks: dict) -> bool:
    """左缘落在已有对话外框里，或贴着侧栏。"""
    if not talk_union:
        return False
    if bbox[3] < talk_union[1] - SNAP:
        return False
    if talk_union[0] - COL_GAP <= bbox[0] <= talk_union[2] + COL_GAP:
        if bbox[1] >= talk_union[1] - SNAP:
            return True
    aside = tracks.get("aside")
    if aside and abs(bbox[0] - aside[0]) <= COL_GAP * 2:
        return True
    return False


def _figure_between(a: dict, b: dict, figure_spans: list[list[float]]) -> bool:
    top = a if a["bbox"][1] <= b["bbox"][1] else b
    bot = b if top is a else a
    gap_lo, gap_hi = top["bbox"][3], bot["bbox"][1]
    if gap_hi <= gap_lo + SNAP:
        return False
    for fy0, fy1 in figure_spans:
        if fy0 >= gap_lo - BAND_SNAP and fy0 < gap_hi:
            return True
        if fy1 > gap_lo and fy1 <= gap_hi + BAND_SNAP:
            return True
    return False


def _layer_of(kind: str, lane: str, role: str) -> str:
    if role == "title":
        if kind == "对话" or lane in {"左", "右"}:
            return "标题"
        return "小标题"
    return "句子"


def close_units(
    rows: list[dict], tracks: dict, boxes: list[dict], src: dict | None = None
) -> list[dict]:
    """套完盒子之后收口：打类型，同类型按位置合成大块，同一行两截用制表符。"""
    aside_side = tracks.get("aside_side")
    pieces: list[dict] = []
    for bi, row in enumerate(rows):
        aside_talk = False
        main_cell = None
        for cell in row.get("cells") or []:
            if cell.get("slot") == "aside":
                aside_talk = _cell_aside_is_talk(cell, boxes, src)
            if cell.get("slot") == "main":
                main_cell = cell
        main_only_title = bool(
            aside_talk
            and main_cell
            and not main_cell.get("blank")
            and not _cell_has_long_prose(main_cell, boxes, src)
        )
        for cell in row.get("cells") or []:
            if cell.get("blank"):
                continue
            slot = str(cell.get("slot") or "main")
            lane = _lane_of(slot, aside_side)
            for part in cell.get("parts") or []:
                for raw in _iter_part_pieces(
                    part, boxes, cell.get("bbox") or [0, 0, 1, 1], src
                ):
                    kind = lane
                    if slot == "aside" and aside_talk:
                        kind = "对话"
                    elif slot == "main" and main_only_title and raw["role"] == "title":
                        kind = "对话"
                    elif slot == "span":
                        kind = "正文"
                    pieces.append(
                        {
                            **raw,
                            "band": bi,
                            "slot": slot,
                            "lane": lane,
                            "kind": kind,
                        }
                    )

    talk_union = None
    ordered = sorted(pieces, key=lambda p: (p["band"], p["bbox"][1], p["bbox"][0]))
    for piece in ordered:
        if piece["kind"] == "对话":
            talk_union = (
                list(piece["bbox"])
                if talk_union is None
                else _pack_box([{"bbox": talk_union}, piece])
            )
            continue
        if _hugs_talk(piece["bbox"], talk_union, tracks):
            piece["kind"] = "对话"
            talk_union = _pack_box([{"bbox": talk_union}, piece])

    figure_spans = [
        [row["bbox"][1], row["bbox"][3]] for row in rows if _band_has_figure(row)
    ]
    type_name = {"正文": "正文", "左": "左栏", "右": "右栏", "对话": "对话"}
    units: list[dict] = []
    by_kind: dict[str, list[dict]] = {}
    for piece in pieces:
        by_kind.setdefault(piece["kind"], []).append(piece)
    for kind, group in by_kind.items():
        group = sorted(group, key=lambda p: (p["bbox"][1], p["bbox"][0]))
        run: list[dict] = []
        for piece in group:
            if run and _figure_between(run[-1], piece, figure_spans):
                units.append(_finish_unit(run, type_name[kind]))
                run = []
            run.append(piece)
        if run:
            units.append(_finish_unit(run, type_name[kind]))
    units.sort(key=lambda u: (u["bbox"][1], u["bbox"][0]))
    return units


def _line_from_pieces(picked: list[dict]) -> dict:
    text = "\t".join(p["text"] for p in picked)
    layer = "句子"
    for piece in picked:
        one = _layer_of(piece["kind"], piece["lane"], piece["role"])
        if one == "标题":
            layer = "标题"
            break
        if one == "小标题" and layer == "句子":
            layer = "小标题"
    return {
        "text": text,
        "bbox": _pack_box(picked),
        "layer": layer,
        "bands": sorted({p["band"] for p in picked}),
        "slots": sorted({p["slot"] for p in picked}),
        "pieces": [
            {
                "text": p["text"],
                "bbox": list(p["bbox"]),
                "raw_union": list(p["raw_union"]) if p.get("raw_union") else list(p["bbox"]),
                "lane": p["lane"],
                "layer": _layer_of(p["kind"], p["lane"], p["role"]),
                "members": [m for m in p["members"] if m is not None],
            }
            for p in picked
        ],
    }


def _finish_unit(group: list[dict], kind: str) -> dict:
    used: set[int] = set()
    lines: list[dict] = []
    by_band: dict[int, list[int]] = {}
    for i, piece in enumerate(group):
        by_band.setdefault(piece["band"], []).append(i)
    for idxs in by_band.values():
        slots = {group[i]["slot"] for i in idxs}
        if "aside" in slots and "main" in slots:
            left = sorted(
                (group[i] for i in idxs if group[i]["slot"] == "aside"),
                key=lambda p: (p["bbox"][1], p["bbox"][0]),
            )
            right = sorted(
                (group[i] for i in idxs if group[i]["slot"] == "main"),
                key=lambda p: (p["bbox"][1], p["bbox"][0]),
            )
            if left and right:
                left_one = dict(left[0])
                left_one["text"] = " ".join(p["text"] for p in left)
                left_one["bbox"] = _pack_box(left)
                right_one = dict(right[0])
                right_one["text"] = " ".join(p["text"] for p in right)
                right_one["bbox"] = _pack_box(right)
                lines.append(_line_from_pieces([left_one, right_one]))
                used.update(idxs)
    rest = [p for i, p in enumerate(group) if i not in used]
    fake = [{"bbox": p["bbox"], "id": i} for i, p in enumerate(rest)]
    for row in sub_rows(fake):
        row.sort(key=lambda m: m["bbox"][0])
        lines.append(_line_from_pieces([rest[m["id"]] for m in row]))
    lines.sort(key=lambda ln: (ln["bbox"][1], ln["bbox"][0]))
    bbox = _pack_box(group)
    lanes = [p["lane"] for p in group]
    lane = max(set(lanes), key=lanes.count)
    return {
        "type": kind,
        "lane": lane,
        "bbox": bbox,
        "pieces": [
            {
                "text": p["text"],
                "bbox": list(p["bbox"]),
                "raw_union": list(p["raw_union"]) if p.get("raw_union") else list(p["bbox"]),
                "lane": p["lane"],
                "layer": _layer_of(p["kind"], p["lane"], p["role"]),
                "members": [m for m in p["members"] if m is not None],
                "band": p["band"],
                "slot": p["slot"],
            }
            for p in group
        ],
        "lines": lines,
    }


def _best_figure(groups: list[dict], member_ids: set[int]) -> dict | None:
    """一条带里可能有多组图，按重叠面积取最大的那组。"""
    best = None
    best_a = 0.0
    for group in groups:
        ids = {m["id"] for m in group["members"] if m.get("parent") is None}
        hit = ids & member_ids
        if not hit:
            continue
        area = sum(_area(m["bbox"]) for m in group["members"] if m["id"] in hit)
        if area > best_a:
            best_a = area
            best = group
    return best


def assemble_page(
    page: dict,
    book_id: str = "",
    page_no: int = 0,
    extra_blocks: list[dict] | None = None,
    rules: dict[str, float] | None = None,
    ir_blocks: list[dict] | None = None,
) -> dict:
    """总入口。只返回字典，不写盘。

    ir_blocks 给的是摄取环节五源合块后的块表（下标与 para_blocks 同序，升格栏目框接在后面）。
    传了就用它的文本与类型收口，比从 layout.json 的 span 反推准。
    """
    with band_rules_applied(rules):
        return _assemble_page(page, book_id, page_no, extra_blocks, ir_blocks)


def _assemble_page(
    page: dict,
    book_id: str,
    page_no: int,
    extra_blocks: list[dict] | None,
    ir_blocks: list[dict] | None,
) -> dict:
    raw_size = page.get("page_size") or [595, 841]
    page_w, page_h = float(raw_size[0]), float(raw_size[1])
    boxes = drop_cross_page(collect_boxes(page, extra_blocks), page_h)
    chrome, live = isolate_chrome(boxes, page_h)
    frame = page_frame(live)
    em = _em_of(live)
    roots = _roots(live)
    groups = union_overlapping(roots, live)
    absorb_captions(groups, roots)
    for group in groups:
        group["inner"] = layout_figure(group, live)
        for panel in group["inner"]["panels"]:
            for cap in panel["captions"]:
                cap["text"] = _span_text(cap["box"], live) or cap.get("text") or ""
        for cap in group["inner"]["shared"]:
            cap["text"] = _span_text(cap["box"], live) or cap.get("text") or ""
    taken: set[int] = set()
    for group in groups:
        if group["bbox"][2] - group["bbox"][0] < page_w * WIDE_IMAGE_FRAC:
            continue
        for mem in group["members"]:
            if mem["kind"] == "block" and mem["parent"] is None:
                taken.add(mem["id"])
    raw_rows = cut_rows(roots, groups, frame, page_w, taken=taken)
    raw_rows = _fill_page(raw_rows, frame)
    tracks = page_tracks(raw_rows, roots, frame)
    rows = []
    for raw in raw_rows:
        built = fill_band(raw, tracks)
        raw_ids = {m["id"] for m in raw["members"]}
        for i, cell in enumerate(built["cells"]):
            keep = set(cell.get("members") or [])
            cell_mem = [m for m in raw["members"] if m.get("src_i") in keep]
            fig = _best_figure(groups, {m["id"] for m in cell_mem} or raw_ids)
            use_fig = fig.get("inner") if fig and cell_mem else None
            if use_fig and not any(m["type"] in {"image", "image_body"} for m in cell_mem):
                use_fig = None
            built["cells"][i] = nest_cell(cell, cell_mem, live, use_fig, em)
        rows.append(built)
    src = None
    if ir_blocks is not None:
        src = {
            "texts": {i: str(b.get("text") or "") for i, b in enumerate(ir_blocks)},
            "types": {i: str(b.get("type") or "") for i, b in enumerate(ir_blocks)},
        }
    units = close_units(rows, tracks, live, src)
    return {
        "format": FORMAT,
        "book_id": book_id,
        "page": page_no,
        "page_size": [int(page_w), int(page_h)],
        "frame": [float(frame[0]), float(frame[1])],
        "tracks": {
            "frame": list(tracks["frame"]),
            "main": list(tracks["main"]),
            "aside": list(tracks["aside"]) if tracks.get("aside") else None,
            "aside_side": tracks.get("aside_side"),
        },
        "em": round(em, 2),
        "chrome": [
            {
                "role": c["type"],
                "bbox": c["bbox"],
                "text": _span_text(c, boxes) or c.get("text") or "",
            }
            for c in chrome
        ],
        "rows": rows,
        "units": units,
    }


def folio_side(
    chrome_bboxes: list | None,
    chrome_types: list[str] | None,
    page_w: float,
) -> str:
    """页码在左是 verso，在右是 recto。没有页码框就空串。"""
    if not chrome_bboxes:
        return ""
    boxes = []
    for i, raw in enumerate(chrome_bboxes):
        typ = str(chrome_types[i]) if chrome_types and i < len(chrome_types) else ""
        if typ not in {"page_number", "page-number"}:
            continue
        if isinstance(raw, (list, tuple)) and len(raw) >= 4:
            boxes.append([float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])])
    if not boxes:
        return ""
    bb = max(boxes, key=lambda b: b[3])
    return "verso" if (bb[0] + bb[2]) / 2 < page_w * 0.5 else "recto"


# ------------------------------------------------------------------ 收口 span + 树


_MARK = re.compile(r"<(sub|sup)>(.*?)</\1>", re.I | re.S)


def split_marks(content: str) -> list[dict[str, str]]:
    """把字面 <sub>/<sup> 切成独立 span。没有标记就原样一条 text。"""
    text = str(content or "")
    out: list[dict[str, str]] = []
    pos = 0
    for match in _MARK.finditer(text):
        if match.start() > pos:
            out.append({"t": "text", "c": text[pos:match.start()]})
        out.append({"t": match.group(1).lower(), "c": match.group(2)})
        pos = match.end()
    if pos == 0:
        return [{"t": "text", "c": text}] if text else []
    if pos < len(text):
        out.append({"t": "text", "c": text[pos:]})
    return out


def _copy_span(span: dict) -> dict:
    item = {k: v for k, v in span.items() if v is not None}
    kind = str(item.get("t") or "text")
    if kind == "text":
        bits = split_marks(str(item.get("c") or ""))
        if len(bits) == 1 and bits[0]["t"] == "text":
            return item
        return bits
    return item


def _flatten_spans(spans: list) -> list[dict]:
    out: list[dict] = []
    for span in spans:
        if isinstance(span, list):
            out.extend(span)
        elif isinstance(span, dict):
            out.append(span)
    return out


def _unwrap_tex(text: str) -> str:
    """`$$…$$` / `$…$` 里取出 LaTeX。没有美元号就返回空串（当普通文本处理）。"""
    body = str(text or "").strip()
    for fence in ("$$", "$"):
        if body.startswith(fence) and body.endswith(fence) and len(body) > 2 * len(fence):
            return body[len(fence):-len(fence)].strip()
    return ""


def spans_from_block(blk: dict) -> list[dict]:
    """从现行块抽出收口要用的 span，保留公式/图种类。"""
    btype = str(blk.get("type") or "")
    kind = str(blk.get("kind") or "")
    if btype == "interline_equation":
        # 行间公式的 LaTeX 可能在 latex 字段，也可能就是 text 里包着 $$ 的那串
        tex = str(blk.get("latex") or "").strip() or _unwrap_tex(str(blk.get("text") or ""))
        if tex:
            return [{"t": "ieq", "c": tex, "latex": tex}]
        if blk.get("asset"):
            return [{"t": "img", "c": blk["asset"]}]
        return split_marks(str(blk.get("text") or ""))
    if btype in {"image", "chart"} and kind != "inline_image":
        if blk.get("asset"):
            return [{"t": "img", "c": blk["asset"]}]
        return []
    rows = []
    for row in blk.get("lines") or []:
        for span in row or []:
            if isinstance(span, dict):
                rows.append(_copy_span(span))
    flat = _flatten_spans(rows)
    if flat:
        return flat
    return split_marks(str(blk.get("text") or ""))


def spans_from_members(members: list, blocks: list[dict]) -> list[dict]:
    """每条 span 带上来源块 ID（b）。注解改了源块，收口块才能照着重生成。"""
    out: list[dict] = []
    for src_i in members or []:
        if src_i is None:
            continue
        idx = int(src_i)
        if not (0 <= idx < len(blocks)):
            continue
        bid = str(blocks[idx].get("id") or "")
        for span in spans_from_block(blocks[idx]):
            item = dict(span)
            if bid:
                item["b"] = bid
            out.append(item)
    return out


def _valid_members(members: list, n: int) -> list[int]:
    out: list[int] = []
    for item in members or []:
        if item is None:
            continue
        idx = int(item)
        if 0 <= idx < n and idx not in out:
            out.append(idx)
    return out


def attach_close(assembled: dict, blocks: list[dict]) -> list[dict]:
    """按收口行追加 kb-close-* 块。行上带 eq/ieq/img/sub/sup/tab span。"""
    refs: list[dict] = []
    # 一个源块横跨几条带时，切带会给每条带各留一份碎片，碎片的字却都是整块的字。
    # 照着建块就会同一段话出两三遍，所以同源同字的行只认最上面那一行。
    minted: set[tuple] = set()
    for ui, unit in enumerate(assembled.get("units") or []):
        for li, line in enumerate(unit.get("lines") or []):
            pieces = list(line.get("pieces") or [])
            row: list[dict] = []
            source_ids: list[str] = []
            if len(pieces) >= 2:
                for i, piece in enumerate(pieces):
                    if i:
                        row.append({"t": "tab", "c": "\t"})
                    mems = _valid_members(piece.get("members") or [], len(blocks))
                    row.extend(spans_from_members(mems, blocks))
                    for idx in mems:
                        bid = str(blocks[idx].get("id") or "")
                        if bid and bid not in source_ids:
                            source_ids.append(bid)
            else:
                mems: list[int] = []
                for piece in pieces:
                    mems.extend(_valid_members(piece.get("members") or [], len(blocks)))
                row.extend(spans_from_members(mems, blocks))
                for idx in mems:
                    bid = str(blocks[idx].get("id") or "")
                    if bid and bid not in source_ids:
                        source_ids.append(bid)
            if not row:
                row = split_marks(str(line.get("text") or "")) or [{"t": "text", "c": ""}]
            text = str(line.get("text") or "")
            if source_ids:
                key = (tuple(source_ids), text)
                if key in minted:
                    continue
                minted.add(key)
            orders = [
                int(blocks[i].get("order") or 0)
                for i in _valid_members(
                    [m for piece in pieces for m in (piece.get("members") or [])], len(blocks)
                )
            ]
            blocks.append(
                {
                    "id": f"kb-close-{ui}-{li}",
                    "type": "title" if line.get("layer") in {"标题", "小标题"} else "text",
                    "kind": "body",
                    "order": min(orders) if orders else 0,
                    "text": text,
                    "lines": [row],
                    "bbox": list(line.get("bbox") or [0, 0, 1, 1]),
                    "close_type": unit.get("type"),
                    "close_layer": line.get("layer"),
                    "source_ids": source_ids,
                }
            )
            refs.append(
                {
                    "idx": len(blocks) - 1,
                    "bands": list(line.get("bands") or []),
                    "slots": list(line.get("slots") or []),
                    # 这一行吃掉了哪些源块。build_tree 靠它判断谁还没人渲染
                    "srcs": _valid_members(
                        [m for piece in pieces for m in (piece.get("members") or [])], len(blocks)
                    ),
                }
            )
    return refs


def _flow(members: list[int], bbox: list[float], slot: str, empty: bool, nid: str) -> dict:
    return {
        "id": nid,
        "kind": "flow",
        "empty": empty,
        "members": [] if empty else members,
        "slots": [slot or "main"],
        "x0": bbox[0],
        "y0": bbox[1],
        "x1": bbox[2],
        "y1": bbox[3],
    }


def _group(nid: str, kind: str, kids: list[dict], bbox: list[float]) -> dict:
    return {
        "id": nid,
        "kind": kind,
        "empty": False,
        "members": [i for kid in kids for i in _node_members(kid)],
        "children": kids,
        "x0": bbox[0],
        "y0": bbox[1],
        "x1": bbox[2],
        "y1": bbox[3],
    }


def _node_members(node: dict) -> list[int]:
    out = list(node.get("members") or [])
    for kid in node.get("children") or []:
        for idx in _node_members(kid):
            if idx not in out:
                out.append(idx)
    return out


def _set_caption(blocks: list[dict], members: list[int], caption: str) -> None:
    if not caption:
        return
    for idx in members:
        blk = blocks[idx]
        if blk.get("type") == "image" or blk.get("kind") == "figure":
            blk["caption"] = caption


def _add_text_block(blocks: list[dict], text: str, align: str) -> int:
    blocks.append(
        {
            "id": f"kb-algo-cap-{len(blocks)}",
            "type": "text",
            "kind": "body",
            "text": text,
            "align": align,
            "lines": [split_marks(text) or [{"t": "text", "c": text}]],
        }
    )
    return len(blocks) - 1


def _apply_part(blocks: list[dict], part: dict, members: list[int]) -> None:
    step = part.get("indent")
    for idx in members:
        blk = blocks[idx]
        blk.pop("align", None)
        blk.pop("indented", None)
        blk.pop("indent", None)
        if blk.get("type") == "title" and part.get("align"):
            blk["align"] = part["align"]
        if blk.get("type") in {"text", "list"} and isinstance(step, int) and step >= 1:
            blk["indented"] = True
            blk["indent"] = step * int(INDENT_PX)


def cell_node(cell: dict, blocks: list[dict], nid: str) -> dict:
    bbox = list(cell.get("bbox") or [0, 0, 1, 1])
    members = _valid_members(cell.get("members") or [], len(blocks))
    parts = list(cell.get("parts") or [])
    slot = str(cell.get("slot") or "main")
    if cell.get("blank") or (not members and not parts):
        return _flow([], bbox, slot, True, nid)
    if not parts:
        return _flow(members, bbox, slot, False, nid)
    kids: list[dict] = []
    for pi, part in enumerate(parts):
        role = str(part.get("role") or "")
        pbox = list(part.get("bbox") or bbox)
        if role == "blank":
            kids.append(_flow([], pbox, "main", True, f"{nid}b{pi}"))
            continue
        if role == "shared_caption":
            idx = _add_text_block(blocks, part.get("text") or "", part.get("align") or "center")
            kids.append(_flow([idx], pbox, "main", False, f"{nid}s{pi}"))
            continue
        if role == "panel_row":
            sub = []
            for ci, colp in enumerate(part.get("cols") or []):
                mems = _valid_members(colp.get("members") or [], len(blocks))
                _set_caption(blocks, mems, colp.get("caption") or "")
                sub.append(
                    _flow(mems, list(colp.get("bbox") or pbox), f"fig_{ci}", False, f"{nid}p{pi}_{ci}")
                )
            if not sub:
                continue
            kids.append(sub[0] if len(sub) == 1 else _group(f"{nid}p{pi}", "cols", sub, pbox))
            continue
        if role == "row" and len(part.get("cols") or []) >= 2:
            sub = []
            for ci, colp in enumerate(part["cols"]):
                mems = _valid_members(colp.get("members") or [], len(blocks))
                sub.append(
                    _flow(mems, list(colp.get("bbox") or pbox), "main", not mems, f"{nid}r{pi}_{ci}")
                )
            kids.append(_group(f"{nid}r{pi}", "cols", sub, pbox))
            continue
        mems = _valid_members(part.get("members") or [], len(blocks))
        _apply_part(blocks, part, mems)
        kids.append(_flow(mems, pbox, slot, not mems, f"{nid}f{pi}"))
    if not kids:
        return _flow(members, bbox, slot, False, nid)
    if len(kids) == 1:
        return kids[0]
    return _group(nid, "cols", [_group(nid + "s", "rows", kids, bbox)], bbox)


def _caption_srcs(assembled: dict) -> set[int]:
    """被吸成图题注的源块下标。它们的字随图走，不再单独成段。"""
    out: set[int] = set()
    for row in assembled.get("rows") or []:
        for cell in row.get("cells") or []:
            for part in cell.get("parts") or []:
                pools = [part.get("caption_srcs") or []]
                pools += [col.get("caption_srcs") or [] for col in part.get("cols") or []]
                for pool in pools:
                    for item in pool:
                        if item is not None:
                            out.add(int(item))
    return out


def _rescue_missing(
    rows: list[dict], blocks: list[dict], n_src: int, skip: set[int], frame: list[float]
) -> None:
    """兜底：谁都没认领的源块自己成一带，按原位插进去。宁可版式糙，也不许内容消失。"""
    placed: set[int] = set()
    for node in rows:
        placed.update(_node_members(node))
    missing = [
        i
        for i in range(min(n_src, len(blocks)))
        if i not in placed and i not in skip and _showable(blocks[i])
    ]
    for idx in _order_members(missing, blocks):
        bb = blocks[idx].get("bbox") or blocks[idx].get("_bb") or [frame[0], 0.0, frame[1], 0.0]
        box = [float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])]
        node = _flow([idx], box, "main", False, f"rx{idx}")
        pos = len(rows)
        for k, row in enumerate(rows):
            if float(row.get("y0") or 0.0) > box[1]:
                pos = k
                break
        rows.insert(pos, node)


def build_tree(
    assembled: dict, blocks: list[dict], line_refs: list[dict], n_src: int | None = None
) -> dict:
    """算法横带 → 现行 page-ir 认识的 layout.tree。字走收口大块，图仍走原格子。"""
    all_rows = list(assembled.get("rows") or [])
    fig_bands = {
        ri
        for ri, row in enumerate(all_rows)
        if any(
            (part.get("role") == "panel_row")
            for cell in (row.get("cells") or [])
            for part in (cell.get("parts") or [])
        )
    }
    # 图带走原格子，不引收口行；只有落在文字带上的收口行会真被画出来。
    # 被它们吃掉的源块一律不再单独渲染，否则同一段字出两遍。
    eaten = {
        i
        for ref in line_refs
        if any(b not in fig_bands for b in ref.get("bands") or [])
        for i in ref.get("srcs") or []
    }
    # 题注被吸进图里的源块也不再单独渲染
    skip = eaten | _caption_srcs(assembled)
    rows = []
    for ri, row in enumerate(all_rows):
        cells = list(row.get("cells") or [])
        bbox = list(row.get("bbox") or [0, 0, 1, 1])
        has_fig = ri in fig_bands
        here = [ref for ref in line_refs if ri in ref["bands"]]
        if has_fig:
            kids = [cell_node(cell, blocks, f"r{ri}c{ci}") for ci, cell in enumerate(cells)]
        elif any(len(ref["slots"]) > 1 for ref in here):
            ids = _order_members(
                [ref["idx"] for ref in here] + _spare(cells, skip, blocks), blocks
            )
            kids = [_flow(ids, bbox, "span", not ids, f"r{ri}u")]
        else:
            by_slot: dict[str, list[int]] = {}
            for ref in here:
                slot = (ref["slots"] or ["main"])[0]
                by_slot.setdefault(slot, []).append(ref["idx"])
            kids = []
            for ci, cell in enumerate(cells):
                slot = str(cell.get("slot") or "main")
                ids = _order_members(
                    (by_slot.get(slot) or []) + _spare([cell], skip, blocks), blocks
                )
                kids.append(_flow(ids, list(cell.get("bbox") or bbox), slot, not ids, f"r{ri}c{ci}"))
        node = kids[0] if len(kids) == 1 else _group(f"r{ri}", "cols", kids, bbox)
        rows.append(node)
    frame = [float(v) for v in (assembled.get("frame") or [0.0, 1.0])]
    _rescue_missing(rows, blocks, len(blocks) if n_src is None else n_src, skip, frame)
    tree = {
        "id": "page",
        "kind": "rows",
        "empty": False,
        "members": [],
        "children": rows,
        "x0": frame[0],
        "x1": frame[1],
    }
    _drop_repeats(tree, set(skip))
    tree["members"] = sorted(_node_members(tree))
    return tree


def _showable(blk: dict) -> bool:
    """这个块有东西可画吗。空文本块不值得单独占一行。"""
    if blk.get("asset") or blk.get("table_html"):
        return True
    if str(blk.get("text") or "").strip():
        return True
    return bool(blk.get("lines"))


def _spare(cells: list[dict], eaten: set[int], blocks: list[dict]) -> list[int]:
    """收口行没吃掉的成员（图、表、行内小图等）。不补回来就会整块消失。"""
    out: list[int] = []
    for cell in cells:
        for idx in _valid_members(cell.get("members") or [], len(blocks)):
            if idx in eaten or idx in out:
                continue
            if not _showable(blocks[idx]):
                continue
            out.append(idx)
    return out


def _order_members(members: list[int], blocks: list[dict]) -> list[int]:
    """按块在页上的位置排：先上后下，同高先左后右。"""
    def key(idx: int) -> tuple[float, float, int]:
        bb = blocks[idx].get("bbox") or blocks[idx].get("_bb") or [0.0, 0.0, 0.0, 0.0]
        return (round(float(bb[1]), 1), round(float(bb[0]), 1), idx)

    seen: list[int] = []
    for idx in members:
        if idx not in seen:
            seen.append(idx)
    return sorted(seen, key=key)


def _drop_repeats(node: dict, seen: set[int]) -> None:
    """一个块只画一次。图跨两带时，第二带里的那份剪掉。"""
    kids = node.get("children") or []
    if kids:
        for kid in kids:
            _drop_repeats(kid, seen)
        node["members"] = [i for kid in kids for i in _node_members(kid)]
        return
    keep = []
    for idx in node.get("members") or []:
        if idx in seen:
            continue
        seen.add(idx)
        keep.append(idx)
    node["members"] = keep
    if node.get("kind") == "flow":
        node["empty"] = not keep


def build_layout(
    assembled: dict,
    blocks: list[dict],
    *,
    folio: str = "",
    rules: dict[str, float] | None = None,
) -> dict:
    """收口 + 转树，返回写入 ir/pages 的 layout。

    这一段只读 INDENT_PX，但阈值同样从全局取，所以也要圈在 band_rules_applied 里，
    否则 assemble_page 还原之后这里就退回模块默认值，YAML 改不动缩进。
    """
    with band_rules_applied(rules):
        return _build_layout(assembled, blocks, folio=folio)


def _build_layout(assembled: dict, blocks: list[dict], *, folio: str = "") -> dict:
    n_src = len(blocks)  # 收口块从这个下标往后追加，兜底只找真源块
    refs = attach_close(assembled, blocks)
    tree = build_tree(assembled, blocks, refs, n_src=n_src)
    tracks = assembled.get("tracks") or {}
    return {
        "format": FORMAT,
        "folio": folio,
        "tracks": {
            "frame": list(tracks.get("frame") or []),
            "main": list(tracks.get("main") or []),
            "aside": list(tracks["aside"]) if tracks.get("aside") else None,
            "aside_side": tracks.get("aside_side"),
        },
        "em": assembled.get("em"),
        "frame": list(assembled.get("frame") or []),
        "tree": tree,
    }


def explain_page(assembled: dict) -> str:
    tracks = assembled.get("tracks") or {}
    lines = [
        f"format {assembled.get('format')}  page {assembled.get('page')}",
        f"frame {assembled.get('frame')}  em {assembled.get('em')}",
        f"main {tracks.get('main')}  aside {tracks.get('aside')}  side {tracks.get('aside_side')}",
        f"bands {len(assembled.get('rows') or [])}  units {len(assembled.get('units') or [])}",
    ]
    for i, row in enumerate(assembled.get("rows") or []):
        cells = row.get("cells") or []
        bits = []
        for cell in cells:
            slot = cell.get("slot")
            n = len([m for m in (cell.get("members") or []) if m is not None])
            bits.append(f"{slot}:{n}{' blank' if cell.get('blank') else ''}")
        lines.append(f"  band {i} {row.get('kind')}  " + " | ".join(bits))
    for i, unit in enumerate(assembled.get("units") or []):
        nline = len(unit.get("lines") or [])
        tabs = sum("\t" in str(ln.get("text") or "") for ln in unit.get("lines") or [])
        lines.append(f"  unit {i} {unit.get('type')}  lines={nline} tabs={tabs}")
    return "\n".join(lines) + "\n"


def _find_book(book_id: str) -> Path:
    """容器里是 /data/agent/textbooks/<书>，宿主机是 data/agent-home/textbooks/<书>。两边都要能跑。"""
    here = Path(__file__).resolve()
    candidates = [
        Path(os.environ["TEXTBOOKS_DIR"]) / book_id if os.environ.get("TEXTBOOKS_DIR") else None,
        Path.cwd() / "textbooks" / book_id,
        Path.cwd() / book_id,
    ]
    for parent in here.parents:
        candidates.append(parent / "textbooks" / book_id)
        candidates.append(parent / "data" / "agent-home" / "textbooks" / book_id)
    for path in candidates:
        if path is not None and (path / "mineru" / "layout.json").is_file():
            return path
    raise FileNotFoundError(f"book {book_id} not found")


def _mineru_page(book: Path, page_no: int) -> dict:
    layout = json.loads((book / "mineru" / "layout.json").read_text(encoding="utf-8"))
    for page in layout.get("pdf_info") or []:
        if int(page.get("page_idx") or -1) == page_no - 1:
            return page
    raise FileNotFoundError(f"{book.name} page {page_no}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    book_id = ""
    page_no = 0
    explain = False
    i = 0
    while i < len(args):
        if args[i] in {"--book", "-b"} and i + 1 < len(args):
            book_id = args[i + 1]
            i += 2
            continue
        if args[i] in {"--page", "-p"} and i + 1 < len(args):
            page_no = int(args[i + 1])
            i += 2
            continue
        if args[i] == "--explain":
            explain = True
            i += 1
            continue
        i += 1
    if not book_id or page_no < 1:
        print("usage: ir_bands.py --book <id> --page N [--explain]", file=sys.stderr)
        return 2
    page = _mineru_page(_find_book(book_id), page_no)
    assembled = assemble_page(page, book_id, page_no)
    if explain:
        sys.stdout.write(explain_page(assembled))
        return 0
    json.dump(assembled, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

