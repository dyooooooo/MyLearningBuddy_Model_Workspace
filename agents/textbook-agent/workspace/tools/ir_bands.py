"""页宽补空 + 盒子生长，给块写 slot。

零写盘、零书名判断。入参是一页 MinerU 块 + 页宽高 + 阈值。
先按页宽补空白、Y 重叠分栏，图横长、文字纵长套成大盒子。
内存里仍有轨和带（--explain 可打印）；页 JSON 不写 tree / tracks / em。
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
    "SIG_RATIO_TOL",
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
    "CAPTION_MAX_CHARS",
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
# 相邻小包并成一条带的最大垂直间隙（还要过指纹，单靠它不批准合并）
PAIR_GAP = 80.0
# 上下两行对应栏的宽度占比差（占页宽）不超过这个数，才算同一类可以往下长
SIG_RATIO_TOL = 0.10
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
# 题注最多这么多字。更长的按正文算，不许被图吃掉
CAPTION_MAX_CHARS = 20


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


# 「图 1-1-2」「表 3.1」这种编号开头的，铁定是题注
_CAPTION_HEAD = re.compile(r"^\s*(图|圖|表|Fig\.?|Figure|Table)\s*[0-9０-９一二三四五六七八九十]")
# 「(第2题)」「（图1）」这类整块括起来的短标记也是题注
_CAPTION_TAG = re.compile(r"^\s*[（(\[【][^）)\]】]{0,12}[）)\]】]\s*$")
# 句末标点。教材里半角句点、冒号一样常见，漏掉它们整句正文就会被当题注吃掉
_SENTENCE_END = re.compile(r"[。！？；：;:.．!?]\s*$")
# 「3. 求下列…」是题干，「A.」「B）」是选项，都不是题注
_ITEM_HEAD = re.compile(r"^\s*([0-9０-９]{1,2}|[A-DＡ-Ｄ])\s*[.．、)）]")


def _caption_text_ok(box: dict) -> bool:
    """这几个字像不像题注。

    以「图 1-2」开头或整块括起来的直接算题注，其余要同时满足：不是小节标题、
    不是题号选项、没写成一整句、也不长过题注字数上限。
    """
    text = plain_text(str(box.get("text") or "")).strip()
    if not text:
        return True
    if _CAPTION_HEAD.match(text) or _CAPTION_TAG.match(text):
        return True
    if str(box.get("type") or "") == "title":
        return False
    if _ITEM_HEAD.match(text):
        return False
    if _SENTENCE_END.search(text):
        return False
    return len(text) <= CAPTION_MAX_CHARS


def _caption_marked(box: dict) -> bool:
    """写着「图 1-2」「(第2题)」这种记号，认题注可以放宽几何。"""
    text = plain_text(str(box.get("text") or "")).strip()
    return bool(text) and bool(_CAPTION_HEAD.match(text) or _CAPTION_TAG.match(text))


def _caption_fits_figure(fig_box: list[float], root: dict) -> bool:
    """题注得压在图的横向范围里。左右两侧的正文只是蹭到图边，不算题注。

    没写题注记号的短句更严：整块都得在图宽以内。只要向左或向右探出图外，
    那就是版心里的一行正文碰巧挨着图，不能吃。
    """
    width = max(1.0, root["bbox"][2] - root["bbox"][0])
    if _overlap_x(root["bbox"], [fig_box[0], fig_box[2]]) < width * CONTAIN_FRAC:
        return False
    if _caption_marked(root):
        return True
    return root["bbox"][0] >= fig_box[0] - SNAP and root["bbox"][2] <= fig_box[2] + SNAP


def absorb_captions(groups: list[dict], roots: list[dict]) -> None:
    """矮的题注块并进图段。正文碰到图边不并：整句话、跨出图宽的块都留在原处。"""
    taken = {m["id"] for g in groups for m in g["members"]}
    for group in groups:
        for root in roots:
            if root["id"] in taken or root["type"] in {"image", "image_body", "image_caption"}:
                continue
            if root["bbox"][3] - root["bbox"][1] >= CAPTION_MAX_H:
                continue
            if _intersect(group["bbox"], root["bbox"]) is None:
                continue
            if not _caption_fits_figure(group["bbox"], root):
                continue
            if not _caption_text_ok(root):
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


def attach_slots(
    assembled: dict,
    blocks: list[dict],
    rules: dict[str, float] | None = None,
) -> list[dict]:
    """用内存栏轨给每块写 slot（main / aside / span）。只改 blocks，不写盘。"""
    with band_rules_applied(rules):
        return _attach_slots(assembled, blocks)


def _attach_slots(assembled: dict, blocks: list[dict]) -> list[dict]:
    tracks = assembled.get("tracks") if isinstance(assembled.get("tracks"), dict) else {}
    members: list[dict] = []
    for i, blk in enumerate(blocks):
        bb = blk.get("bbox")
        if not isinstance(bb, (list, tuple)) or len(bb) < 4:
            blk["slot"] = "main"
            continue
        members.append(
            {
                "bbox": [float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])],
                "_i": i,
            }
        )
    buckets = _assign_members(members, tracks)
    aside_is = {m["_i"] for m in buckets.get("aside") or []}
    frame = tracks.get("frame") or assembled.get("frame")
    frame_w = 0.0
    if isinstance(frame, (list, tuple)) and len(frame) >= 2:
        try:
            frame_w = float(frame[1]) - float(frame[0])
        except (TypeError, ValueError):
            frame_w = 0.0
    for mem in members:
        i = mem["_i"]
        if i in aside_is:
            blocks[i]["slot"] = "aside"
            continue
        bw = mem["bbox"][2] - mem["bbox"][0]
        if frame_w > 0 and bw >= frame_w * SPAN_FRAC:
            blocks[i]["slot"] = "span"
        else:
            blocks[i]["slot"] = "main"
    return blocks


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
        py0 = min(p["bbox"][1] for p in panel_cols)
        py1 = max(p["bbox"][3] for p in panel_cols)
        parts.append(
            {
                "role": "panel_row",
                "bbox": [
                    min(p["bbox"][0] for p in panel_cols),
                    py0,
                    max(p["bbox"][2] for p in panel_cols),
                    py1,
                ],
                "members": [p["members"][0] for p in panel_cols],
                "cols": _pad_panel_cols(panel_cols, py0, py1),
            }
        )
    slot = str(cell.get("slot") or "main")
    for cap in figure_inner.get("shared") or []:
        raw = list(cap["box"]["bbox"])
        # 题注跟着图走。图本来就允许探出格子，题注跟不上就别硬塞回格子——
        # 退回整格会让两条题注变成同一个满格框，叠在一起。
        clipped = raw
        if slot != "span" and _intersect(raw, cell["bbox"]) is not None:
            clipped = _clip(raw, cell["bbox"])
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
    copy["parts"] = _fill_blanks(parts, cell["bbox"]) if parts else parts
    return copy


def _pad_panel_cols(panel_cols: list[dict], y0: float, y1: float) -> list[dict]:
    """并排的图面板之间那道空当也是一列空白。规则一：一行里横着也不留洞。"""
    ordered = sorted(panel_cols, key=lambda p: float(p["bbox"][0]))
    out: list[dict] = []
    cursor: float | None = None
    for col in ordered:
        x0 = float(col["bbox"][0])
        if cursor is not None and x0 - cursor > SNAP:
            out.append({"bbox": [cursor, y0, x0, y1], "members": [], "blank": True})
        out.append(col)
        cursor = max(cursor or x0, float(col["bbox"][2]))
    return out


def _blank_part(cell_box: list[float], y0: float, y1: float) -> dict:
    return {"role": "blank", "bbox": [cell_box[0], y0, cell_box[2], y1], "members": []}


def _fill_blanks(parts: list[dict], cell_box: list[float]) -> list[dict]:
    """格子里没被内容占住的竖向空档一律补成空白段：规则一，铺满不留洞。

    先前的空白段全部作废，只按最终的内容段重算一遍，省得图在字下面时游标算歪。
    """
    solid = [p for p in parts if p.get("role") != "blank" and len(p.get("bbox") or []) >= 4]
    out = list(solid)
    cursor = float(cell_box[1])
    for part in sorted(solid, key=lambda p: float(p["bbox"][1])):
        top = float(part["bbox"][1])
        if top - cursor > BLANK_MIN:
            out.append(_blank_part(cell_box, cursor, top))
        cursor = max(cursor, float(part["bbox"][3]))
    if float(cell_box[3]) - cursor > BLANK_MIN:
        out.append(_blank_part(cell_box, cursor, float(cell_box[3])))
    return sorted(out, key=lambda p: (float(p["bbox"][1]), float(p["bbox"][0])))



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
    for row in sub_rows(members):
        ry0 = min(m["bbox"][1] for m in row)
        ry1 = max(m["bbox"][3] for m in row)
        if all(m["type"] == "title" for m in row):
            parts.append(_title_part(row, box, slot, boxes, em))
        elif len(row) >= 2:
            parts.append(_row_part(row, box, ry0, ry1))
        else:
            parts.append(_single_part(row[0], box, slot, indents, boxes, em))
    if parts:
        copy["parts"] = _fill_blanks(parts, box)
    return copy


def _as_inners(figure_inner) -> list[dict]:
    """一个格子里可以有好几组图。单组、多组、空都归成列表，按上下排。"""
    if not figure_inner:
        return []
    items = figure_inner if isinstance(figure_inner, list) else [figure_inner]
    out = [g for g in items if g and (g.get("panels") or g.get("shared"))]
    return sorted(out, key=lambda g: _inner_y0(g))


def _inner_y0(inner: dict) -> float:
    ys = [float(p["body"]["bbox"][1]) for p in (inner.get("panels") or []) if p.get("body")]
    ys += [float(c["box"]["bbox"][1]) for c in (inner.get("shared") or []) if c.get("box")]
    return min(ys) if ys else 0.0


def _figure_src_ids(figure_inner) -> set:
    out: set = set()
    for inner in _as_inners(figure_inner):
        out |= _one_figure_src_ids(inner)
    return out


def _one_figure_src_ids(figure_inner: dict | None) -> set:
    out: set = set()
    if not figure_inner:
        return out
    for panel in figure_inner.get("panels") or []:
        if panel.get("src_i") is not None:
            out.add(panel["src_i"])
        for cap in panel.get("captions") or []:
            box = cap.get("box") or {}
            if box.get("src_i") is not None:
                out.add(box["src_i"])
    for cap in figure_inner.get("shared") or []:
        box = cap.get("box") or {}
        if box.get("src_i") is not None:
            out.add(box["src_i"])
    return out


def nest_body(cell: dict, members: list[dict], boxes: list[dict], em: float) -> dict:
    """正文格：按阅读序和类型出段。坐标只换成缩进档，不再用来挪位置。

    竖向压在一起的几块仍按规则二分成并排的列，不叠在同一个框里；
    为铺满版面撑出来的空白不丢：格子比字高时，上下和段间的空档照原高度留成空白段，
    跟字一起装在这个大盒子里。
    """
    copy = dict(cell)
    box = cell["bbox"]
    slot = str(cell.get("slot") or "main")
    indents = column_indent(members, boxes, em)
    parts: list[dict] = []
    y_cursor = float(box[1])
    for row in sub_rows(members):
        ry0 = min(float(m["bbox"][1]) for m in row)
        ry1 = max(float(m["bbox"][3]) for m in row)
        if len(row) >= 2:
            parts.append(_row_part(row, box, ry0, ry1))
        else:
            piece = _single_part(row[0], box, slot, indents, boxes, em)
            if row[0]["type"] == "title":
                piece["align"] = "left"
            parts.append(piece)
    if parts:
        copy["parts"] = _fill_blanks(parts, box)
    return copy


def nest_side(
    cell: dict,
    members: list[dict],
    boxes: list[dict],
    figure_inner: dict | None,
    em: float,
) -> dict:
    """侧栏：块留在原位，不做标题居中、缩进分档。图和字都留着。

    撑出来的空白同样不丢，按原高度留在格子里。
    """
    copy = dict(cell)
    box = cell["bbox"]
    parts: list[dict] = []
    taken = _figure_src_ids(figure_inner)
    for inner in _as_inners(figure_inner):
        if inner.get("panels"):
            parts.extend(nest_figure(dict(cell), inner).get("parts") or [])
    rest = [m for m in members if m.get("src_i") not in taken]
    for mem in sorted(rest, key=lambda m: (m["bbox"][1], m["bbox"][0])):
        parts.append(
            {
                "role": mem["type"],
                "bbox": list(mem["bbox"]),
                "members": [mem.get("src_i")],
            }
        )
    if parts:
        copy["parts"] = _fill_blanks(parts, box)
    return copy


def nest_cell(
    cell: dict,
    members: list[dict],
    boxes: list[dict] | None = None,
    figure_inner: dict | None = None,
    em: float = 12.0,
) -> dict:
    """格内再套。图不再吃掉同格的字。"""
    if cell.get("blank") or not members:
        return cell
    boxes = boxes or []
    slot = str(cell.get("slot") or "main")
    if slot == "aside":
        return nest_side(cell, members, boxes, figure_inner, em)
    inners = [g for g in _as_inners(figure_inner) if g.get("panels")]
    taken = _figure_src_ids(inners)
    # 只挡图组自己的成员。同格里属于别组的图也要出段，按类型一刀切会让它们整块消失
    leftover = [m for m in members if m.get("src_i") not in taken]
    if inners and not leftover and len(inners) == 1:
        return nest_figure(cell, inners[0])
    if inners:
        fig_parts: list[dict] = []
        for inner in inners:
            fig_parts.extend(nest_figure(dict(cell), inner).get("parts") or [])
        text_parts = nest_body(cell, leftover, boxes, em).get("parts") or [] if leftover else []
        copy = dict(cell)
        copy["parts"] = _fill_blanks(fig_parts + text_parts, cell["bbox"])
        return copy
    if slot == "main":
        return nest_body(cell, members, boxes, em)
    return nest_flow(cell, members, boxes, em)


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


def plain_text(text: str) -> str:
    """扫描稿里的制表符会被当成分栏符。抽字时换成空格。"""
    return (text or "").replace("\t", " ")


def _annotate_text(boxes: list[dict]) -> None:
    """根块自己不带字，字挂在 span 上。判题注要看字，先把字收到根块上。"""
    for box in boxes:
        if box.get("kind") == "block" and box.get("parent") is None and not box.get("text"):
            box["text"] = _span_text(box, boxes)


def _figures_hit(groups: list[dict], member_ids: set[int]) -> list[dict]:
    """格子里落到的每一组图都要，按上下排。只取最大那组会让别的图整块消失。"""
    out: list[dict] = []
    for group in groups:
        ids = {m["id"] for m in group["members"] if m.get("parent") is None}
        if not (ids & member_ids):
            continue
        inner = group.get("inner")
        if inner:
            out.append(inner)
    return sorted(out, key=_inner_y0)



# ------------------------------------------------------------------ 盒子生长（页宽补空、Y 重叠分栏、图横长、文字纵长）


def _y_hits(a: list[float], b: list[float]) -> bool:
    return min(a[3], b[3]) - max(a[1], b[1]) > SNAP


def _is_fig_root(box: dict) -> bool:
    return (
        box.get("kind") == "block"
        and box.get("parent") is None
        and box["type"] in {"image", "image_body", "chart"}
        and not _is_icon(box)
    )


def _is_caption_like(box: dict) -> bool:
    if box.get("type") == "image_caption":
        return True
    if box.get("type") not in {"text", "title"}:
        return False
    if (box["bbox"][3] - box["bbox"][1]) >= CAPTION_MAX_H:
        return False
    return _caption_text_ok(box)


def _connected(items: list[dict], linked) -> list[list[dict]]:
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i, a in enumerate(items):
        for j in range(i + 1, len(items)):
            if linked(a, items[j]):
                union(i, j)
    groups: dict[int, list[dict]] = {}
    for i, item in enumerate(items):
        groups.setdefault(find(i), []).append(item)
    return list(groups.values())


def _same_column(a: dict, b: dict) -> bool:
    """同一栏：左缘差不超过栏缝，或窄的那块大部分落在宽的里面。"""
    aa, bb = a["bbox"], b["bbox"]
    if abs(float(aa[0]) - float(bb[0])) <= COL_GAP:
        return True
    wa, wb = max(1.0, aa[2] - aa[0]), max(1.0, bb[2] - bb[0])
    return _overlap_x(aa, [bb[0], bb[2]]) >= min(wa, wb) * TRACK_IN_FRAC


def _vgap(a: dict, b: dict) -> float:
    aa, bb = a["bbox"], b["bbox"]
    return max(0.0, max(aa[1], bb[1]) - min(aa[3], bb[3]))


def _cluster_columns(members: list[dict], page_w: float) -> list[dict]:
    """Y 重叠的块按 X 分成 N 栏。栏宽取该栏最宽块，不把栏缝两侧并成一栏。"""
    if not members:
        return []
    groups = _connected(members, _same_column)
    cols: list[dict] = []
    wide_cut = max(1.0, page_w * WIDE_PACK_FRAC)
    for group in groups:
        widest = max(group, key=lambda m: m["bbox"][2] - m["bbox"][0])
        x0, x1 = widest["bbox"][0], widest["bbox"][2]
        if (x1 - x0) >= wide_cut:
            x0, x1 = min(m["bbox"][0] for m in group), max(m["bbox"][2] for m in group)
        cols.append(
            {
                "members": list(group),
                "x0": float(x0),
                "x1": float(x1),
                "y0": min(m["bbox"][1] for m in group),
                "y1": max(m["bbox"][3] for m in group),
            }
        )
    cols.sort(key=lambda c: c["x0"])
    return _clip_keep_x0(_absorb_skinny(cols))


def _clip_keep_x0(cols: list[dict]) -> list[dict]:
    """两栏叠在一起时只收左边的右缘，不挪任何一栏的左缘。"""
    cols = [dict(c, members=list(c.get("members") or [])) for c in cols]
    cols.sort(key=lambda c: c["x0"])
    out: list[dict] = []
    for col in cols:
        if out and float(col["x0"]) < float(out[-1]["x1"]):
            prev = out[-1]
            if float(col["x0"]) - float(prev["x0"]) >= SNAP:
                prev["x1"] = float(col["x0"])
            else:
                col["members"] = list(prev.get("members") or []) + list(col.get("members") or [])
                col["x0"] = min(float(prev["x0"]), float(col["x0"]))
                col["x1"] = max(float(prev["x1"]), float(col["x1"]))
                col["y0"] = min(float(prev["y0"]), float(col["y0"]))
                col["y1"] = max(float(prev["y1"]), float(col["y1"]))
                col["blank"] = not col["members"]
                if prev.get("figure_inner") and not col.get("figure_inner"):
                    col["figure_inner"] = prev.get("figure_inner")
                out[-1] = col
                continue
        out.append(col)
    return out


def _absorb_skinny(cols: list[dict]) -> list[dict]:
    """窄图标自己占一栏时并进旁边那栏。"""
    cols = [dict(c, members=list(c["members"])) for c in cols]
    changed = True
    while changed and len(cols) > 1:
        changed = False
        cols.sort(key=lambda c: c["x0"])
        for i, col in enumerate(cols):
            if col.get("blank") or not col.get("members"):
                continue
            if col["x1"] - col["x0"] >= ICON_MAX:
                continue
            prev = cols[i - 1] if i and cols[i - 1].get("members") else None
            nxt = (
                cols[i + 1]
                if i + 1 < len(cols) and cols[i + 1].get("members")
                else None
            )

            def dist(other: dict) -> float:
                if other["x1"] <= col["x0"]:
                    return col["x0"] - other["x1"]
                if col["x1"] <= other["x0"]:
                    return other["x0"] - col["x1"]
                return 0.0

            cand = [(dist(prev), i - 1)] if prev else []
            if nxt:
                cand.append((dist(nxt), i + 1))
            if not cand or min(cand)[0] > COL_GAP:
                continue
            j = min(cand)[1]
            neigh = cols[j]
            neigh["members"].extend(col["members"])
            neigh["x0"] = min(float(m["bbox"][0]) for m in neigh["members"])
            neigh["x1"] = max(float(m["bbox"][2]) for m in neigh["members"])
            neigh["y0"] = min(neigh["y0"], col["y0"])
            neigh["y1"] = max(neigh["y1"], col["y1"])
            cols.pop(i)
            changed = True
            break
    return cols


def _min_blank(page_w: float) -> float:
    """栏缝小于这个数不当独立空白格：COL_GAP 是下限，CONTAIN_FRAC 再抬一点。"""
    return max(SNAP, COL_GAP, page_w * (1.0 - CONTAIN_FRAC) * 0.05)


def _retile_content(cols: list[dict]) -> list[dict]:
    """有字的栏按 X 排好。套在里面的并进宽栏，真重叠才从中线切开。"""
    content = [dict(c, members=list(c.get("members") or [])) for c in _content_cols(cols)]
    content.sort(key=lambda c: (c["x0"], -_col_width(c)))
    tiled: list[dict] = []
    for col in content:
        if not tiled:
            tiled.append(col)
            continue
        prev = tiled[-1]
        if col["x0"] >= prev["x0"] - SNAP and col["x1"] <= prev["x1"] + SNAP:
            prev["members"].extend(col["members"])
            continue
        if prev["x0"] >= col["x0"] - SNAP and prev["x1"] <= col["x1"] + SNAP:
            col["members"] = list(prev.get("members") or []) + list(col.get("members") or [])
            tiled[-1] = col
            continue
        if col["x0"] < prev["x1"]:
            if float(col["x0"]) - float(prev["x0"]) >= SNAP:
                prev["x1"] = float(col["x0"])
            elif _col_width(prev) >= _col_width(col):
                prev["members"].extend(col["members"])
                prev["x1"] = max(prev["x1"], col["x1"])
                continue
            else:
                col["members"] = list(prev.get("members") or []) + list(col.get("members") or [])
                col["x0"] = min(prev["x0"], col["x0"])
                tiled[-1] = col
                continue
        tiled.append(col)
    return _clip_keep_x0(tiled)


def _append_h_blank(
    out: list[dict], x0: float, x1: float, y0: float, y1: float
) -> None:
    """横缝只进空白栏。窄缝并进已有空白，绝不把字栏拉宽。"""
    if x1 - x0 <= SNAP:
        return
    if out and out[-1].get("blank"):
        out[-1]["x1"] = x1
        return
    out.append(
        {
            "members": [],
            "x0": x0,
            "x1": x1,
            "y0": y0,
            "y1": y1,
            "blank": True,
        }
    )


def _pad_to_page(cols: list[dict], canvas: list[float], y0: float, y1: float) -> list[dict]:
    """没占满页宽的缝生成一等空白格，立刻和原栏封在同一行大块里。"""
    out: list[dict] = []
    cursor = float(canvas[0])
    for col in _retile_content(cols):
        x0, x1 = float(col["x0"]), float(col["x1"])
        if x0 > cursor:
            _append_h_blank(out, cursor, x0, y0, y1)
        item = dict(col)
        item["x0"] = x0
        item["y0"] = y0
        item["y1"] = y1
        item["blank"] = False
        out.append(item)
        cursor = max(cursor, float(item["x1"]))
    if canvas[1] > cursor:
        _append_h_blank(out, cursor, float(canvas[1]), y0, y1)
    return out


def _figure_groups(figures: list[dict], live: list[dict], roots: list[dict]) -> list[dict]:
    groups = union_overlapping(figures, live)
    absorb_captions(groups, roots)
    for group in groups:
        group["bbox"] = clip_to_siblings(
            group, [r for r in roots if r["id"] not in {m["id"] for m in group["members"]}]
        )
        group["inner"] = layout_figure(group, live)
        for panel in group["inner"]["panels"]:
            for cap in panel["captions"]:
                cap["text"] = _span_text(cap["box"], live) or cap.get("text") or ""
        for cap in group["inner"]["shared"]:
            cap["text"] = _span_text(cap["box"], live) or cap.get("text") or ""
    return groups


def _text_clusters(blocks: list[dict]) -> list[list[dict]]:
    """同一栏里上下两块：空隙小于 PAIR_GAP 就连成一段，否则拆开。"""

    def linked(a: dict, b: dict) -> bool:
        if _y_hits(a["bbox"], b["bbox"]):
            return True
        gap = max(0.0, max(a["bbox"][1], b["bbox"][1]) - min(a["bbox"][3], b["bbox"][3]))
        return gap <= PAIR_GAP

    return _connected(blocks, linked)


def _has_fig(col: dict) -> bool:
    return any(
        m.get("type") in {"image", "image_body", "chart"}
        for m in (col.get("members") or [])
    )


def _content_cols(cols: list[dict]) -> list[dict]:
    return [c for c in cols if c.get("members")]


def _col_width(col: dict) -> float:
    return max(0.0, float(col["x1"]) - float(col["x0"]))


def _is_blank_col(col: dict) -> bool:
    return bool(col.get("blank")) or not (col.get("members") or [])


def _band_signature(cols: list[dict], page_w: float) -> list[tuple[bool, float]]:
    """从左到右：是不是空白栏，以及占页宽的比例。"""
    width = max(1.0, page_w)
    return [(_is_blank_col(col), _col_width(col) / width) for col in cols]


def _same_band_sig(upper: list[dict], lower: list[dict], page_w: float) -> bool:
    """栏数、角色相同，且每一栏宽度占比差不超过 SIG_RATIO_TOL，才能往下长。"""
    top = _band_signature(upper, page_w)
    bot = _band_signature(lower, page_w)
    if len(top) != len(bot):
        return False
    for (blank_a, frac_a), (blank_b, frac_b), up, lo in zip(top, bot, upper, lower):
        if blank_a != blank_b:
            return False
        if abs(frac_a - frac_b) > SIG_RATIO_TOL:
            return False
        if blank_a:
            continue
        wa, wb = max(1.0, _col_width(up)), max(1.0, _col_width(lo))
        if _overlap_x(
            [up["x0"], 0.0, up["x1"], 1.0], [lo["x0"], lo["x1"]]
        ) < min(wa, wb) * CONTAIN_FRAC:
            return False
    return True


def _merge_col_pair(upper: dict, lower: dict, y0: float, y1: float) -> dict:
    """上下两格按栏位对齐：盒子沿用栏的左右缘，字的坐标不动。"""
    members = list(upper.get("members") or []) + list(lower.get("members") or [])
    return {
        "members": members,
        "x0": min(float(upper["x0"]), float(lower["x0"])),
        "x1": max(float(upper["x1"]), float(lower["x1"])),
        "y0": y0,
        "y1": y1,
        "blank": not members,
        "figure_inner": upper.get("figure_inner") or lower.get("figure_inner"),
    }


def _clip_blank_overlaps(cols: list[dict]) -> list[dict]:
    """合并后字栏变宽时，只把贴着的空白栏让开，不把字栏再拉宽。"""
    out = [dict(c, members=list(c.get("members") or [])) for c in cols]
    for i, col in enumerate(out):
        if i + 1 >= len(out):
            break
        nxt = out[i + 1]
        if float(col["x1"]) <= float(nxt["x0"]) + SNAP:
            continue
        if col.get("blank") and not nxt.get("blank"):
            col["x1"] = float(nxt["x0"])
        elif nxt.get("blank") and not col.get("blank"):
            nxt["x0"] = float(col["x1"])
    return [c for c in out if _col_width(c) > SNAP or c.get("members")]


def _merge_aligned_bands(
    upper: list[dict], lower: list[dict], y0: float, y1: float
) -> list[dict]:
    """指纹已对齐的两行：字对字、空对空，空白留在栏里，不再丢掉重铺。"""
    return _clip_blank_overlaps(
        [_merge_col_pair(up, lo, y0, y1) for up, lo in zip(upper, lower)]
    )


def _atomic_y_groups(blocks: list[dict]) -> list[list[dict]]:
    groups = _connected(blocks, lambda a, b: _y_hits(a["bbox"], b["bbox"]))
    groups.sort(key=lambda g: min(m["bbox"][1] for m in g))
    return groups


def _x_hit(a: list[float], b: list[float]) -> float:
    return _overlap_x(a, [b[0], b[2]] if len(b) > 2 else b)


def _grow_single_h(
    cols: list[dict], canvas: list[float], body: list[float], y0: float, y1: float
) -> list[dict]:
    """这一段里只有一块字时，这块横向长满版心（不是整张纸）。多块只纵长。

    长到 canvas 的话字会顶到纸边，跟别的页对不齐；剩下的页边由 _pad_to_page 补空白格。
    """
    content = _content_cols(cols)
    n = sum(len(c.get("members") or []) for c in content)
    if n != 1 or len(content) != 1 or _has_fig(content[0]):
        return cols
    only = dict(content[0])
    only["x0"] = min(float(body[0]), float(only["x0"]))
    only["x1"] = max(float(body[1]), float(only["x1"]))
    only["y0"] = y0
    only["y1"] = y1
    only["blank"] = False
    return _pad_to_page([only], canvas, y0, y1)


def _content_x(roots: list[dict], canvas: list[float]) -> list[float]:
    """版心横向范围：这一页有字有图的最左最右。没有内容就退回整页。"""
    live = [r for r in roots if r.get("kind") == "block" and r.get("parent") is None]
    if not live:
        return [float(canvas[0]), float(canvas[1])]
    return [
        max(float(canvas[0]), min(float(r["bbox"][0]) for r in live)),
        min(float(canvas[1]), max(float(r["bbox"][2]) for r in live)),
    ]


def _grow_text_region(
    blocks: list[dict],
    y0: float,
    y1: float,
    canvas: list[float],
    body: list[float],
    page_w: float,
) -> list[dict]:
    """Y 重叠先分成一行多栏并补空白，封成大块后再按指纹纵向并到图段为止。"""
    groups = _atomic_y_groups(blocks)
    if not groups:
        return []
    page_span = max(1.0, float(canvas[1]) - float(canvas[0]))
    slices: list[dict] = []
    for group in groups:
        cols = _cluster_columns(group, page_w)
        top = min(m["bbox"][1] for m in group)
        bot = max(m["bbox"][3] for m in group)
        slices.append({"y0": top, "y1": bot, "cols": _pad_to_page(cols, canvas, top, bot)})
    grown = [slices[0]]
    for sl in slices[1:]:
        prev = grown[-1]
        if sl["y0"] - prev["y1"] <= PAIR_GAP and _same_band_sig(
            prev["cols"], sl["cols"], page_span
        ):
            prev["cols"] = _merge_aligned_bands(
                prev["cols"], sl["cols"], prev["y0"], sl["y1"]
            )
            prev["y1"] = sl["y1"]
        else:
            if sl["y0"] - prev["y1"] > PAIR_GAP:
                grown.append(
                    {
                        "y0": prev["y1"],
                        "y1": sl["y0"],
                        "cols": _pad_to_page([], canvas, prev["y1"], sl["y0"]),
                        "gap_blank": True,
                    }
                )
            grown.append(sl)
    if grown[0]["y0"] >= y0 - SNAP and grown[0]["y0"] - y0 <= PAIR_GAP:
        grown[0]["y0"] = y0
        for col in grown[0]["cols"]:
            col["y0"] = y0
    if grown[-1]["y1"] <= y1 + SNAP and y1 - grown[-1]["y1"] <= PAIR_GAP:
        grown[-1]["y1"] = y1
        for col in grown[-1]["cols"]:
            col["y1"] = y1
    for sl in grown:
        sl["cols"] = _grow_single_h(sl.get("cols") or [], canvas, body, sl["y0"], sl["y1"])
    return grown


def _figure_grow_h(
    pack: list[dict],
    caps: list[dict],
    interior: list[dict],
    live: list[dict],
    roots: list[dict],
    canvas: list[float],
    y0: float,
    y1: float,
) -> tuple[list[dict], dict]:
    """图段只横向长：贴边的余量并进图，更宽的余量变成空白格。"""
    group = _layout_pack(pack, caps + interior, live, roots)
    fx0, fx1 = float(group["bbox"][0]), float(group["bbox"][2])
    if fx0 - canvas[0] <= FIG_NEAR_X:
        fx0 = float(canvas[0])
    if canvas[1] - fx1 <= FIG_NEAR_X:
        fx1 = float(canvas[1])
    fig_col = {
        "members": list(group["members"]),
        "x0": fx0,
        "x1": fx1,
        "y0": y0,
        "y1": y1,
        "blank": False,
        "figure_inner": group.get("inner"),
    }
    return _pad_to_page([fig_col], canvas, y0, y1), group


def _layout_pack(pack: list[dict], caps: list[dict], live: list[dict], roots: list[dict]) -> dict:
    members = [m for m in pack if m.get("parent") is None] + list(caps)
    group = {"members": members, "bbox": _pack_box(members) if members else [0.0, 0.0, 1.0, 1.0]}
    skip = {m["id"] for m in members}
    group["bbox"] = clip_to_siblings(group, [r for r in roots if r["id"] not in skip])
    inner = layout_figure(group, live)
    for panel in inner["panels"]:
        for cap in panel["captions"]:
            cap["text"] = _span_text(cap["box"], live) or cap.get("text") or ""
    for cap in inner["shared"]:
        cap["text"] = _span_text(cap["box"], live) or cap.get("text") or ""
    group["inner"] = inner
    return group


def _pull_captions(union: list[float], live_text: list[dict], consumed: set[int]) -> list[dict]:
    """图段上下 PAIR_GAP 以内、同一栏、压在图宽里的矮说明跟图走。"""
    out: list[dict] = []
    for item in live_text:
        if item["id"] in consumed or not _is_caption_like(item):
            continue
        if not _caption_fits_figure(union, item):
            continue
        gap = max(0.0, item["bbox"][1] - union[3], union[1] - item["bbox"][3])
        if gap <= PAIR_GAP and _same_column({"bbox": union}, item):
            out.append(item)
    return out


def _is_figure_barrier(pack: list[dict], live_text: list[dict], page_w: float) -> bool:
    """旁边没有并排正文的图，或够宽的图，横切成图段。"""
    union = _pack_box(pack)
    fig_w = max(1.0, union[2] - union[0])
    wfrac = fig_w / max(1.0, page_w)
    if wfrac >= SPAN_FRAC or fig_w >= page_w * TRACK_CROSS_FRAC:
        return True
    side_text = [
        t
        for t in live_text
        if not _is_caption_like(t)
        and _y_hits(union, t["bbox"])
        and not _same_column({"bbox": union}, t)
    ]
    return not side_text


def _occupied_slices(blocks: list[dict], y0: float, y1: float, join_gap: float) -> list[tuple[float, float]]:
    """同一段文字里，空隙大于 join_gap 才切开；空档小于它的并进大盒子。"""
    spans: list[tuple[float, float]] = []
    for item in blocks:
        top, bot = max(y0, item["bbox"][1]), min(y1, item["bbox"][3])
        if bot > top:
            spans.append((top, bot))
    if not spans:
        return []
    spans.sort()
    out = [spans[0]]
    for top, bot in spans[1:]:
        if top <= out[-1][1] + join_gap:
            out[-1] = (out[-1][0], max(out[-1][1], bot))
        else:
            out.append((top, bot))
    if out[0][0] - y0 <= join_gap:
        out[0] = (y0, out[0][1])
    if y1 - out[-1][1] <= join_gap:
        out[-1] = (out[-1][0], y1)
    return out


def _stitch_rows(rows: list[dict], canvas: list[float]) -> list[dict]:
    if not rows:
        return rows
    rows = [r for r in rows if r["bbox"][3] - r["bbox"][1] >= SNAP]
    rows = sorted(rows, key=lambda r: r["bbox"][1])
    for i, row in enumerate(rows):
        row["bbox"][0] = canvas[0]
        row["bbox"][2] = canvas[1]
        if i + 1 < len(rows):
            nxt = float(rows[i + 1]["bbox"][1])
            if nxt >= row["bbox"][1] + SNAP:
                row["bbox"][3] = nxt
                for cell in row.get("cells") or []:
                    cell["bbox"][3] = nxt
    return rows


def _body_x_after_boxes(raw_rows: list[dict], canvas: list[float]) -> list[float]:
    """盒子套好之后才认正文：墨最多、够宽、又不是通栏的那一列文字。"""
    page_w = max(1.0, float(canvas[1]) - float(canvas[0]))
    wide_cut = page_w * SPAN_FRAC
    cands: list[dict] = []
    for raw in raw_rows:
        for col in raw.get("cols") or []:
            if col.get("blank") or not col.get("members"):
                continue
            if _has_fig(col) and not any(
                m.get("type") not in {"image", "image_body", "chart"}
                for m in col["members"]
            ):
                continue
            cands.append(col)
    if not cands:
        return [float(canvas[0]), float(canvas[1])]
    narrow = [c for c in cands if _col_width(c) < wide_cut]
    pool = narrow or cands
    pick = max(pool, key=lambda c: (_ink(c.get("members") or []), _col_width(c)))
    return [float(pick["x0"]), float(pick["x1"])]


def _format_row(
    raw: dict,
    canvas: list[float],
    live: list[dict],
    groups: list[dict],
    em: float,
    body_x: list[float],
) -> dict:
    """格子已经铺满页宽之后，才标正文/侧栏并做格内格式化。"""
    y0, y1 = float(raw["y0"]), float(raw["y1"])
    page_w = max(1.0, float(canvas[1]) - float(canvas[0]))
    cells: list[dict] = []
    for col in raw.get("cols") or []:
        members = list(col.get("members") or [])
        box = [float(col["x0"]), y0, float(col["x1"]), y1]
        blank = bool(col.get("blank") or not members)
        has_fig = _has_fig(col)
        leftover_text = any(
            m.get("type") not in {"image", "image_body", "chart"}
            for m in members
        )
        if blank:
            cells.append(_cell("blank", box[0], box[2], y0, y1, []))
            continue
        width = max(1.0, box[2] - box[0])
        if has_fig and not leftover_text and width >= page_w * SPAN_FRAC:
            slot = "span"
        elif body_x is None:
            slot = "span"
        elif _overlap_x(box, body_x) >= width * 0.5:
            slot = "main"
        else:
            slot = "aside"
        cell = _cell(slot, box[0], box[2], y0, y1, members)
        use_fig = col.get("figure_inner")
        if use_fig is None:
            use_fig = _figures_hit(groups, {m["id"] for m in members})
        if not has_fig:
            use_fig = None
        cells.append(nest_cell(cell, members, live, use_fig, em))
    content_n = len([c for c in cells if not c.get("blank")])
    kind = "span" if content_n <= 1 else "pair"
    if kind == "span":
        for cell in cells:
            if not cell.get("blank"):
                cell["slot"] = "span"
    return {
        "kind": kind,
        "bbox": [canvas[0], y0, canvas[1], y1],
        "cols": [c["bbox"][0:3:2] for c in cells],
        "cells": cells,
    }


def grow_layout(
    roots: list[dict],
    live: list[dict],
    canvas: list[float],
    page_w: float,
    em: float,
) -> tuple[list[dict], dict]:
    """整页生成嵌套盒子。先铺满、再分栏、再生长，最后才格式化。零写盘。"""
    figs = [r for r in roots if _is_fig_root(r)]
    texts = [
        r
        for r in roots
        if r.get("kind") == "block" and r.get("parent") is None and not _is_fig_root(r)
    ]
    groups = _figure_groups(figs, live, roots)
    taken_caps = {m["id"] for g in groups for m in g["members"] if _is_caption_like(m)}
    live_text = [t for t in texts if t["id"] not in taken_caps]

    fig_cc = _connected(
        figs,
        lambda a, b: _y_hits(a["bbox"], b["bbox"]) or _vgap(a, b) <= TIGHT_PACK,
    )
    barriers: list[tuple[float, float, list[dict], list[dict]]] = []
    mixed_figs: list[dict] = []
    consumed: set[int] = set(taken_caps)
    for pack in fig_cc:
        union = _pack_box(pack)
        if _is_figure_barrier(pack, live_text, page_w):
            caps = _pull_captions(union, live_text, consumed)
            barriers.append((union[1], union[3], pack, caps))
            consumed.update(m["id"] for m in pack)
            consumed.update(c["id"] for c in caps)
        else:
            mixed_figs.extend(pack)
    barriers.sort(key=lambda item: item[0])

    movable = live_text + mixed_figs
    raw_rows: list[dict] = []
    walk_y = min((r["bbox"][1] for r in roots), default=0.0)
    page_bottom = max((r["bbox"][3] for r in roots), default=0.0)

    content_x = _content_x(roots, canvas)

    def emit_text(y0: float, y1: float, blocks: list[dict]) -> None:
        here = [b for b in blocks if b["id"] not in consumed]
        if not here:
            return
        for sl in _grow_text_region(here, y0, y1, canvas, content_x, page_w):
            if sl["y1"] - sl["y0"] < SNAP:
                continue
            raw_rows.append(sl)
            consumed.update(
                m["id"]
                for c in sl.get("cols") or []
                for m in c.get("members") or []
            )

    stops = barriers + [(page_bottom + 1.0, page_bottom + 1.0, [], [])]
    for y0, y1, pack, caps in stops:
        if y0 > walk_y + SNAP:
            in_gap = [
                b
                for b in movable
                if b["id"] not in consumed
                and b["bbox"][3] > walk_y + SNAP
                and b["bbox"][1] < y0 - SNAP
            ]
            if in_gap:
                emit_text(walk_y, y0, in_gap)
            elif y0 - walk_y >= TIGHT_PACK:
                raw_rows.append(
                    {
                        "y0": walk_y,
                        "y1": y0,
                        "cols": _pad_to_page([], canvas, walk_y, y0),
                    }
                )
        if not pack:
            break
        union = _pack_box(pack)
        cap_ids = {c["id"] for c in caps}

        def _in_fig(item: dict) -> bool:
            """图里的字：横向压在图宽里，竖向也基本没探出图外。

            只看横向的话，一段穿过图上下缘的正文也会被当成图内标注吃进图里。
            """
            width = max(1.0, item["bbox"][2] - item["bbox"][0])
            if _overlap_x(item["bbox"], [union[0], union[2]]) < width * CONTAIN_FRAC:
                return False
            return _y_overlap_ratio(item["bbox"], union) >= CONTAIN_FRAC

        interior = [
            b
            for b in movable
            if b["id"] not in consumed
            and b["id"] not in cap_ids
            and _y_hits(union, b["bbox"])
            and _in_fig(b)
        ]
        side = [
            b
            for b in movable
            if b["id"] not in consumed
            and b["id"] not in cap_ids
            and _y_hits(union, b["bbox"])
            and not _in_fig(b)
        ]
        if side:
            cols = _cluster_columns(pack + side, page_w)
            raw_rows.append(
                {
                    "y0": y0,
                    "y1": y1,
                    "cols": _pad_to_page(cols, canvas, y0, y1),
                }
            )
            consumed.update(b["id"] for b in side)
        else:
            cols, group = _figure_grow_h(pack, caps, interior, live, roots, canvas, y0, y1)
            raw_rows.append({"y0": y0, "y1": y1, "cols": cols})
        consumed.update(m["id"] for m in pack)
        consumed.update(cap_ids)
        consumed.update(b["id"] for b in interior)
        walk_y = max(walk_y, y1)

    rest = [b for b in movable if b["id"] not in consumed]
    if rest:
        emit_text(
            min(b["bbox"][1] for b in rest),
            max(b["bbox"][3] for b in rest),
            rest,
        )

    body_x = _body_x_after_boxes(raw_rows, canvas)
    rows = [_format_row(raw, canvas, live, groups, em, body_x) for raw in raw_rows]
    rows = _stitch_rows(rows, canvas)
    asides = [
        cell
        for row in rows
        for cell in row.get("cells") or []
        if not cell.get("blank")
        and str(cell.get("slot")) == "aside"
        and cell.get("members")
        and (cell["bbox"][2] - cell["bbox"][0]) < page_w * SPAN_FRAC
    ]
    aside = None
    aside_side = None
    if asides:
        pick = max(asides, key=lambda c: (len(c.get("members") or []), c["bbox"][2] - c["bbox"][0]))
        aside = [pick["bbox"][0], pick["bbox"][2]]
        aside_mid = (pick["bbox"][0] + pick["bbox"][2]) / 2.0
        body_mid = (body_x[0] + body_x[1]) / 2.0
        aside_side = "left" if aside_mid <= body_mid else "right"
    tracks = {
        "frame": [float(canvas[0]), float(canvas[1])],
        "main": [float(body_x[0]), float(body_x[1])],
        "aside": [float(aside[0]), float(aside[1])] if aside else None,
        "aside_side": aside_side,
    }
    return rows, tracks


def assemble_page(
    page: dict,
    book_id: str = "",
    page_no: int = 0,
    extra_blocks: list[dict] | None = None,
    rules: dict[str, float] | None = None,
) -> dict:
    """总入口。只返回字典，不写盘。

    排版不再合并原始块：格子里装的就是 MinerU 原始块的下标，块内多行由块自己的
    lines 合并。所以这里不再需要摄取环节的合块文本。
    """
    with band_rules_applied(rules):
        return _assemble_page(page, book_id, page_no, extra_blocks)


def _assemble_page(
    page: dict,
    book_id: str,
    page_no: int,
    extra_blocks: list[dict] | None,
) -> dict:
    raw_size = page.get("page_size") or [595, 841]
    page_w, page_h = float(raw_size[0]), float(raw_size[1])
    boxes = drop_cross_page(collect_boxes(page, extra_blocks), page_h)
    _annotate_text(boxes)
    chrome, live = isolate_chrome(boxes, page_h)
    em = _em_of(live)
    roots = _roots(live)
    canvas = [0.0, page_w]
    rows, tracks = grow_layout(roots, live, canvas, page_w, em)
    frame = tracks.get("frame") or canvas
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
    from ir_lib import ir_spans
    for span in ir_spans(blk.get("lines")):
        rows.append(_copy_span(span))
    flat = _flatten_spans(rows)
    if flat:
        return flat
    return split_marks(str(blk.get("text") or ""))
def _valid_members(members: list, n: int) -> list[int]:
    out: list[int] = []
    for item in members or []:
        if item is None:
            continue
        idx = int(item)
        if 0 <= idx < n and idx not in out:
            out.append(idx)
    return out
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


def _add_text_block(blocks: list[dict], text: str, align: str, bbox: list[float]) -> int:
    """整行题注新起一块。必须带坐标，否则排序退回 order=0，空间校验也失效。"""
    blocks.append(
        {
            "id": f"kb-algo-cap-{len(blocks)}",
            "type": "text",
            "kind": "body",
            "text": text,
            "align": align,
            "bbox": [float(v) for v in bbox[:4]],
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
            idx = _add_text_block(
                blocks, part.get("text") or "", part.get("align") or "center", pbox
            )
            kids.append(_flow([idx], pbox, "main", False, f"{nid}s{pi}"))
            continue
        if role == "panel_row":
            sub = []
            for ci, colp in enumerate(part.get("cols") or []):
                cbox = list(colp.get("bbox") or pbox)
                if colp.get("blank"):
                    sub.append(_flow([], cbox, "blank", True, f"{nid}p{pi}_{ci}"))
                    continue
                mems = _valid_members(colp.get("members") or [], len(blocks))
                _set_caption(blocks, mems, colp.get("caption") or "")
                sub.append(_flow(mems, cbox, f"fig_{ci}", False, f"{nid}p{pi}_{ci}"))
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
        kid = kids[0]
        if (
            abs(float(kid.get("x0") or bbox[0]) - bbox[0]) <= SNAP
            and abs(float(kid.get("x1") or bbox[2]) - bbox[2]) <= SNAP
        ):
            kid["x0"], kid["x1"] = bbox[0], bbox[2]
            return kid
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


def _slot_by_box(box: list[float], tracks: dict | None) -> str:
    """漏网块按坐标进栏，不再写死正文。"""
    if not tracks:
        return "main"
    aside = tracks.get("aside")
    main = tracks.get("main")
    if aside:
        aside_hit = _overlap_x(box, aside)
        main_hit = _overlap_x(box, main or [0.0, 0.0])
        width = max(1.0, box[2] - box[0])
        if aside_hit >= width * 0.5 and aside_hit >= main_hit:
            return "aside"
    return "main"


def _iter_flows(node: dict):
    kids = node.get("children") or []
    if not kids:
        yield node
        return
    for kid in kids:
        yield from _iter_flows(kid)


def _padded_rescue_row(
    idx: int, box: list[float], frame: list[float], tracks: dict | None
) -> dict:
    """漏网块也铺满页宽：左右用空白格补上。"""
    y0, y1 = box[1], box[3]
    kids: list[dict] = []
    if box[0] - frame[0] > SNAP:
        kids.append(_flow([], [frame[0], y0, box[0], y1], "blank", True, f"rx{idx}l"))
    kids.append(_flow([idx], box, _slot_by_box(box, tracks), False, f"rx{idx}"))
    if frame[1] - box[2] > SNAP:
        kids.append(_flow([], [box[2], y0, frame[1], y1], "blank", True, f"rx{idx}r"))
    if len(kids) == 1:
        return kids[0]
    return _group(f"rx{idx}g", "cols", kids, [frame[0], y0, frame[1], y1])


def _rescue_into_cell(rows: list[dict], idx: int, box: list[float], tracks: dict | None) -> bool:
    """漏网块优先进已经套住它的格子，不另开一行窄带。"""
    cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    cands: list[tuple[float, dict]] = []
    for row in rows:
        for node in _iter_flows(row):
            nb = [
                float(node.get("x0") or 0.0),
                float(node.get("y0") or 0.0),
                float(node.get("x1") or 0.0),
                float(node.get("y1") or 0.0),
            ]
            if not (nb[0] <= cx <= nb[2] and nb[1] <= cy <= nb[3]):
                continue
            area = max(1.0, (nb[2] - nb[0]) * (nb[3] - nb[1]))
            cands.append((area, node))
    if not cands:
        return False
    node = min(cands, key=lambda item: item[0])[1]
    slot = (node.get("slots") or ["blank"])[0]
    if (slot == "blank" or node.get("empty")) and not (node.get("members") or []):
        _split_blank_leaf(node, idx, box, tracks)
        return True
    mems = list(node.get("members") or [])
    if idx not in mems:
        # 格子里已经有别的块也照样塞进去。以前这里直接 return，块就无声无息没了
        mems.append(idx)
    node["members"] = mems
    node["empty"] = False
    if slot == "blank":
        node["slots"] = [_slot_by_box(box, tracks)]
    return True


def _split_blank_leaf(node: dict, idx: int, box: list[float], tracks: dict | None) -> None:
    """漏网块掉进一整片空白格：把空白按它的四周切开，别让它撑满整格。

    切完仍是「上空白 + （左空白 | 块 | 右空白） + 下空白」，页宽照样铺满。
    """
    nb = [float(node["x0"]), float(node["y0"]), float(node["x1"]), float(node["y1"])]
    bx = [max(nb[0], box[0]), max(nb[1], box[1]), min(nb[2], box[2]), min(nb[3], box[3])]
    nid = str(node.get("id") or f"rx{idx}")
    mid_kids: list[dict] = []
    if bx[0] - nb[0] > SNAP:
        mid_kids.append(_flow([], [nb[0], bx[1], bx[0], bx[3]], "blank", True, f"{nid}xl"))
    mid_kids.append(
        _flow([idx], [bx[0], bx[1], bx[2], bx[3]], _slot_by_box(box, tracks), False, f"{nid}x")
    )
    if nb[2] - bx[2] > SNAP:
        mid_kids.append(_flow([], [bx[2], bx[1], nb[2], bx[3]], "blank", True, f"{nid}xr"))
    mid = (
        mid_kids[0]
        if len(mid_kids) == 1
        else _group(f"{nid}xc", "cols", mid_kids, [nb[0], bx[1], nb[2], bx[3]])
    )
    stack: list[dict] = []
    if bx[1] - nb[1] > SNAP:
        stack.append(_flow([], [nb[0], nb[1], nb[2], bx[1]], "blank", True, f"{nid}xt"))
    stack.append(mid)
    if nb[3] - bx[3] > SNAP:
        stack.append(_flow([], [nb[0], bx[3], nb[2], nb[3]], "blank", True, f"{nid}xb"))
    inner = stack[0] if len(stack) == 1 else _group(f"{nid}xs", "rows", stack, nb)
    node.pop("slots", None)
    node["kind"] = "cols"
    node["empty"] = False
    node["members"] = [idx]
    node["children"] = [inner]


def _rescue_missing(
    rows: list[dict],
    blocks: list[dict],
    n_src: int,
    skip: set[int],
    frame: list[float],
    tracks: dict | None = None,
) -> None:
    """兜底：漏网块先放进套住它的格子；没有格子才新开一行，仍铺满页宽。"""
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
        if _rescue_into_cell(rows, idx, box, tracks):
            continue
        node = _padded_rescue_row(idx, box, frame, tracks)
        pos = len(rows)
        for k, row in enumerate(rows):
            if float(row.get("y0") or 0.0) > box[1]:
                pos = k
                break
        rows.insert(pos, node)


def _close_row_gaps(rows: list[dict], frame: list[float]) -> list[dict]:
    """兜底行插进来之后，横带之间又会裂开缝。缝也是整页宽的空白带，补上。"""
    ordered = sorted(rows, key=lambda r: float(r.get("y0") or 0.0))
    out: list[dict] = []
    cursor: float | None = None
    for i, row in enumerate(ordered):
        y0 = float(row.get("y0") or 0.0)
        if cursor is not None and y0 - cursor > BLANK_MIN:
            out.append(_flow([], [frame[0], cursor, frame[1], y0], "blank", True, f"rg{i}"))
        out.append(row)
        cursor = max(cursor if cursor is not None else y0, float(row.get("y1") or y0))
    return out


def build_tree(assembled: dict, blocks: list[dict], n_src: int | None = None) -> dict:
    """算法横带 → 现行 page-ir 认识的 layout.tree。

    排版不合并原始块：每个格子装的就是落在它里面的原始块下标，有图没图走同一条路。
    块内的多行由块自己的 lines 合并，这里不再造派生块。
    """
    all_rows = list(assembled.get("rows") or [])
    # 题注被吸进图里的源块不再单独渲染，否则同一句字出两遍
    skip = _caption_srcs(assembled)
    rows = []
    for ri, row in enumerate(all_rows):
        cells = list(row.get("cells") or [])
        bbox = list(row.get("bbox") or [0, 0, 1, 1])
        kids = [cell_node(cell, blocks, f"r{ri}c{ci}") for ci, cell in enumerate(cells)]
        node = kids[0] if len(kids) == 1 else _group(f"r{ri}", "cols", kids, bbox)
        rows.append(node)
    frame = [float(v) for v in (assembled.get("frame") or [0.0, 1.0])]
    _rescue_missing(
        rows,
        blocks,
        len(blocks) if n_src is None else n_src,
        skip,
        frame,
        assembled.get("tracks") if isinstance(assembled.get("tracks"), dict) else None,
    )
    rows = _close_row_gaps(rows, frame)
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
    """横带转树。摄取不再写入页 JSON；留给 --explain 和旧测验。

    这一段只读 INDENT_PX，但阈值同样从全局取，所以也要圈在 band_rules_applied 里，
    否则 assemble_page 还原之后这里就退回模块默认值，YAML 改不动缩进。
    """
    with band_rules_applied(rules):
        return _build_layout(assembled, blocks, folio=folio)


def _build_layout(assembled: dict, blocks: list[dict], *, folio: str = "") -> dict:
    n_src = len(blocks)  # 图题注块从这个下标往后追加，兜底只找真源块
    tree = build_tree(assembled, blocks, n_src=n_src)
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
        f"bands {len(assembled.get('rows') or [])}",
    ]
    for i, row in enumerate(assembled.get("rows") or []):
        cells = row.get("cells") or []
        bits = []
        for cell in cells:
            slot = cell.get("slot")
            n = len([m for m in (cell.get("members") or []) if m is not None])
            bits.append(f"{slot}:{n}{' blank' if cell.get('blank') else ''}")
        lines.append(f"  band {i} {row.get('kind')}  " + " | ".join(bits))
        for ci, cell in enumerate(cells):
            roles = [str(p.get("role") or "") for p in cell.get("parts") or []]
            if roles:
                lines.append(f"    cell {ci} {cell.get('slot')}  parts=" + ",".join(roles))
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

