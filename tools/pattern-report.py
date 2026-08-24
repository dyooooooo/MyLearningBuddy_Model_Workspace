#!/usr/bin/env python3
"""导出抽象轨全量数据文档（Markdown）。

按落地目标分组列出每条模式的规律、成员页、判定理由与落地状态，
让人不用连数据库也能审。

用法（在 buddy-api 容器里跑，输出重定向到 doc/reports/）：
    docker exec -i buddy-api python - < tools/pattern-report.py > doc/reports/xxx.md
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict

sys.path.insert(0, "/app")

from app.db.models import CorpusImprovementPattern, CorpusImprovementPoint  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.services.improvement_points import LANDING_TARGETS  # noqa: E402

STATUS_LABEL = {
    "draft": "草案（带类目先验，未经确认）",
    "candidate": "待定路由",
    "routed": "已定路由 · 待落地",
    "landed": "已落地",
    "no_landing": "不可规则化（终态）",
    "needs_ip_split": "需先拆改进点（模式级拆不动）",
}

# 展示顺序：先看已判定的，再看待办，最后看死胡同。
TARGET_ORDER = [
    "L1.layout_rule",
    "L1.config_pattern",
    "L1.theme_css",
    "L0.normalize",
    "L0.renderer",
    "L3.book_patch",
    "",
]


def esc(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def main() -> int:
    session = get_session_factory()()
    pats = session.query(CorpusImprovementPattern).all()
    members: dict[str, list[CorpusImprovementPoint]] = defaultdict(list)
    for ip in session.query(CorpusImprovementPoint).filter(
        CorpusImprovementPoint.pattern_id != ""
    ):
        members[ip.pattern_id].append(ip)

    def pages(pid: str) -> list[int]:
        return sorted({int(m.page_no or 0) for m in members.get(pid, []) if m.page_no})

    by_status = Counter(p.status for p in pats)
    by_target = Counter(p.landing_target or "" for p in pats)
    out: list[str] = []
    w = out.append

    w("# 抽象轨全量数据")
    w("")
    w(f"教材 `hujiao_math_hs_v1` · 模式 {len(pats)} 条 · "
      f"挂到模式的改进点 {sum(len(v) for v in members.values())} 条")
    w("")
    w("`landed_ref` 目前全为空 —— 已定路由的模式知道该写进哪个文件，"
      "但写盘要走 `fs.request_edit` / `editions.publish` 批准闸门，尚未执行。")
    w("")

    w("## 一、总览")
    w("")
    w("### 按状态")
    w("")
    w("| 状态 | 条数 | 含义 |")
    w("|---|---:|---|")
    for st, n in by_status.most_common():
        w(f"| `{st}` | {n} | {STATUS_LABEL.get(st, '')} |")
    w("")
    w("### 按落地目标（该写到哪个文件）")
    w("")
    w("| landing_target | 条数 | 写入面 |")
    w("|---|---:|---|")
    for t in TARGET_ORDER:
        n = by_target.get(t, 0)
        if not n:
            continue
        label = LANDING_TARGETS.get(t, "**尚未判定**")
        w(f"| `{t or '(未定)'}` | {n} | {label} |")
    w("")
    w("### 按成员数（成员页 = 该模式的黄金集）")
    w("")
    dist = Counter(int(p.member_count or 0) for p in pats)
    w("| 成员数 | 模式条数 |")
    w("|---:|---:|")
    for k in sorted(dist):
        w(f"| {k} | {dist[k]} |")
    w("")
    w(f"单成员模式 {dist.get(1, 0)} 条（{dist.get(1, 0) * 100 // max(len(pats), 1)}%）。"
      "单成员意味着这条规律目前只见过一次，证据薄弱；"
      "成员多的（如 28 页那条）才是最值得优先落地的。")
    w("")

    # ---- 明细：按落地目标分组 ----
    w("## 二、明细")
    w("")
    grouped: dict[str, list[CorpusImprovementPattern]] = defaultdict(list)
    for p in pats:
        grouped[p.landing_target or ""].append(p)

    for target in TARGET_ORDER:
        group = grouped.get(target) or []
        if not group:
            continue
        group.sort(key=lambda x: -int(x.member_count or 0))
        title = f"`{target}`" if target else "未定路由"
        w(f"### {title} — {len(group)} 条")
        w("")
        if target:
            w(f"> {LANDING_TARGETS[target]}")
            w("")
        w("| 成员 | 页码 | 规律 | 状态 | 判定理由 |")
        w("|---:|---|---|---|---|")
        for p in group:
            pg = pages(p.id)
            pg_s = ", ".join(str(x) for x in pg[:10]) + ("…" if len(pg) > 10 else "")
            note = esc(p.landing_note)
            if "按类目先验推定" in note:
                note = "_（类目先验，未经人工/Agent 确认）_"
            w(
                f"| {p.member_count or 0} | {pg_s or '—'} | {esc(p.abstract_text)[:70]} "
                f"| `{p.status}` | {note[:120] or '—'} |"
            )
        w("")

    # ---- 重点模式：成员最多的几条 ----
    w("## 三、最值得优先落地的（成员 ≥ 3）")
    w("")
    w("成员多 = 同一规律反复出现 = 证据足、收益大。这些自带 N 页黄金集。")
    w("")
    top = sorted(pats, key=lambda x: -int(x.member_count or 0))
    for p in top:
        if int(p.member_count or 0) < 3:
            break
        w(f"#### {esc(p.abstract_text)[:64]}")
        w("")
        w(f"- 模式 id：`{p.id}`")
        w(f"- 成员 {p.member_count} 条，页码：{pages(p.id)}")
        w(f"- 类目 `{p.category}` · 状态 `{p.status}` · 落地目标 `{p.landing_target or '(未定)'}`")
        if p.landing_note:
            w(f"- 判定理由：{esc(p.landing_note)}")
        sample = [
            esc(str(__import__("json").loads(m.claim_json or "{}").get("summary") or ""))
            for m in members.get(p.id, [])[:3]
        ]
        sample = [x for x in sample if x]
        if sample:
            w("- 成员改进点原文（抽样）：")
            for x in sample:
                w(f"  - {x[:110]}")
        w("")

    # ---- 残留重复族：98 条 draft 里还没并起来的 ----
    w("## 四、还没并起来的重复族（draft 里）")
    w("")
    w("`_ensure_pattern` 用摘要前 120 字的精确哈希匹配，「pN（印张48偶数页）补缺失章页眉」"
      "与「pN（印张50偶数页）补缺失章页眉」归一化后仍是不同串，各成一条。"
      "下面这些是同一条规律被记成好几条 —— 跑 `pattern_abstract` 会话可以合并掉。")
    w("")
    families: list[tuple[str, str]] = [
        ("补缺失章页眉", r"页眉.*(补|缺)|(补|缺).*页眉"),
        ("CJK 字符混进 LaTeX", r"(中文|CJK|汉字).*(公式|latex)|(公式|latex).*(中文|CJK)"),
        ("删除误入正文的装饰图", r"删.*(装饰|噪点|碎片).*图|装饰图"),
        ("小题编号①②③④防吞题", r"①|②③|题干.*编号|编号.*题干"),
        ("旁注落侧栏", r"旁注|aside"),
    ]
    drafts = [p for p in pats if p.status == "draft"]
    seen: set[str] = set()
    import re as _re

    for label, pattern in families:
        rx = _re.compile(pattern)
        hits = [p for p in drafts if p.id not in seen and rx.search(p.abstract_text or "")]
        if len(hits) < 2:
            continue
        for p in hits:
            seen.add(p.id)
        targets = Counter(p.landing_target or "(未定)" for p in hits)
        w(f"### {label} — {len(hits)} 条独立模式")
        w("")
        if len(targets) > 1:
            w(f"⚠️ 这些被判去了不同生效面（{dict(targets)}），合并前要先确认判据是否真的相同。")
            w("")
        w("| 页 | 规律 | 落地目标 |")
        w("|---|---|---|")
        for p in hits:
            pg = pages(p.id)
            w(
                f"| {', '.join(str(x) for x in pg[:6]) or '—'} "
                f"| {esc(p.abstract_text)[:64] } | `{p.landing_target or '(未定)'}` |"
            )
        w("")

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
