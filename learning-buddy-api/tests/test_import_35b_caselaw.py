"""v4 判例治理 golden：判例记录 / finding 富化 / T2 开关 / 离线归纳晋级门。

全部纯代码路径（无 Milvus / 无 vLLM）：
- 判例向量索引在无 pymilvus/连接时降级为 no-op，测试只验 canonical Postgres 行为；
- enrich/agent 默认关闭时必须原样返回（零行为变更）；
- 归纳 + 晋级门在种子判例上产出 promotable/blocked，并冲突/一致性判定正确。
"""
from __future__ import annotations

import pytest

from app.import_35b import store
from app.import_35b.caselaw import induction, service
from app.import_35b.caselaw import store as vec_store
from app.import_35b.validation import agent_check_enabled, run_agent_checks
from app.import_35b.validation.rules import Finding


@pytest.fixture
def db(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import models  # noqa: F401
    from app.db.models import Base

    monkeypatch.setattr("app.config.settings.buddy_data_dir", str(tmp_path))
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.close()


class _Item:
    """轻量替身，模拟 Import35bReviewItem 的字段访问。"""

    def __init__(self, **kw):
        self.id = kw.get("id", "rev-1")
        self.job_id = kw.get("job_id", "job-1")
        self.page_no = kw.get("page_no", 12)
        self.suspect_type = kw.get("suspect_type", "MATH_FALLBACK")
        self.fingerprint = kw.get("fingerprint", "fp-abc")
        self.evidence_json = kw.get("evidence_json", '{"text": "x \\\\leqslant 1"}')


# ── Finding 模型：detector 字段 ─────────────────────────────────────────────


def test_finding_has_detector_default_rule():
    f = Finding(suspect_type="X", page_no=1).finalize()
    assert f.detector == "rule"
    assert f.model_dump()["detector"] == "rule"


# ── T2 Agent 视觉比对：默认关闭 ─────────────────────────────────────────────


def test_agent_check_disabled_by_default():
    assert agent_check_enabled() is False


def test_run_agent_checks_returns_empty_when_disabled():
    class _BP:
        page_no = 3

    out = run_agent_checks(_BP(), "<section></section>", page_jpeg=b"\xff\xd8\xff")
    assert out == []


# ── 判例记录：裁决 → canonical Postgres 行 ─────────────────────────────────


def test_record_case_false_positive_writes_misjudgment(db):
    cid = service.record_case(
        db,
        item=_Item(),
        subject="math",
        grade_band="hs",
        decision={"action": "false_positive", "scope": "fingerprint"},
    )
    assert cid
    assert store.count_caselaw(db, kind="misjudgment") == 1
    assert store.count_caselaw(db, kind="solution") == 0


def test_record_case_correct_writes_solution(db):
    cid = service.record_case(
        db,
        item=_Item(suspect_type="MATH_RENDER_FAILED"),
        subject="math",
        grade_band="hs",
        decision={"action": "correct", "old": "x \\leqslant 1", "new": "x \\leq 1"},
    )
    assert cid
    assert store.count_caselaw(db, kind="solution") == 1


def test_record_case_confirm_is_noop(db):
    cid = service.record_case(
        db, item=_Item(), subject="math", grade_band="hs", decision={"action": "confirm"}
    )
    assert cid is None
    assert store.count_caselaw(db) == 0


# ── enrich：默认/未激活 → 原样返回 ─────────────────────────────────────────


def test_enrich_findings_noop_when_disabled(db):
    findings = [{"suspect_type": "MATH_FALLBACK", "severity": "low", "evidence": {}, "candidates": []}]
    out = service.enrich_findings(db, findings, subject="math", grade_band="hs")
    assert out == findings  # 未开启开关：零变更


def test_enrich_findings_noop_below_threshold(db, monkeypatch):
    # 开启开关但判例数不足门槛 → 仍原样返回
    monkeypatch.setattr("app.config.settings.import35b_caselaw_enabled", True)
    monkeypatch.setattr("app.config.settings.import35b_caselaw_min_cases", 100)
    findings = [{"suspect_type": "X", "severity": "high", "evidence": {}, "candidates": []}]
    out = service.enrich_findings(db, findings, subject="math", grade_band="hs")
    assert out[0]["severity"] == "high"


# ── 向量层：无 Milvus 时降级 no-op ─────────────────────────────────────────


def test_vec_store_degrades_without_milvus(monkeypatch):
    monkeypatch.setattr(vec_store, "_pymilvus", lambda: None)
    assert vec_store.available() is False
    assert vec_store.index_case(
        case_id="c1", kind="misjudgment", suspect_type="X",
        subject="math", grade_band="hs", fingerprint="fp", text="t",
    ) is False
    assert vec_store.search_cases("t", kind="misjudgment", subject="math", grade_band="hs") == []
    assert vec_store.count() == 0


# ── 离线归纳 + 晋级门 ───────────────────────────────────────────────────────


def _seed_misjudgment(db, fp: str, n: int):
    for i in range(n):
        store.add_caselaw(
            db, kind="misjudgment", suspect_type="MATH_FALLBACK", subject="math",
            grade_band="hs", fingerprint=fp, case_text=f"c{i}",
            decision={"action": "false_positive"},
        )


def _seed_solution(db, fp: str, n: int, old: str, new: str):
    for i in range(n):
        store.add_caselaw(
            db, kind="solution", suspect_type="MATH_RENDER_FAILED", subject="math",
            grade_band="hs", fingerprint=fp, case_text=f"s{i}",
            decision={"action": "correct", "old": old, "new": new},
        )


def test_mine_proposals_suppression_from_repeated_misjudgment(db):
    _seed_misjudgment(db, "fp-mis", 3)
    proposals = induction.mine_proposals(db, min_occurrences=3)
    supp = [p for p in proposals if p.kind == "suppression"]
    assert len(supp) == 1
    assert supp[0].fingerprint == "fp-mis"
    assert supp[0].occurrences == 3
    assert supp[0].formalizable is True
    assert supp[0].conflict is False
    assert supp[0].target_slot == induction.SLOT_SUPPRESSIONS
    assert "import35b_suppressions" in supp[0].action_hint


def test_repair_rule_command_class_goes_to_repair_rules_json(db):
    # 命令类：new 里出现未白名单、首字母 b/f/n/r/t 的命令 \boxed → 进抽屉B
    _seed_solution(db, "fp-cmd", 3, "x", "\\boxed{x}")
    proposals = induction.mine_proposals(db, min_occurrences=3)
    rr = [p for p in proposals if p.kind == "repair_rule" and p.fingerprint == "fp-cmd"]
    assert rr and rr[0].target_slot == induction.SLOT_REPAIR_RULES
    assert rr[0].formalizable is True
    assert "boxed" in rr[0].detail["add_commands"]


def test_repair_rule_content_class_is_advisory_only(db):
    # 内容类：\leq 已在白名单，无新增命令 → 无执行槽，仅建议
    _seed_solution(db, "fp-content", 4, "x \\leqslant 1", "x \\leq 1")
    proposals = induction.mine_proposals(db, min_occurrences=3)
    rr = [p for p in proposals if p.kind == "repair_rule" and p.fingerprint == "fp-content"]
    assert rr and rr[0].target_slot == induction.SLOT_OVERRIDE_ONLY
    assert rr[0].formalizable is False


def test_mine_proposals_repair_rule_inconsistent_not_formalizable(db):
    store.add_caselaw(db, kind="solution", suspect_type="T", subject="math", grade_band="hs",
                      fingerprint="fp-x", case_text="a", decision={"action": "correct", "old": "a", "new": "b"})
    store.add_caselaw(db, kind="solution", suspect_type="T", subject="math", grade_band="hs",
                      fingerprint="fp-x", case_text="a", decision={"action": "correct", "old": "a", "new": "c"})
    store.add_caselaw(db, kind="solution", suspect_type="T", subject="math", grade_band="hs",
                      fingerprint="fp-x", case_text="a", decision={"action": "correct", "old": "a", "new": "d"})
    proposals = induction.mine_proposals(db, min_occurrences=3)
    rr = [p for p in proposals if p.kind == "repair_rule" and p.fingerprint == "fp-x"]
    assert rr and rr[0].formalizable is False


def test_conflict_blocks_suppression(db):
    # 同指纹既在误判库又在方案库 → suppression 冲突，不可晋级
    _seed_misjudgment(db, "fp-both", 3)
    _seed_solution(db, "fp-both", 3, "a", "b")
    proposals = induction.mine_proposals(db, min_occurrences=3)
    supp = [p for p in proposals if p.kind == "suppression" and p.fingerprint == "fp-both"]
    assert supp and supp[0].conflict is True


def test_golden_gate_passes_on_baseline():
    gate = induction.golden_gate()
    assert gate.golden_total > 0
    assert gate.passed, gate.golden_failures


def test_build_report_conflict_goes_to_blocked(db):
    _seed_misjudgment(db, "fp-both", 3)
    _seed_solution(db, "fp-both", 3, "a", "b")
    report = induction.build_report(db, min_occurrences=3)
    # 冲突的 suppression 必须在 blocked，不在 promotable
    promotable_supp = [p for p in report["promotable"] if p["kind"] == "suppression"]
    assert all(p["fingerprint"] != "fp-both" for p in promotable_supp)
