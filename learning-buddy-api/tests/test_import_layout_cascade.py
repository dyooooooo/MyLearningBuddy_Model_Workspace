"""Import finalize: no 35B refine; escalate list is for Cursor SDK."""
from __future__ import annotations

from app.services import import_ir_pipeline as pipe
from app.services.document_ir import DocumentBlock
from app.services.ir.layout import PageDecision


def test_refresh_requires_ir(monkeypatch):
    from unittest.mock import MagicMock

    import pytest

    job = MagicMock()
    job.id = "j"
    job.meta_json = "{}"
    monkeypatch.setattr(pipe, "get_job_ir", lambda _job: None)
    with pytest.raises(ValueError, match="no IR"):
        pipe.refresh_job_symptom_findings(job)


def test_merge_escalate_full_replace():
    meta: dict = {"layout_escalate_pages": [1, 2]}
    cascade = {
        "layout_escalate_pages": [29, 85],
        "layout_escalate_summary": {"29": {"score": 0.4, "low_keys": ["b0"]}},
        "page_refine_chunks": [],
        "refine_errors": [],
        "refine_prompt_version": "",
    }
    pipe._merge_escalate_into_meta(meta, cascade)
    assert meta["layout_escalate_pages"] == [29, 85]
    assert meta["layout_escalate_summary"]["29"]["score"] == 0.4
    # 没有任何命名症状时覆盖标记是 none —— 低置信页不等于已知症状。
    assert meta["layout_escalate_target"] == "none"


def test_merge_marks_coverage_from_symptom_findings():
    meta: dict = {}
    cascade = {
        "layout_escalate_pages": [],
        "layout_escalate_summary": {},
        "page_refine_chunks": [],
        "refine_errors": [],
        "refine_prompt_version": "",
        "symptom_findings": [
            {"suspect_type": "A", "page_no": 3, "playbook": "写规则"},
            {"suspect_type": "B", "page_no": 7},
        ],
    }
    pipe._merge_escalate_into_meta(meta, cascade)
    assert meta["layout_escalate_target"] == "mixed"
    assert meta["symptom_summary"]["known_pages"] == [3]
    assert meta["symptom_summary"]["unknown_pages"] == [7]


def test_partial_rebuild_keeps_symptoms_of_untouched_pages():
    meta: dict = {
        "symptom_findings": [
            {"suspect_type": "A", "page_no": 10, "pattern_id": "p1"},
            {"suspect_type": "A", "page_no": 20, "pattern_id": "p1"},
        ]
    }
    cascade = {
        "layout_escalate_pages": [],
        "layout_escalate_summary": {},
        "page_refine_chunks": [],
        "refine_errors": [],
        "refine_prompt_version": "",
        "symptom_findings": [],  # 页 20 修好了
    }
    pipe._merge_escalate_into_meta(meta, cascade, only_pages={20})
    assert [f["page_no"] for f in meta["symptom_findings"]] == [10]


def test_findings_are_mapped_through_a_real_session():
    """映射必须真的跑通：拿错 session 会被 except 吞成裸 findings，界面就全是「未知疑点」。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base, CorpusImprovementPattern

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        s.add(
            CorpusImprovementPattern(
                id="pat1",
                category="layout",
                suspect_type="HEADER_IN_MAIN",
                remediation_kind="playbook",
                landing_target="L1.layout_rule",
                member_count=3,
            )
        )
        s.commit()

    import app.db.session as db_session_mod

    original = db_session_mod.get_session_factory
    db_session_mod.get_session_factory = lambda: factory
    try:
        out = pipe._map_findings_to_patterns(
            [{"suspect_type": "HEADER_IN_MAIN", "page_no": 3, "fingerprint": "abc"}]
        )
    finally:
        db_session_mod.get_session_factory = original

    assert out[0]["pattern_id"] == "pat1"
    assert out[0]["match"] == "suspect_type"
    assert out[0]["remediation_kind"] == "playbook"


def test_merge_escalate_partial_rebuild_updates_only_touched_pages():
    meta: dict = {
        "layout_escalate_pages": [10, 20, 30],
        "layout_escalate_summary": {
            "10": {"score": 0.3, "low_keys": []},
            "20": {"score": 0.3, "low_keys": []},
            "30": {"score": 0.3, "low_keys": []},
        },
    }
    cascade = {
        "layout_escalate_pages": [],  # page 20 no longer escalates
        "layout_escalate_summary": {},
        "page_refine_chunks": [],
        "refine_errors": [],
        "refine_prompt_version": "",
    }
    pipe._merge_escalate_into_meta(meta, cascade, only_pages={20})
    assert meta["layout_escalate_pages"] == [10, 30]
    assert "20" not in meta["layout_escalate_summary"]


def test_finalize_records_escalate_without_refine(monkeypatch):
    calls: list[str] = []

    def boom(*_a, **_k):
        calls.append("refine")
        raise AssertionError("refine_page must not be called")

    monkeypatch.setattr(
        "app.services.ir.mineru_page_refine.refine_page", boom, raising=False
    )

    decision = PageDecision(
        page_no=1,
        score=0.4,
        escalate=True,
        low_confidence_keys=["b0"],
    )

    def fake_finalize(page_blocks, *, profile, page_w, page_h):
        return page_blocks, decision

    class _Cache:
        def page_size(self, _n):
            return 1000.0, 1414.0

        def close(self):
            return None

    monkeypatch.setattr(pipe, "_finalize_page_blocks", fake_finalize)
    monkeypatch.setattr(
        pipe, "load_textbook_profile", lambda _pid: {"edition_id": "hujiao_math_hs_v1"}
    )
    monkeypatch.setattr(pipe, "PdfRenderCache", lambda _b: _Cache())
    monkeypatch.setattr(
        pipe, "render_page_html", lambda *_a, **_k: "<div></div>"
    )

    blocks = [
        DocumentBlock(id="b0", type="paragraph", page_no=1, order=1, text="x"),
    ]
    out, meta = pipe._run_finalize_streaming(
        blocks,
        pdf_bytes=b"%PDF",
        profile_id="hujiao_math_hs_v1",
        page_html_cb=lambda *_a: None,
    )
    assert len(out) == 1
    assert meta["layout_escalate_pages"] == [1]
    assert "symptom_findings" in meta
    assert "refine" not in calls
