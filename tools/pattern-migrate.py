#!/usr/bin/env python3
"""抽象轨一次性整理：重分类、定路由先验、标记待拆的多主题模式。

背景：`infer_category` 原先首次命中即返回，`caption` 同时属于 figure 与 table，
表格标题被整批误分进 figure；同时 141 个模式的 `target_layer` 全是 `L1`，而
`layout_rules.yaml` 的规则语言只能表达 zone，绝大多数模式根本落不了 L1。

本脚本只做**确定性**的部分：
1. 用修好的 `infer_category` 重算类目
2. 按类目先验填 `landing_target`（未定路由的才填，已人工定过的不动）
3. 找出「一条摘要塞了好几件事」的模式，标出来交给 pattern_abstract 会话去拆

语义归并与拆分需要判断，不在这里做 —— 那是 buddy-copilot `pattern_abstract`
场景的活。

用法（在 buddy-api 容器里跑）：
    docker exec -i buddy-api python - < tools/pattern-migrate.py            # 预演
    docker exec -i buddy-api python - < tools/pattern-migrate.py -- --apply # 落库
"""

from __future__ import annotations

import re
import sys
from collections import Counter

sys.path.insert(0, "/app")

from app.db.models import CorpusImprovementPattern, CorpusImprovementPoint  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.services.improvement_points import infer_category  # noqa: E402

APPLY = "--apply" in sys.argv

# 多主题摘要的分隔符：中文顿号/分号/西文分号都算一件事的边界。
_CLAUSE_SEP = re.compile(r"[；;、]")
# 「必修二第9批：…」这类批次前缀不是主题，切分前先摘掉。
_BATCH_PREFIX = re.compile(r"^[^：:]{0,20}第\s*\d+\s*批\s*[：:]")


def clauses(text: str) -> list[str]:
    body = _BATCH_PREFIX.sub("", text or "")
    return [c.strip() for c in _CLAUSE_SEP.split(body) if len(c.strip()) >= 4]


def topics(text: str) -> set[str]:
    """摘要里各分句分别命中的类目集合。多于一个 = 一条塞了好几件事。"""
    return {infer_category(c) for c in clauses(text)} - {"other"}


def main() -> int:
    session = get_session_factory()()
    pats = session.query(CorpusImprovementPattern).all()

    recat: list[tuple[str, str, str]] = []
    routed: Counter[str] = Counter()
    multi: list[tuple[str, int, str]] = []

    for pat in pats:
        text = pat.abstract_text or ""

        found = topics(text)
        if len(found) >= 2:
            multi.append((pat.id, len(found), text[:70]))

        new_cat = infer_category(text)
        if new_cat != pat.category:
            recat.append((pat.category, new_cat, text[:60]))
            if APPLY:
                pat.category = new_cat

        # 只统计，不回填。按类目关键词猜出来的生效面从没被确认过，填上去只会让
        # 「待定路由」的清单假性清空。没定就是没定，等人或 Agent 显式判。
        routed[pat.landing_target or "(未定路由)"] += 1

        # 多主题的先别定路由：拆开之前判哪个生效面都是错的。
        if APPLY and len(found) >= 2:
            pat.landing_target = ""
            pat.target_layer = "L1"
            pat.landing_note = f"摘要含 {len(found)} 个主题（{'/'.join(sorted(found))}），需先拆分再定路由"

    total_ips = session.query(CorpusImprovementPoint).filter_by(status="accepted").count()

    print(f"模式 {len(pats)} 条 · accepted 改进点 {total_ips} 条")
    print()
    print(f"=== 重分类：{len(recat)} 条类目会变 ===")
    for (old, new), count in Counter((o, n) for o, n, _ in recat).most_common():
        print(f"  {old:9s} -> {new:9s}  {count}")
    print()
    print("=== 落地目标分布（先验） ===")
    for target, count in routed.most_common():
        print(f"  {target:20s} {count}")
    print()
    print(f"=== 多主题模式（一条塞了好几件事，需拆）：{len(multi)} 条 ===")
    for pid, n, text in sorted(multi, key=lambda x: -x[1])[:15]:
        print(f"  [{n} 个主题] {text}")

    if APPLY:
        session.commit()
        print()
        print("已落库。")
    else:
        print()
        print("预演，未落库。加 --apply 生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
