"""检测器运维面：绑定、误报记账、晋级门。

晋级门是这条线上最容易被绕过的地方——「看起来像规律」和「够格写进规则文件」
之间差着五道门，所以每一道都单独立个用例钉住。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base, CorpusImprovementPattern, CorpusImprovementPoint
from app.services import corpus_symptom_ops as ops
from app.services import improvement_points as ip_svc
from app.services.corpus_symptoms import enrich_findings, load_checks


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _accept(session: Session, *, job: str, page: int, fp: str, suspect: str) -> str:
    out = ip_svc.confirm_improvement(
        session,
        job_id=job,
        page_no=page,
        summary=f"{suspect} on page {page}",
        fingerprint_hash=fp,
        authority="edition",
        procedure={"steps": [{"tool": "corpus.patch_block", "args_summary": "fix"}]},
        pattern_status="none",
    )
    row = session.get(CorpusImprovementPoint, out["ip"]["id"])
    ip_svc._set_prep(row, ip_svc.PREP_READY)
    session.flush()
    pat = ip_svc.set_pattern(
        session,
        row.id,
        pattern_status="draft",
        abstract_text=suspect,
        suspect_type=suspect,
    )
    return str(pat.get("pattern_id") or "")


def test_bind_detector_rejects_unknown_declaration(session: Session) -> None:
    pid = _accept(session, job="j1", page=3, fp="bookA", suspect="ASIDE_IN_MAIN")
    with pytest.raises(ValueError, match="检测器声明未加载"):
        ops.bind_detector(
            session,
            pattern_id=pid,
            suspect_type="ASIDE_IN_MAIN",
            detector_ref="no_such_file.yaml",
        )


def test_bind_detector_rejects_playbook_without_steps(session: Session) -> None:
    pid = _accept(session, job="j1", page=3, fp="bookA", suspect="ASIDE_IN_MAIN")
    with pytest.raises(ValueError, match="手册步骤"):
        ops.bind_detector(
            session,
            pattern_id=pid,
            suspect_type="ASIDE_IN_MAIN",
            remediation_kind="playbook",
        )


def test_one_symptom_cannot_own_two_patterns(session: Session) -> None:
    first = _accept(session, job="j1", page=3, fp="bookA", suspect="ASIDE_IN_MAIN")
    other = _accept(session, job="j1", page=4, fp="bookA", suspect="HEADER_IN_MAIN")
    assert first != other
    with pytest.raises(ValueError, match="已经绑在模式"):
        ops.bind_detector(session, pattern_id=other, suspect_type="ASIDE_IN_MAIN")


def test_false_positive_downgrades_but_never_hides(session: Session) -> None:
    _accept(session, job="j1", page=3, fp="bookA", suspect="ASIDE_IN_MAIN")
    ops.report_false_positive(
        session,
        suspect_type="ASIDE_IN_MAIN",
        fingerprint="fp-1",
        page_no=3,
        note="这一处本来就是正文",
    )
    findings = enrich_findings(
        session,
        [
            {
                "suspect_type": "ASIDE_IN_MAIN",
                "fingerprint": "fp-1",
                "severity": "high",
                "page_no": 3,
            }
        ],
    )
    assert len(findings) == 1, "误报只降权，不许静默丢弃"
    assert findings[0]["severity"] == "medium"
    assert findings[0]["false_positive_hits"] == 1
    assert any("疑似历史误报" in c for c in findings[0]["candidates"])


def test_promotion_blocked_on_single_book(session: Session) -> None:
    pid = _accept(session, job="j1", page=3, fp="bookA", suspect="ASIDE_IN_MAIN")
    _accept(session, job="j1", page=5, fp="bookA", suspect="ASIDE_IN_MAIN")
    _accept(session, job="j1", page=7, fp="bookA", suspect="ASIDE_IN_MAIN")
    out = ops.promotion_check(session, pid)
    assert out["promotable"] is False
    repro = next(g for g in out["gates"] if g["gate"] == "复现足够")
    assert repro["passed"] is False, "三次都在同一本书上，只能算巧合"
    assert "人工批准" in out["proposal"] or "未过的门" in out["proposal"]


def test_promotion_needs_detector_and_landing(session: Session) -> None:
    pid = _accept(session, job="j1", page=3, fp="bookA", suspect="ASIDE_IN_MAIN")
    _accept(session, job="j2", page=5, fp="bookB", suspect="ASIDE_IN_MAIN")
    _accept(session, job="j3", page=7, fp="bookC", suspect="ASIDE_IN_MAIN")
    out = ops.promotion_check(session, pid)
    assert next(g for g in out["gates"] if g["gate"] == "复现足够")["passed"] is True
    assert next(g for g in out["gates"] if g["gate"] == "可形式化")["passed"] is False
    assert next(g for g in out["gates"] if g["gate"] == "正反例全绿")["passed"] is False
    assert out["promotable"] is False


def test_promotion_flags_inconsistent_fix_actions(session: Session) -> None:
    pid = _accept(session, job="j1", page=3, fp="bookA", suspect="ASIDE_IN_MAIN")
    _accept(session, job="j2", page=5, fp="bookB", suspect="ASIDE_IN_MAIN")
    member = (
        session.query(CorpusImprovementPoint)
        .filter(CorpusImprovementPoint.page_no == 5)
        .one()
    )
    member.procedure_json = (
        '{"steps": [{"tool": "corpus.rebuild_ir", "args_summary": "layout only"}]}'
    )
    session.flush()
    out = ops.promotion_check(session, pid)
    consistent = next(g for g in out["gates"] if g["gate"] == "修复动作一致")
    assert consistent["passed"] is False
    assert "corpus.rebuild_ir" in consistent["detail"]


def test_hits_come_from_detection_not_from_human_verdicts(session: Session) -> None:
    """误报率的分母只能是检测器命中数：人工判定不许自己给自己加分母。"""
    pid = _accept(session, job="j1", page=3, fp="bookA", suspect="ASIDE_IN_MAIN")
    findings = [
        {"suspect_type": "ASIDE_IN_MAIN", "fingerprint": "fp-1", "page_no": 3},
        {"suspect_type": "ASIDE_IN_MAIN", "fingerprint": "fp-2", "page_no": 5},
    ]
    enrich_findings(session, list(findings))  # 读接口富化：不计数
    assert session.get(CorpusImprovementPattern, pid).hits == 0

    enrich_findings(session, list(findings), count_hits=True)  # 导入真检测：计数
    assert session.get(CorpusImprovementPattern, pid).hits == 2

    ops.report_false_positive(
        session, suspect_type="ASIDE_IN_MAIN", fingerprint="fp-1", page_no=3
    )
    ops.confirm_true_positive(session, suspect_type="ASIDE_IN_MAIN")
    pat = session.get(CorpusImprovementPattern, pid)
    assert pat.hits == 2, "人工反馈不该改命中数"
    assert (pat.false_positive, pat.true_positive) == (1, 1)


def test_promotion_check_never_writes_rule_files(session: Session, tmp_path) -> None:
    """判断可以自动，落笔必须经人：晋级检查不得碰任何规则文件。"""
    pid = _accept(session, job="j1", page=3, fp="bookA", suspect="ASIDE_IN_MAIN")
    before = session.get(CorpusImprovementPattern, pid).landed_ref
    out = ops.promotion_check(session, pid)
    assert "proposal" in out
    assert session.get(CorpusImprovementPattern, pid).landed_ref == before
    assert session.get(CorpusImprovementPattern, pid).status != "landed"


def _declared_suspects() -> list[str]:
    return [c.suspect_type for c in load_checks()]


@pytest.mark.parametrize("suspect", _declared_suspects())
def test_declared_detector_passes_its_own_examples(suspect: str) -> None:
    """词从镜像搬到挂载目录之后，18 条现存检测器的判定不许有一条变。"""
    out = ops.run_examples(suspect_type=suspect)
    assert out["passed"], f"{suspect}: {out.get('detail') or out}"


def test_checks_overview_exposes_the_vocabulary_surface(session: Session) -> None:
    """界面上要看得出这个词住在哪一层——挂载层改完即生效，镜像层要重建。"""
    out = ops.checks_overview(session=session)
    words = {w["name"]: w for w in out["vocabulary"]["words"]}
    assert words["zone"]["layer"] == "L0"
    assert words["has_math"]["layer"] == "L2"
    assert out["vocabulary"]["diagnostics"]["coverage"]["L2"]["total"] >= 1
    assert out["load_errors"] == []
    for check in out["checks"]:
        assert check["locate_by"], f"{check['suspect_type']} 没透出 locate_by"
