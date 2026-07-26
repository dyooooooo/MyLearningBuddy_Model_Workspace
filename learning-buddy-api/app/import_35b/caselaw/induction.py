"""离线归纳 Agent + 晋级门（判例 → 规则的提议流水线）。

严格遵守架构文档 4.2 的晋级治理：**Agent 提议、Harness 验证、人批准**——本模块
只生成**提议文件**，绝不自动写规则库 / 抑制表 / prompt。人是唯一有规则库写权限
的角色。

晋级硬标准（全部满足才纳入提议，并附 gate 结果供人核）：
1. 复现 ≥ N（同指纹判例出现次数）
2. 修复 100% 一致（方案库同指纹的 old→new 必须完全相同）
3. 可形式化（指纹匹配 / 字面替换）
4. 回放零破坏（golden 全绿——在**当前基线**上跑通，破坏基线则不许晋级）
5. 无冲突裁决（同一指纹不得既在误判库又在方案库）

「离线」体现在：本模块不在导入/裁决热路径调用，只由 CLI ``scripts/induct-caselaw.py``
手动触发；不调用 GPU/VLM（纯 Postgres 聚合 + 纯代码 golden 回放）。
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.import_35b import store
from app.util.json_util import loads

logger = logging.getLogger(__name__)

# 三个「执行槽」——晋级为规则只能落进其一，否则不可执行。
SLOT_SUPPRESSIONS = "import35b_suppressions"  # 抽屉A：黑名单（指纹精确匹配）
SLOT_REPAIR_RULES = "repair_rules_json"       # 抽屉B：乱码更正（命令白名单）
SLOT_OVERRIDE_ONLY = "override_only"          # 无全局执行槽：仅建议，保留为局部 override

_CMD_RE = re.compile(r"\\([a-zA-Z]+)")


@dataclass
class Proposal:
    kind: str  # "suppression" | "repair_rule"
    suspect_type: str
    fingerprint: str
    occurrences: int
    consistency: float
    formalizable: bool
    # 该建议应落进哪个执行槽（人批准时照此放置）
    target_slot: str = ""
    # 人类可读的一句话操作指引
    action_hint: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    evidence_case_ids: list[str] = field(default_factory=list)
    conflict: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass
class GateResult:
    passed: bool
    golden_total: int
    golden_failures: list[str]
    checked_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 归纳：判例聚类 → 候选提议 ───────────────────────────────────────────────


def mine_proposals(session: Session, *, min_occurrences: int | None = None) -> list[Proposal]:
    """从 Postgres 判例库聚合候选提议（不写任何规则）。"""
    n = min_occurrences or 3
    mis = store.list_caselaw(session, kind="misjudgment")
    sol = store.list_caselaw(session, kind="solution")

    mis_by_fp: dict[str, list] = defaultdict(list)
    for c in mis:
        if c.fingerprint:
            mis_by_fp[c.fingerprint].append(c)
    sol_by_fp: dict[str, list] = defaultdict(list)
    for c in sol:
        if c.fingerprint:
            sol_by_fp[c.fingerprint].append(c)

    proposals: list[Proposal] = []

    # 误判库 → suppression 提议（同指纹重复误报 ≥ N，且不与方案库冲突）
    for fp, cases in mis_by_fp.items():
        if len(cases) < n:
            continue
        conflict = fp in sol_by_fp
        reasons = []
        if conflict:
            reasons.append("冲突：同指纹也出现在方案库（有时是真错误），不可晋级为抑制")
        stype = cases[0].suspect_type
        proposals.append(
            Proposal(
                kind="suppression",
                suspect_type=stype,
                fingerprint=fp,
                occurrences=len(cases),
                consistency=1.0,
                formalizable=True,
                target_slot=SLOT_SUPPRESSIONS,
                action_hint=(
                    f"抽屉A（黑名单）：往 import35b_suppressions 插一行 "
                    f"scope=fingerprint, fingerprint={fp}, suspect_type={stype}"
                ),
                detail={"scope": "fingerprint"},
                evidence_case_ids=[c.id for c in cases],
                conflict=conflict,
                reasons=reasons,
            )
        )

    # 方案库 → repair_rule 提议（同指纹 old→new 100% 一致 ≥ N）
    from app.import_35b.model_boundary import (
        ESCAPE_PRONE_FIRST_LETTERS,
        whitelisted_commands,
    )

    whitelisted = whitelisted_commands()
    for fp, cases in sol_by_fp.items():
        if len(cases) < n:
            continue
        stype = cases[0].suspect_type
        mappings = set()
        for c in cases:
            dec = loads(c.decision_json, {})
            mappings.add((str(dec.get("old") or ""), str(dec.get("new") or "")))
        consistent = len(mappings) == 1
        (old, new) = next(iter(mappings)) if mappings else ("", "")
        reasons: list[str] = []
        if not consistent:
            reasons.append(f"不一致：同指纹有 {len(mappings)} 种 old→new 修正，不可形式化")

        # 分类：命令类（能进抽屉B）vs 内容类（无全局执行槽，仅建议）
        add_commands = _escape_damage_commands(old, new, whitelisted, ESCAPE_PRONE_FIRST_LETTERS)
        if add_commands and consistent:
            target_slot = SLOT_REPAIR_RULES
            formalizable = True
            action_hint = (
                f"抽屉B（乱码更正）：把命令 {', '.join(add_commands)} "
                f"加入 repair_rules.json 的 commands 数组"
            )
            detail = {"add_commands": add_commands, "old": old, "new": new}
        else:
            target_slot = SLOT_OVERRIDE_ONLY
            formalizable = False
            if consistent:
                reasons.append(
                    "内容类修正：无全局执行槽——保留为该页 override；"
                    "如属系统性问题请考虑调 prompt（过 golden 门）"
                )
            action_hint = "仅建议：无执行槽，不要写入任何规则文件/表"
            detail = {"old": old, "new": new}

        proposals.append(
            Proposal(
                kind="repair_rule",
                suspect_type=stype,
                fingerprint=fp,
                occurrences=len(cases),
                consistency=1.0 if consistent else round(1 / max(len(mappings), 1), 3),
                formalizable=formalizable,
                target_slot=target_slot,
                action_hint=action_hint,
                detail=detail,
                evidence_case_ids=[c.id for c in cases],
                conflict=fp in mis_by_fp,
                reasons=reasons,
            )
        )

    return proposals


def _escape_damage_commands(
    old: str,
    new: str,
    whitelisted: set[str],
    escape_prone: frozenset[str],
) -> list[str]:
    """从 old→new 里提取「应加入白名单」的命令：修正后新出现、首字母属转义易损
    集（b/f/n/r/t）、且尚未在白名单中的 LaTeX 命令。空列表 = 非命令类修正。"""
    new_cmds = set(_CMD_RE.findall(new or ""))
    old_cmds = set(_CMD_RE.findall(old or ""))
    restored = {
        c
        for c in (new_cmds - old_cmds)
        if c and c[0] in escape_prone and c not in whitelisted
    }
    return sorted(restored)


# ── 晋级门：golden 全绿回放（纯代码，无 GPU）────────────────────────────────


def golden_gate() -> GateResult:
    """在当前基线上跑 golden 全绿。破坏基线则任何提议都不许晋级。"""
    from app.import_35b.harness_golden import (
        assert_case_expectations,
        compile_case,
        load_inline_cases,
    )

    failures: list[str] = []
    total = 0
    cases: list[dict[str, Any]] = []
    cases.extend(load_inline_cases())
    cases.extend(_load_v4_arch_cases())

    for case in cases:
        if case.get("expect_v4_model_output") is False:
            continue
        total += 1
        try:
            bp, html = compile_case(case)
            assert_case_expectations(case, bp, html)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{case.get('id', '?')}: {exc}")

    return GateResult(
        passed=not failures,
        golden_total=total,
        golden_failures=failures,
        checked_at=_now(),
    )


def _load_v4_arch_cases() -> list[dict[str, Any]]:
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[3]
        / "data"
        / "eval"
        / "35b_v4_arch_golden.jsonl"
    )
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def build_report(session: Session, *, min_occurrences: int | None = None) -> dict[str, Any]:
    """归纳 + 过晋级门 → 完整提议报告（供人批准，绝不自动应用）。"""
    proposals = mine_proposals(session, min_occurrences=min_occurrences)
    gate = golden_gate()

    promotable = [
        p
        for p in proposals
        if p.formalizable and not p.conflict and gate.passed
    ]
    blocked = [p for p in proposals if p not in promotable]

    return {
        "generated_at": _now(),
        "caselaw_total": store.count_caselaw(session),
        "misjudgment_total": store.count_caselaw(session, kind="misjudgment"),
        "solution_total": store.count_caselaw(session, kind="solution"),
        "min_occurrences": min_occurrences or 3,
        "gate": asdict(gate),
        "activation_threshold": settings.import35b_caselaw_min_cases,
        "promotable": [asdict(p) for p in promotable],
        "blocked": [asdict(p) for p in blocked],
        "note": (
            "本报告仅为提议。晋级为规则/抑制须人工审核 promotable 项后，手动写入 "
            "import35b_suppressions / repair_rules。系统绝不自动应用。"
        ),
    }
