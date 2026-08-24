"""Improvement Point converge / confirm / procedure gates."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    Base,
    CorpusCorrection,
    CorpusImprovementLink,
    CorpusImprovementPoint,
    EditionCorrectionQueue,
)
from app.services import improvement_points as ip_svc


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


def test_confirm_requires_procedure_or_attempts(session: Session) -> None:
    with pytest.raises(ValueError, match="procedure"):
        ip_svc.confirm_improvement(
            session,
            job_id="job1",
            page_no=3,
            summary="fix figure",
            attempts=[],
            procedure={"steps": []},
        )


def test_confirm_supersedes_peers_and_writes_correction(session: Session) -> None:
    ip_svc.ingest_attempts(
        session,
        job_id="job1",
        fingerprint_hash="fpabc",
        attempts=[
            {
                "id": "a1",
                "page": 3,
                "op": "patch_block",
                "tool": "corpus.patch_block",
                "summary": "swap figures wrong",
                "block_ids": ["b1", "b2"],
            },
            {
                "id": "a2",
                "page": 3,
                "op": "patch_block",
                "tool": "corpus.patch_block",
                "summary": "swap figures correct",
                "block_ids": ["b1", "b2"],
            },
        ],
    )
    out = ip_svc.confirm_improvement(
        session,
        job_id="job1",
        fingerprint_hash="fpabc",
        page_no=3,
        summary="swap figures correct",
        category="figure",
        block_ids=["b1", "b2"],
        attempts=[
            {
                "id": "a2",
                "page": 3,
                "tool": "corpus.patch_block",
                "args_summary": "swap b1/b2",
                "summary": "swap figures correct",
                "block_ids": ["b1", "b2"],
            }
        ],
        pattern_status="none",
    )
    assert out["ok"]
    assert out["ip"]["status"] == "accepted"
    assert out["ip"]["procedure"]["steps"]
    assert out["superseded_count"] >= 1
    corr = session.query(CorpusCorrection).filter_by(ip_id=out["ip"]["id"]).one()
    assert corr.fingerprint_hash == "fpabc"
    assert corr.procedure_json


def test_has_success_procedure_and_bulk_purge(session: Session) -> None:
    assert ip_svc.has_success_procedure(
        {"steps": [{"tool": "patch_block", "args_summary": "fix title"}]}
    )
    assert not ip_svc.has_success_procedure({"steps": []})
    assert not ip_svc.has_success_procedure(
        {"steps": [{"tool": "unknown", "args_summary": ""}]}
    )
    assert ip_svc.has_success_procedure(None, after_blocks=[{"id": "b1"}])

    bad = CorpusImprovementPoint(
        fingerprint_hash="fp-bad",
        source_job_id="job-bad",
        locus_key="page:1:cat:other:slot:x",
        page_no=1,
        category="other",
        status="accepted",
        claim_json='{"summary":"no path"}',
        evidence_json="{}",
        procedure_json='{"steps":[]}',
        concrete_json="{}",
        pattern_status="none",
        summary="无路径应删",
        origin="user_ask",
        created_at="t",
        updated_at="t",
    )
    session.add(bad)
    session.flush()
    out = ip_svc.purge_ips_without_success_procedure(session)
    session.commit()
    assert out["purged_count"] == 1
    assert session.get(CorpusImprovementPoint, bad.id) is None


def test_purge_removes_ip_and_all_residuals(session: Session) -> None:
    out = ip_svc.confirm_improvement(
        session,
        job_id="job-purge",
        fingerprint_hash="fp-purge",
        page_no=1,
        summary="封面：删除噪点",
        category="title",
        attempts=[
            {
                "id": "a-purge",
                "page": 1,
                "tool": "corpus.patch_block",
                "args_summary": "drop noise",
                "summary": "封面：删除噪点",
            }
        ],
        pattern_status="none",
    )
    ip_id = out["ip"]["id"]
    assert session.query(CorpusCorrection).filter_by(ip_id=ip_id).count() == 1
    session.add(
        EditionCorrectionQueue(
            edition_id="ed1",
            source="ai_import",
            source_ref=ip_id,
            candidate_json=f'{{"ip_id": "{ip_id}", "summary": "封面：删除噪点"}}',
            status="pending",
            created_at="t",
            updated_at="t",
        )
    )
    session.add(
        CorpusImprovementLink(
            from_id=ip_id,
            to_id="other",
            rel="same_locus",
            created_at="t",
        )
    )
    peer = CorpusImprovementPoint(
        fingerprint_hash="fp-purge",
        source_job_id="job-purge",
        locus_key="page:1:cat:title:slot:x",
        page_no=1,
        category="title",
        status="superseded",
        claim_json="{}",
        evidence_json="{}",
        procedure_json="{}",
        concrete_json="{}",
        superseded_by=ip_id,
        pattern_status="none",
        created_at="t",
        updated_at="t",
    )
    session.add(peer)
    session.flush()

    purged = ip_svc.purge_improvement_point(session, ip_id)
    session.commit()
    assert purged["ok"] is True
    assert purged["purged"]["corrections"] == 1
    assert purged["purged"]["edition_queue"] >= 1
    assert purged["purged"]["links"] >= 1
    assert purged["purged"]["cleared_superseded_by"] >= 1
    assert session.get(CorpusImprovementPoint, ip_id) is None
    assert session.query(CorpusCorrection).filter_by(ip_id=ip_id).count() == 0
    assert (
        session.query(EditionCorrectionQueue).filter_by(source_ref=ip_id).count() == 0
    )
    assert (
        session.query(CorpusImprovementLink)
        .filter(
            (CorpusImprovementLink.from_id == ip_id)
            | (CorpusImprovementLink.to_id == ip_id)
        )
        .count()
        == 0
    )
    session.refresh(peer)
    assert peer.superseded_by == ""
    assert (
        session.query(CorpusImprovementPoint)
        .filter(CorpusImprovementPoint.superseded_by == ip_id)
        .count()
        == 0
    )


def test_converge_blocks_on_missing_procedure(session: Session) -> None:
    row = CorpusImprovementPoint(
        fingerprint_hash="fp1",
        source_job_id="j1",
        locus_key="page:1:cat:other:slot:page",
        page_no=1,
        category="other",
        status="candidate",
        claim_json='{"summary":"x"}',
        evidence_json='{"attempts":[]}',
        procedure_json="{}",
        concrete_json="{}",
        pattern_status="none",
        created_at="t",
        updated_at="t",
    )
    session.add(row)
    session.flush()
    out = ip_svc.converge_job(session, job_id="j1")
    assert out["ok"] is False
    assert out["conflicts"]
    assert out["conflicts"][0].get("reason") == "missing_procedure"


def test_bind_book_edition(session: Session) -> None:
    out = ip_svc.bind_book_edition(session, "fpxyz", "ed_book2", source_job_id="j9")
    assert out["edition_id"] == "ed_book2"
    got = ip_svc.get_book_edition_bind(session, "fpxyz")
    assert got and got["edition_id"] == "ed_book2"


def test_same_locus_merge_via_confirm(session: Session) -> None:
    for i, summary in enumerate(["bad1", "bad2", "good"], start=1):
        ip_svc.ingest_attempts(
            session,
            job_id="j2",
            fingerprint_hash="fp2",
            attempts=[
                {
                    "id": f"x{i}",
                    "page": 10,
                    "tool": "corpus.patch_block",
                    "summary": summary,
                    "category": "math",
                }
            ],
        )
    out = ip_svc.confirm_improvement(
        session,
        job_id="j2",
        fingerprint_hash="fp2",
        page_no=10,
        category="math",
        summary="good",
        attempts=[
            {
                "id": "x3",
                "page": 10,
                "tool": "corpus.patch_block",
                "args_summary": "fix latex",
                "summary": "good",
            }
        ],
    )
    accepted = (
        session.query(CorpusImprovementPoint)
        .filter_by(source_job_id="j2", status="accepted")
        .all()
    )
    assert len(accepted) == 1
    superseded = (
        session.query(CorpusImprovementPoint)
        .filter_by(source_job_id="j2", status="superseded")
        .count()
    )
    assert superseded >= 1
    assert out["ip"]["id"] == accepted[0].id


def test_confirm_absorbs_same_text_twin_on_other_locus(session: Session) -> None:
    """同页同文但定位键不同（ingest 走 slot:page，confirm 走 slot:corr）不得留成双写。"""
    ip_svc.ingest_attempts(
        session,
        job_id="j3",
        fingerprint_hash="fp3",
        attempts=[
            {
                "id": "t1",
                "page": 7,
                "tool": "corpus.patch_block",
                "summary": "换算框改为两行独立公式",
                "category": "math",
            }
        ],
    )
    ip_svc.confirm_improvement(
        session,
        job_id="j3",
        fingerprint_hash="fp3",
        page_no=7,
        category="math",
        # 标点不同但实质同文，且定位键落在 corr 槽位
        summary="换算框改为两行独立公式。",
        concrete={"correction_id": "c-7-abc"},
        attempts=[
            {
                "id": "t2",
                "page": 7,
                "tool": "corpus.patch_block",
                "args_summary": "split into two display formulas",
                "summary": "换算框改为两行独立公式。",
            }
        ],
    )
    rows = (
        session.query(CorpusImprovementPoint).filter_by(source_job_id="j3").all()
    )
    live = [r for r in rows if r.status not in ("superseded", "rejected")]
    assert len(live) == 1, f"同文双写没被吸收: {[(r.status, r.locus_key) for r in rows]}"
    twin = next(r for r in rows if r.status == "superseded")
    assert twin.superseded_by == live[0].id


def test_origin_stored_at_write_time(session: Session) -> None:
    """来源按会话映射写入 claim，读取时归一到闭集（非靠摘要关键词猜）。"""
    ip_svc.ingest_attempts(
        session,
        job_id="j4",
        session_id="cursor-ide-migration",
        fingerprint_hash="fp4",
        attempts=[
            {
                "id": "m1",
                "page": 2,
                "tool": "corpus.patch_block",
                # 摘要里没有任何「历史补丁」字样，靠关键词猜会误判
                "summary": "封面删除跑道牌噪点",
                "category": "figure",
            }
        ],
    )
    row = session.query(CorpusImprovementPoint).filter_by(source_job_id="j4").one()
    claim = ip_svc.loads(row.claim_json, {})
    assert claim.get("origin") == "migrated_l3"
    out = ip_svc._ip_out(row)
    # 对外闭集：migrated_l3 → user_ask
    assert out["origin"] == "user_ask"
    assert out["origin_label"] == "用户让 AI 改"


def test_infer_category_scores_instead_of_first_hit() -> None:
    # caption 同时属于 figure 与 table 的关键词；靠首次命中会把表题全判成 figure。
    assert ip_svc.infer_category("表格 caption 未与表体对齐") == "table"
    assert ip_svc.infer_category("图 2-1 的 caption 丢失") == "figure"
    # 「表格标题」同时命中 table 与 title，平局按 _CAT_HINTS 声明序取 table。
    assert ip_svc.infer_category("表格标题错位") == "table"


def test_infer_category_keeps_unambiguous_cases() -> None:
    assert ip_svc.infer_category("删除装饰插图") == "figure"
    assert ip_svc.infer_category("公式里的 CJK 字符混进 latex") == "math"
    assert ip_svc.infer_category("右侧旁注未落进侧栏") == "layout"
    assert ip_svc.infer_category("页眉未恢复") == "header"
    assert ip_svc.infer_category("选项网格排布错乱") == "exercise"
    assert ip_svc.infer_category("例题被误判成 heading") == "title"
    assert ip_svc.infer_category("说不清的问题") == "other"


def _accept(session: Session, *, job: str, page: int, summary: str, fp: str) -> str:
    out = ip_svc.confirm_improvement(
        session,
        job_id=job,
        page_no=page,
        summary=summary,
        fingerprint_hash=fp,
        authority="edition",
        procedure={"steps": [{"tool": "corpus.patch_block", "args_summary": summary}]},
        pattern_status="none",
    )
    ip_id = out["ip"]["id"]
    row = session.get(CorpusImprovementPoint, ip_id)
    ip_svc._set_prep(row, ip_svc.PREP_READY)
    session.flush()
    pat = ip_svc.set_pattern(
        session,
        ip_id,
        pattern_status="draft",
        abstract_text=summary,
    )
    return str(pat.get("pattern_id") or row.pattern_id or "")


def test_new_pattern_leaves_landing_target_undecided(session: Session) -> None:
    """类目能自动推，生效面不能。

    同属 layout，「旁注误入正文」是 zone 规则，「缺图未恢复」只能留专书补丁。
    按类目猜出来的生效面从没被确认过，填上去只会让待定路由的清单假性清空。
    """
    pid = _accept(session, job="j1", page=3, summary="右侧旁注未落进侧栏", fp="fpA")
    pat = ip_svc.get_pattern(session, pid)
    assert pat["category"] == "layout"
    assert pat["landing_target"] == ""
    assert pat["landing_note"] == ""


def _accept_with_symptom(
    session: Session, *, job: str, page: int, summary: str, fp: str, suspect: str
) -> str:
    out = ip_svc.confirm_improvement(
        session,
        job_id=job,
        page_no=page,
        summary=summary,
        fingerprint_hash=fp,
        authority="edition",
        procedure={"steps": [{"tool": "corpus.patch_block", "args_summary": summary}]},
        pattern_status="none",
    )
    row = session.get(CorpusImprovementPoint, out["ip"]["id"])
    ip_svc._set_prep(row, ip_svc.PREP_READY)
    session.flush()
    pat = ip_svc.set_pattern(
        session, row.id, pattern_status="draft", abstract_text=summary, suspect_type=suspect
    )
    return str(pat.get("pattern_id") or "")


def test_symptom_name_groups_differently_worded_points(session: Session) -> None:
    """摘要写法不同不该分家 —— 症状名才是归并的锚点。"""
    first = _accept_with_symptom(
        session, job="j1", page=3, summary="右侧旁注未落进侧栏",
        fp="bookA", suspect="ASIDE_IN_MAIN",
    )
    second = _accept_with_symptom(
        session, job="j2", page=8, summary="边上那段提示掉正文里了",
        fp="bookB", suspect="ASIDE_IN_MAIN",
    )
    assert first == second
    pat = ip_svc.get_pattern(session, first)
    assert pat["suspect_type"] == "ASIDE_IN_MAIN"
    assert pat["member_count"] == 2
    # 跨了两本书才够格占用人工定路由的注意力。
    assert pat["book_count"] == 2
    assert pat["status"] == "candidate"


def test_member_count_recomputed_not_incremented(session: Session) -> None:
    """计数从真实成员重算：拆走成员后源模式的数要跟着降。"""
    keep = _accept(session, job="j1", page=3, summary="右侧旁注未落进侧栏", fp="fpA")
    other = _accept(session, job="j1", page=9, summary="左边的旁注掉进正文了", fp="fpA")
    ip_svc.merge_patterns(session, keep_id=keep, merge_ids=[other])
    assert ip_svc.get_pattern(session, keep)["member_count"] == 2
    moved = [
        m["ip_id"] for m in ip_svc.get_pattern(session, keep)["members"] if m["page_no"] == 9
    ]
    ip_svc.split_pattern(
        session, pattern_id=keep, member_ip_ids=moved, abstract_text="左侧旁注落栏"
    )
    assert ip_svc.get_pattern(session, keep)["member_count"] == 1


def test_single_book_pattern_stays_draft(session: Session) -> None:
    pid = _accept_with_symptom(
        session, job="j1", page=3, summary="右侧旁注未落进侧栏",
        fp="bookA", suspect="ASIDE_IN_MAIN",
    )
    _accept_with_symptom(
        session, job="j1", page=5, summary="右侧旁注未落进侧栏",
        fp="bookA", suspect="ASIDE_IN_MAIN",
    )
    pat = ip_svc.get_pattern(session, pid)
    assert pat["book_count"] == 1
    assert pat["status"] == "draft"


def test_route_then_land_writes_landed_ref(session: Session) -> None:
    pid = _accept(session, job="j1", page=3, summary="右侧旁注未落进侧栏", fp="fpA")
    ip_svc.route_pattern(session, pid, landing_target="L1.layout_rule", note="zone 可表达")
    pat = ip_svc.get_pattern(session, pid)
    assert pat["status"] == "routed"
    assert pat["target_layer"] == "L1"
    out = ip_svc.land_pattern(session, pid, landed_ref="hujiao.right-margin-tip-rescue")
    assert out["pattern"]["status"] == "landed"
    assert out["pattern"]["landed_ref"] == "hujiao.right-margin-tip-rescue"


def test_route_rejects_unknown_target(session: Session) -> None:
    pid = _accept(session, job="j1", page=3, summary="右侧旁注未落进侧栏", fp="fpA")
    with pytest.raises(ValueError, match="unknown landing_target"):
        ip_svc.route_pattern(session, pid, landing_target="L9.nowhere")


def test_book_patch_is_terminal_and_cannot_land(session: Session) -> None:
    pid = _accept(session, job="j1", page=7, summary="第7页缺图未恢复", fp="fpA")
    ip_svc.route_pattern(session, pid, landing_target="L3.book_patch", note="图资源缺失")
    assert ip_svc.get_pattern(session, pid)["status"] == "no_landing"
    with pytest.raises(ValueError, match="do not land"):
        ip_svc.land_pattern(session, pid, landed_ref="whatever")


def test_land_requires_route_first(session: Session) -> None:
    pid = _accept(session, job="j1", page=3, summary="说不清的问题", fp="fpA")
    pat = session.get(ip_svc.CorpusImprovementPattern, pid)
    pat.landing_target = ""
    session.flush()
    with pytest.raises(ValueError, match="call route first"):
        ip_svc.land_pattern(session, pid, landed_ref="x")


def test_merge_patterns_repoints_members(session: Session) -> None:
    keep = _accept(session, job="j1", page=3, summary="右侧旁注未落进侧栏", fp="fpA")
    other = _accept(session, job="j1", page=9, summary="左边的旁注掉进正文了", fp="fpA")
    assert keep != other
    out = ip_svc.merge_patterns(session, keep_id=keep, merge_ids=[other], note="同一条规律")
    assert out["moved_members"] == 1
    assert out["pattern"]["member_count"] == 2
    assert sorted(out["pattern"]["member_pages"]) == [3, 9]
    assert session.get(ip_svc.CorpusImprovementPattern, other) is None


def test_cross_book_recurrence_marks_candidate(session: Session) -> None:
    pid = _accept(session, job="j1", page=3, summary="右侧旁注未落进侧栏", fp="fpA")
    assert ip_svc.get_pattern(session, pid)["status"] == "draft"
    # 同一条规律在第二本书复现 → 值得占用人工定路由的注意力
    same = _accept(session, job="j2", page=5, summary="右侧旁注未落进侧栏", fp="fpB")
    assert same == pid
    assert ip_svc.get_pattern(session, pid)["status"] == "candidate"


def test_split_pattern_moves_members_out(session: Session) -> None:
    keep = _accept(session, job="j1", page=3, summary="右侧旁注未落进侧栏", fp="fpA")
    other = _accept(session, job="j1", page=9, summary="左边的旁注掉进正文了", fp="fpA")
    ip_svc.merge_patterns(session, keep_id=keep, merge_ids=[other])
    moved = ip_svc.get_pattern(session, keep)["members"]
    p9 = [m["ip_id"] for m in moved if m["page_no"] == 9]
    out = ip_svc.split_pattern(
        session,
        pattern_id=keep,
        member_ip_ids=p9,
        abstract_text="左侧旁注落栏",
        note="左右两侧判据不同",
    )
    assert out["source"]["member_count"] == 1
    assert out["created"]["member_count"] == 1
    assert out["created"]["member_pages"] == [9]
    assert out["created"]["landing_target"] == ""


def test_split_refuses_to_empty_the_source(session: Session) -> None:
    pid = _accept(session, job="j1", page=3, summary="右侧旁注未落进侧栏", fp="fpA")
    only = ip_svc.get_pattern(session, pid)["members"][0]["ip_id"]
    with pytest.raises(ValueError, match="every member"):
        ip_svc.split_pattern(session, pattern_id=pid, member_ip_ids=[only], abstract_text="x")


def _confirm_simple(
    session: Session,
    *,
    summary: str,
    page: int = 3,
    fp: str = "fpPrep",
    job: str = "jobPrep",
    procedure: dict | None = None,
) -> dict:
    return ip_svc.confirm_improvement(
        session,
        job_id=job,
        page_no=page,
        summary=summary,
        fingerprint_hash=fp,
        authority="edition",
        procedure=procedure
        or {
            "steps": [{"tool": "corpus.patch_block", "args_summary": summary[:80]}]
        },
        pattern_status="none",
    )


def _multi_step_procedure(*args_summaries: str) -> dict:
    return {
        "steps": [
            {"tool": "corpus.patch_block", "args_summary": s}
            for s in args_summaries
        ]
    }


def test_list_ip_books_groups_orphan_fingerprints(session: Session) -> None:
    a = _confirm_simple(session, summary="甲书一条", fp="fpBookA", job="jobA")
    _confirm_simple(session, summary="乙书一条", fp="fpBookB", job="jobB")
    _confirm_simple(session, summary="乙书二条", fp="fpBookB", job="jobB2")
    out = ip_svc.list_ip_books(session)
    assert out["total"] == 3
    by_fp = {b["fingerprint_hash"]: b["count"] for b in out["books"]}
    assert by_fp["fpBookA"] == 1
    assert by_fp["fpBookB"] == 2
    # 点某书指纹不应混入其它书
    listed = ip_svc.list_improvement_points(session, fingerprint_hash="fpBookA")
    assert listed["counts"]["total"] == 1
    assert listed["items"][0]["id"] == a["ip"]["id"]


def test_confirm_always_needs_triage(session: Session) -> None:
    out = _confirm_simple(
        session,
        summary="删除页眉噪声；同时修正侧栏落位；并且加宽表格；另外隐藏废图",
    )
    assert out["ip"]["prep_status"] == "needs_triage"
    assert out["ip"]["needs_split"] is False
    assert out["ip"]["prep_status_label"] == "待整理"


def test_triage_apply_split_priority_and_ready(session: Session) -> None:
    a = _confirm_simple(session, summary="单主题修正页眉", page=1, job="t1")
    b = _confirm_simple(
        session,
        summary="删除噪声；修正侧栏",
        page=2,
        job="t2",
        procedure=_multi_step_procedure("删除噪声", "修正侧栏"),
    )
    # c 复合摘要：同时进 split 与 aggregate → 优先待拆分
    c = _confirm_simple(
        session,
        summary="删除本页侧栏旁注；并收紧页边距",
        page=3,
        job="t3",
        procedure=_multi_step_procedure("删除本页侧栏旁注", "收紧页边距"),
    )
    d = _confirm_simple(session, summary="保留本页侧栏旁注", page=3, job="t4")
    a_id, b_id, c_id, d_id = (
        a["ip"]["id"],
        b["ip"]["id"],
        c["ip"]["id"],
        d["ip"]["id"],
    )
    out = ip_svc.triage_apply(
        session,
        fingerprint_hash="fpPrep",
        ready_ids=[a_id],
        split_ids=[b_id, c_id],
        aggregate_groups=[
            {"member_ids": [c_id, d_id], "reason": "同页删/留对立"},
        ],
    )
    assert a_id in out["applied"]["ready"]
    assert b_id in out["applied"]["split"]
    assert c_id in out["applied"]["split"]
    assert c_id not in out["applied"]["aggregate"]
    # c 被优先拆走后，簇不足 2 人 → d 仍待整理（不单条标聚合）
    assert d_id not in out["applied"]["aggregate"]
    assert session.get(CorpusImprovementPoint, a_id).prep_status == "ready"
    assert session.get(CorpusImprovementPoint, b_id).prep_status == "needs_split"
    assert session.get(CorpusImprovementPoint, c_id).prep_status == "needs_split"
    assert session.get(CorpusImprovementPoint, d_id).prep_status == "needs_triage"

    # 独立聚合簇仍可落库
    e = _confirm_simple(session, summary="删除本页插图", page=4, job="t5")
    f = _confirm_simple(session, summary="保留本页插图", page=4, job="t6")
    e_id, f_id = e["ip"]["id"], f["ip"]["id"]
    out2 = ip_svc.triage_apply(
        session,
        fingerprint_hash="fpPrep",
        aggregate_groups=[{"member_ids": [e_id, f_id], "reason": "删/留对立"}],
    )
    assert e_id in out2["applied"]["aggregate"]
    assert f_id in out2["applied"]["aggregate"]
    assert session.get(CorpusImprovementPoint, e_id).prep_status == "needs_aggregate"


def test_triage_apply_accepts_enumerated_batch_as_split(session: Session) -> None:
    """迁移回填的批量条目只有一步，但摘要用顿号记了多件事，应能标待拆分。"""
    out = _confirm_simple(
        session,
        summary="必修二第8批：复数标题、例4解出array、图注归位、删装饰插图",
        job="t8",
    )
    ip_id = out["ip"]["id"]
    applied = ip_svc.triage_apply(session, fingerprint_hash="fpPrep", split_ids=[ip_id])
    assert ip_id in applied["applied"]["split"]
    assert session.get(CorpusImprovementPoint, ip_id).prep_status == "needs_split"


def test_triage_apply_keeps_single_topic_enumeration_out_of_split(
    session: Session,
) -> None:
    """单一主题里的顿号（删A、B 两图）不算多件事。"""
    out = _confirm_simple(session, summary="删除装饰插图A、B", job="t9")
    ip_id = out["ip"]["id"]
    applied = ip_svc.triage_apply(session, fingerprint_hash="fpPrep", split_ids=[ip_id])
    assert ip_id in applied["applied"]["skipped_triage"]


def test_triage_apply_rejects_obvious_single_as_split(session: Session) -> None:
    out = _confirm_simple(session, summary="只改页眉对齐", job="t5")
    ip_id = out["ip"]["id"]
    applied = ip_svc.triage_apply(
        session, fingerprint_hash="fpPrep", split_ids=[ip_id]
    )
    assert ip_id in applied["applied"]["skipped_triage"]
    assert session.get(CorpusImprovementPoint, ip_id).prep_status == "needs_triage"


def test_split_apply_purges_source_and_creates_children(session: Session) -> None:
    parts = ["删除页眉噪声", "修正侧栏落位", "加宽本页表格"]
    out = _confirm_simple(
        session,
        summary="；".join(parts),
        procedure=_multi_step_procedure(*parts),
    )
    src_id = out["ip"]["id"]
    ip_svc._set_prep(session.get(CorpusImprovementPoint, src_id), ip_svc.PREP_NEEDS_SPLIT)
    session.flush()
    prop = ip_svc.split_propose(session, src_id)
    assert prop.get("ok") is True
    assert len(prop["proposals"]) >= 2
    applied = ip_svc.split_apply(session, src_id, children=prop["proposals"])
    assert session.get(CorpusImprovementPoint, src_id) is None
    assert len(applied["new_ids"]) >= 2
    for i, nid in enumerate(applied["new_ids"]):
        child = session.get(CorpusImprovementPoint, nid)
        assert child is not None
        assert child.prep_status == "needs_triage"
        cout = ip_svc._ip_out(child)
        lineage = cout.get("lineage") or []
        assert lineage, "拆分子条须有改进历程"
        ev = lineage[0]
        assert ev.get("op") == "split"
        assert "父条" in str(ev.get("summary") or "") or "拆出" in str(
            ev.get("summary") or ""
        )
        parent = ev.get("parent") or {}
        assert parent.get("id") == src_id
        assert str(parent.get("summary") or "").strip()
        assert int((ev.get("meta") or {}).get("split_index", -1)) == i
        assert set((ev.get("meta") or {}).get("sibling_ids") or []) == set(
            applied["new_ids"]
        )


def test_split_apply_rejects_empty_children_no_proc_fallback(session: Session) -> None:
    parts = ["删除页眉噪声", "修正侧栏落位"]
    out = _confirm_simple(
        session,
        summary="；".join(parts),
        procedure=_multi_step_procedure(*parts),
    )
    src_id = out["ip"]["id"]
    ip_svc._set_prep(session.get(CorpusImprovementPoint, src_id), ip_svc.PREP_NEEDS_SPLIT)
    session.flush()
    with pytest.raises(ValueError, match="完备拆分子条"):
        ip_svc.split_apply(session, src_id, children=None)


def test_split_apply_rejects_overlapping_after(session: Session) -> None:
    parts = ["删噪声 block-a", "改落栏 block-b"]
    out = _confirm_simple(
        session,
        summary="；".join(parts),
        procedure=_multi_step_procedure(*parts),
    )
    src_id = out["ip"]["id"]
    row = session.get(CorpusImprovementPoint, src_id)
    concrete = {
        "after": [
            {"id": "block-a", "text": "a"},
            {"id": "block-b", "text": "b"},
        ]
    }
    row.concrete_json = ip_svc.dumps(concrete)
    ip_svc._set_prep(row, ip_svc.PREP_NEEDS_SPLIT)
    session.flush()
    with pytest.raises(ValueError, match="重叠|不完备|after"):
        ip_svc.split_apply(
            session,
            src_id,
            children=[
                {
                    "summary": parts[0],
                    "step_indices": [0],
                    "after_indices": [0],
                },
                {
                    "summary": parts[1],
                    "step_indices": [1],
                    "after_indices": [0],  # 重叠
                },
            ],
        )


def test_conflict_resolve_keeps_ready_and_purges_loser(session: Session) -> None:
    a = _confirm_simple(session, summary="删除本页侧栏旁注", page=8, job="jc1")
    b = _confirm_simple(session, summary="保留本页侧栏旁注", page=8, job="jc2")
    a_id, b_id = a["ip"]["id"], b["ip"]["id"]
    a_row = session.get(CorpusImprovementPoint, a_id)
    b_row = session.get(CorpusImprovementPoint, b_id)
    ip_svc._set_prep(a_row, ip_svc.PREP_NEEDS_AGGREGATE)
    ip_svc._set_prep(b_row, ip_svc.PREP_NEEDS_AGGREGATE)
    # 写入簇元数据，供历程 meta 使用
    ac = ip_svc.loads(a_row.concrete_json, {})
    ac["aggregate_group_id"] = "g-test"
    ac["aggregate_reason"] = "删/留对立"
    a_row.concrete_json = ip_svc.dumps(ac)
    ip_svc._link_conflicts(session, a_id, b_id)
    session.flush()
    resolved = ip_svc.resolve_prep_conflicts(
        session, keep_ids=[a_id], delete_ids=[b_id]
    )
    assert b_id in resolved["deleted_ids"]
    assert session.get(CorpusImprovementPoint, b_id) is None
    kept = session.get(CorpusImprovementPoint, a_id)
    assert kept is not None
    assert kept.prep_status == "ready"
    kout = ip_svc._ip_out(kept)
    lineage = kout.get("lineage") or []
    assert lineage, "聚合保留条须有改进历程"
    ev = lineage[-1]
    assert ev.get("op") == "aggregate_keep"
    assert "删除" in str(ev.get("summary") or "")
    peers = ev.get("peers") or []
    assert any(str(p.get("id") or "") == b_id for p in peers if isinstance(p, dict))
    assert any(
        "保留本页侧栏旁注" in str(p.get("summary") or "")
        for p in peers
        if isinstance(p, dict)
    )


def test_resolve_lineage_weak_backfill_from_split_from() -> None:
    events = ip_svc._resolve_lineage(
        {"split_from": "dead-parent-id", "split_index": 1},
        {},
    )
    assert len(events) == 1
    assert events[0].get("weak") is True
    assert "不可用" in str(events[0].get("summary") or "")
    assert (events[0].get("parent") or {}).get("id") == "dead-parent-id"


def test_set_pattern_rejects_non_ready_prep(session: Session) -> None:
    out = _confirm_simple(
        session,
        summary="复合改动：删噪声；改落栏；加宽表",
    )
    ip_id = out["ip"]["id"]
    assert out["ip"]["prep_status"] == "needs_triage"
    with pytest.raises(ip_svc.PrepGateError, match="完成体"):
        ip_svc.set_pattern(session, ip_id, pattern_status="draft", abstract_text="x")
