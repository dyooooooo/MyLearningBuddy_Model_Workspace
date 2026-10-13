"""正文细目（from_body kids）修复原型 —— 只读，用于验证补丁逻辑与估算影响面。

两种跑法：
  python kids_fix_proto.py --selftest          # 本地单元自检（不碰远程）
  ssh mlb 'cd <repo>/agents/textbook-agent && python3 -B -' < kids_fix_proto.py
                                              # 远程全库扫描（只读，-B 不写 __pycache__）

本文件里的 _strip_footnote / _KID_NUM / _kid_level / is_kids_noise 就是施工单
2026-10-13-body-toc-kids-WORKORDER.md 里要落到 ir_toc.py / toc_lexicon.py 的同一份逻辑。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- 补丁逻辑（与施工单一致）

# 脚注标记：LaTeX 上标 `$^{①}$` / `^{①}`，或贴在标题末尾的圈码 `①`。
_FOOTNOTE_TEX = re.compile(r"\$\^\{[^{}]*\}\$|\^\{[^{}]*\}")
_FOOTNOTE_TAIL = re.compile(r"(?<=[\u4e00-\u9fff\u3001\u3002\uff09\uff1a\u201d\u300b\uff3d])\s*[\u2460-\u2473]\s*$")


def _strip_footnote(text: str) -> str:
    """剥掉脚注标记：`$^{①}$` 形式与尾随圈码。标题显示与比对都用它。"""
    out = _FOOTNOTE_TEX.sub("", text or "")
    prev = None
    while prev != out:
        prev = out
        out = _FOOTNOTE_TAIL.sub("", out)
    return out.strip()


# 细目层级只看编号形态：正文页 IR 的 level 一律是 2，不可用。
_KID_NUM: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^[\uff08(]\s*(?:[0-9\uff10-\uff19]{1,2}|[一二三四五六七八九十]{1,3})\s*[)\uff09]"), 2),  # (1) （一）
    (re.compile(r"^[\u2460-\u2473]"), 3),                                                              # ①
    (re.compile(r"^[0-9\uff10-\uff19]{1,2}(?:\.[0-9\uff10-\uff19]{1,2}){1,2}(?=[\s\u3001\uff0e.]|$)"), 1),  # 1.1 / 1.1.1
    (re.compile(r"^[0-9\uff10-\uff19]{1,2}(?=[\s\u3001\uff0e.])"), 1),                                  # 1 xxx
    (re.compile(r"^[一二三四五六七八九十]{1,3}[\u3001\uff0e.]"), 2),                                      # 一、
]


def _kid_level(text: str, base: int, prev: int | None) -> int:
    """按编号形态定细目层级。base = 骨架行的绝对层级。

    `1 xxx` -> base+1；`(1)` / `（一）` -> base+2；`①` -> base+3；
    无编号 -> 跟随上一条兄弟（不比它深）。
    """
    for pattern, rank in _KID_NUM:
        if pattern.match(text):
            return base + rank
    return prev if prev is not None else base + 1


# ★ 窄闸门：只挡「练习/习题」这类，**不复用 column_labels**
#   —— column_labels 里含「提出问题」「交流与讨论」等，正文里它们是真小标题，
#      复用会误杀（仓库现有用例 test_body_heading_repeated_on_later_pages_is_kept_once 就是守这个）。
_KIDS_NOISE = (
    "练习",
    "习题",
    "例题",
    "复习题",
    "复习参考题",
    "练习题",
    "思考题",
    "组题",
    "A组",
    "B组",
    "C组",
)


def is_kids_noise(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return True
    return any(compact.startswith(re.sub(r"\s+", "", w)) for w in _KIDS_NOISE)


# ---------------------------------------------------------------- 新 _body_kids


def new_body_kids(rows, titles, last_page):
    seen: set[str] = set()
    out = []
    for index, row in enumerate(rows):
        kids = []
        base = row["level"]
        prev_kid_level = None
        start = row["pdf_page"]
        end = next((r["pdf_page"] for r in rows[index + 1 :] if r["pdf_page"]), last_page + 1)
        for page_no in range(start, end) if start else ():
            for item in titles.get(page_no, ()):
                if any(_same_title(item["key"], other["key"]) for other in rows):
                    continue
                raw = _strip_footnote(item["text"])
                title = _keep_heading(raw, seen)
                if not title or is_kids_noise(title):
                    continue
                level = _kid_level(title, base, prev_kid_level)
                prev_kid_level = level
                kids.append(
                    {
                        "level": level,
                        "title": title,
                        "print_page": item["print_page"],
                        "pdf_page": page_no,
                    }
                )
        out.append(kids)
    return out


def _same_title(left, right):
    return _ORIG["same_title"](left, right)


def _keep_heading(text, seen):
    return _ORIG["keep_heading"](text, seen)


_ORIG: dict = {}


def install(ir_toc_mod) -> None:
    """把补丁装到 ir_toc 上（原型用；正式补丁是直接改源码）。"""
    _ORIG["same_title"] = ir_toc_mod._same_title
    _ORIG["keep_heading"] = ir_toc_mod._keep_heading
    _ORIG["title_key"] = ir_toc_mod._title_key
    _ORIG["body_kids"] = ir_toc_mod._body_kids

    # 脚注标记在比对键里也先剥掉，否则 `《论语》十二章$^{①}$` 躲过去重
    ir_toc_mod._title_key = lambda text: _ORIG["title_key"](_strip_footnote(text))
    # 显示标题也剥，并且去重集合用的是剥后文本
    ir_toc_mod._keep_heading = lambda text, seen: _ORIG["keep_heading"](_strip_footnote(text), seen)
    ir_toc_mod._body_kids = new_body_kids


# ---------------------------------------------------------------- 自检


def selftest() -> None:
    fails = []

    def eq(got, want, label):
        if got != want:
            fails.append(f"{label}: got={got!r} want={want!r}")

    # 1) 脚手架：base=1 表示骨架行 `1.2 直线的方程` 的层级
    base = 1
    prev = None
    seq = [
        ("1 几种特殊形式的直线方程", 2),
        ("(1) 直线的点斜式方程", 3),
        ("(2) 直线的两点式方程", 3),
        ("2 直线的一般式方程", 2),
    ]
    for text, want in seq:
        prev = _kid_level(text, base, prev)
        eq(prev, want, f"level({text})")

    # 2) 脚注
    eq(_strip_footnote("《论语》十二章$^{①}$"), "《论语》十二章", "footnote-tex")
    eq(_strip_footnote("苏武传①"), "苏武传", "footnote-tail")
    eq(_strip_footnote("① 直线的倾斜角"), "① 直线的倾斜角", "footnote-leading-circled")

    # 3) 闸门（不能误杀真小标题）
    for w, want in [
        ("练习1.1", True),
        ("习题1.2(3)", True),
        ("A组", True),
        ("B组", True),
        ("提出问题", False),
        ("构建模型", False),
        ("解析几何的诞生", False),
        ("1 几种特殊形式的直线方程", False),
    ]:
        eq(is_kids_noise(w), want, f"noise({w})")

    if fails:
        print("SELFTEST FAILED:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("SELFTEST OK (12 assertions)")


# ---------------------------------------------------------------- 全库扫描（只读）


def book_label(book: Path) -> str:
    meta = book / "meta.json"
    name, subject = book.name[:8], ""
    if meta.is_file():
        try:
            d = json.loads(meta.read_text(encoding="utf-8"))
            name = str(d.get("label") or d.get("title") or d.get("book_name") or name)
            subject = str(d.get("subject") or "")
        except Exception:  # noqa: BLE001
            pass
    return name


def dump(root: Path, ir_toc_mod, needle: str) -> None:
    """打印命中书名里 needle 的每本书「会被删掉」的具体标题，供人工核对闸门。"""
    from collections import Counter

    books = [
        p
        for p in sorted((root / "textbooks").iterdir())
        if p.is_dir() and (p / "toc.print").is_file() and (p / "ir" / "pages").is_dir()
    ]
    targets = [(p, book_label(p)) for p in books if needle in book_label(p)]
    print(f"命中 {len(targets)} 本：{[l for _b, l in targets]}\n")

    def kids_of(toc):
        return [(e["level"], e["title"], e["pdf_page"]) for e in toc["entries"] if e.get("from_body")]

    old_all = {b.name: kids_of(ir_toc_mod.build_toc(b)) for b, _l in targets}
    install(ir_toc_mod)

    for b, label in targets:
        new_kids = kids_of(ir_toc_mod.build_toc(b))
        old_kids = old_all[b.name]
        new_keys = {(t, p) for _lv, t, p in new_kids}
        deleted = [(lv, t, p) for lv, t, p in old_kids if (_strip_footnote(t), p) not in new_keys]
        print(f"=== {label}  旧 {len(old_kids)} → 新 {len(new_kids)}，删 {len(deleted)} ===")
        cnt = Counter(_strip_footnote(t)[:8] for _lv, t, p in deleted)
        print("  删除标题前 8 字词频:", dict(cnt.most_common(12)))
        for lv, t, p in deleted:
            hit = is_kids_noise(_strip_footnote(t))
            print(f"    - {t!r}  (pdf {p}, 旧lv{lv}, 闸门命中={hit})")
        print()


def scan(root: Path, ir_toc_mod) -> None:
    books = [
        p
        for p in sorted((root / "textbooks").iterdir())
        if p.is_dir() and (p / "toc.print").is_file() and (p / "ir" / "pages").is_dir()
    ]
    failed: list[tuple[str, str]] = []

    def kids_of(toc):
        return [(e["level"], e["title"], e["pdf_page"]) for e in toc["entries"] if e.get("from_body")]

    old_all: dict[str, list] = {}
    for book in books:
        try:
            old_all[book.name] = kids_of(ir_toc_mod.build_toc(book))
        except Exception as exc:  # noqa: BLE001
            failed.append((book.name, "old " + repr(exc)[:60]))

    install(ir_toc_mod)

    tot = kept = dropped = added = level_changed = renamed = 0
    per_book = []
    for book in books:
        if book.name not in old_all:
            continue
        try:
            new_kids = kids_of(ir_toc_mod.build_toc(book))
        except Exception as exc:  # noqa: BLE001
            failed.append((book.name, "new " + repr(exc)[:60]))
            continue
        old_kids = old_all[book.name]
        # 归一化旧标题（同样剥脚注），把「只是标题文本变了」和「真被删掉」分开
        old_norm = [(lv, _strip_footnote(t), p) for lv, t, p in old_kids]
        # 对拍只看（标题, 页），层级单独比 —— 否则「只有层级变了」会被算成删+新
        old_keys = {(t, p) for _lv, t, p in old_norm}
        new_keys = {(t, p) for _lv, t, p in new_kids}
        dr = len(old_keys - new_keys)
        ad = len(new_keys - old_keys)
        rn = sum(1 for lv, t, p in old_kids if (t, p) != (_strip_footnote(t), p))
        common = {(t, p): lv for lv, t, p in old_norm}
        new_map = {(t, p): lv for lv, t, p in new_kids}
        lv = sum(1 for k in new_map if k in common and common[k] != new_map[k])
        tot += len(old_norm)
        kept += len(old_keys & new_keys)
        dropped += dr
        added += ad
        renamed += rn
        level_changed += lv
        per_book.append((book_label(book), len(old_norm), len(new_kids), dr, ad, rn, lv))
    print(f"\n=== 全库细目影响面（{len(per_book)} 本参与，{len(failed)} 本失败）===")
    if tot:
        print(f"旧细目总数（脚注归一后）: {tot}")
        print(f"原样保留                : {kept}   ({kept / tot * 100:.1f}%)")
        print(f"真删除                  : {dropped}   ({dropped / tot * 100:.1f}%)")
        print(f"新出现                  : {added}")
        print(f"仅标题文本变化（剥脚注）: {renamed}")
        print(f"层级发生变化            : {level_changed}")
    print("\n删除量 top 20:")
    for name, o, n, dr, ad, rn, lv in sorted(per_book, key=lambda x: -x[3])[:20]:
        pct = dr / o * 100 if o else 0
        print(f"  {name:34s} 旧{o:4d} 新{n:4d} 删{dr:4d}({pct:3.0f}%) 新{ad:3d} 改名{rn:3d} 层级{lv:4d}")
    if failed:
        print("\n失败（前 10）:")
        for name, err in failed[:10]:
            print(f"  {name}: {err}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit(0)
    here = Path(__file__).resolve()
    repo = Path(os.environ.get("MLB_REPO", str(here.parent.parent.parent)))
    tools = Path(os.environ.get("MLB_TOOLS", str(repo / "agents" / "textbook-agent" / "workspace" / "tools")))
    home = Path(os.environ.get("MLB_HOME", str(repo / "data" / "agent-home")))
    if not tools.is_dir():
        print(f"找不到 tools 目录: {tools}  （用 MLB_TOOLS 指定）")
        sys.exit(2)
    if not (home / "textbooks").is_dir():
        print(f"找不到 textbooks 目录: {home / 'textbooks'}  （用 MLB_HOME 指定）")
        sys.exit(2)
    os.environ.setdefault("AGENT_HOME", str(home))
    sys.path.insert(0, str(tools))
    import ir_toc  # noqa: E402

    selftest()
    if "--dump" in sys.argv:
        dump(home, ir_toc, sys.argv[sys.argv.index("--dump") + 1])
    else:
        scan(home, ir_toc)
