"""Same-book re-import: book identity, adopt (认亲), page inheritance, guards."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    Base,
    CorpusBookFingerprint,
    CorpusCorrection,
    CorpusImportJob,
    CorpusImprovementPoint,
)
from app.services import book_identity as identity
from app.services import corpus_corrections as cc
from app.services import corpus_page_inherit as inherit
from app.services.document_ir import DocumentBlock
from app.util.json_util import dumps, loads


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


def _job(session: Session, job_id: str, *, meta: dict | None = None) -> CorpusImportJob:
    meta = dict(meta or {})
    # Seed a unique source-PDF signature so identity fallbacks never reach object
    # storage; tests that exercise the fallback pass a real signature instead.
    meta.setdefault(
        "source_pdf",
        {"v": 2, "sha1": f"sha-{job_id}", "size": 1, "page_count": 0, "samples": []},
    )
    job = CorpusImportJob(
        id=job_id,
        minio_key=f"corpus/imports/staging/{job_id}/book.pdf",
        subject="math",
        grade_band="hs",
        label="必修第二册",
        status="parsed",
        markdown="",
        meta_json=dumps(meta or {}),
    )
    session.add(job)
    session.flush()
    return job


def _blocks(pages: dict[int, list[str]]) -> list[DocumentBlock]:
    out: list[DocumentBlock] = []
    for page_no, texts in sorted(pages.items()):
        for order, text in enumerate(texts):
            out.append(
                DocumentBlock(
                    id=f"p{page_no}b{order}",
                    type="paragraph",
                    page_no=page_no,
                    order=order,
                    text=text,
                )
            )
    return out


# --- book identity ----------------------------------------------------------


def test_page_count_follows_ir_not_stale_meta(session: Session) -> None:
    """A frozen meta total_pages must not shrink the book's identity."""
    job = _job(session, "j1", meta={"total_pages": 20})
    blocks = _blocks({p: [f"第 {p} 页正文"] for p in range(1, 61)})

    feats = cc.extract_book_features(job, blocks)

    assert feats["page_count"] == 60
    assert feats["page_span"] == 60
    assert feats["meta_page_count"] == 20


def test_soft_match_tolerates_page_span_drift(session: Session) -> None:
    old_blocks = _blocks({p: [f"目录 第{p}章 三角"] for p in range(1, 179)})
    old_job = _job(session, "old")
    old_fp = cc.upsert_fingerprint(session, old_job, old_blocks)

    # Re-parse trims 4 pages but keeps the same first page / TOC lines.
    new_blocks = _blocks({p: [f"目录 第{p}章 三角"] for p in range(1, 175)})
    new_job = _job(session, "new")

    row, kind = cc.find_matching_fingerprint(session, new_job, new_blocks)

    assert row is not None
    assert row.fingerprint_hash == old_fp.fingerprint_hash
    assert kind in ("soft_toc", "soft_first_page")


def test_soft_match_rejects_far_page_span(session: Session) -> None:
    old_blocks = _blocks({p: ["普通高中教科书 数学必修 第二册"] for p in range(1, 21)})
    old_job = _job(session, "old")
    cc.upsert_fingerprint(session, old_job, old_blocks)

    new_blocks = _blocks({p: ["普通高中教科书 数学必修 第二册"] for p in range(1, 172)})
    new_job = _job(session, "new")

    row, kind = cc.find_matching_fingerprint(session, new_job, new_blocks)

    assert row is None
    assert kind == "none"


def test_adopt_fingerprint_pins_identity(session: Session) -> None:
    old_blocks = _blocks({p: ["旧书正文"] for p in range(1, 21)})
    old_job = _job(session, "old")
    old_fp = cc.upsert_fingerprint(session, old_job, old_blocks)

    new_blocks = _blocks({p: ["新解析正文"] for p in range(1, 172)})
    new_job = _job(session, "new")
    assert cc.find_matching_fingerprint(session, new_job, new_blocks)[1] == "none"

    out = cc.adopt_book_fingerprint(
        session, new_job, old_fp.fingerprint_hash, note="第二册重导"
    )

    assert out["adopted"]["hash"] == old_fp.fingerprint_hash
    row, kind = cc.find_matching_fingerprint(session, new_job, new_blocks)
    assert kind == "override"
    assert row is not None and row.fingerprint_hash == old_fp.fingerprint_hash


def test_adopt_unknown_fingerprint_rejected(session: Session) -> None:
    job = _job(session, "j1")
    with pytest.raises(ValueError, match="unknown book fingerprint"):
        cc.adopt_book_fingerprint(session, job, "nope")


# --- replay guards / ledger stats -------------------------------------------


def test_partial_page_snapshot_is_blocked() -> None:
    # A one-block "after" must not replace a seven-block page.
    assert cc.page_replace_is_safe(1, 7) is False
    assert cc.page_replace_is_safe(6, 7) is True
    # Genuinely short pages (cover / chapter opener) stay replaceable.
    assert cc.page_replace_is_safe(1, 3) is True


def test_ledger_stats_counts_replayable_only(session: Session) -> None:
    fp = "fp-book"
    session.add(
        CorpusBookFingerprint(
            fingerprint_hash=fp,
            subject="math",
            grade_band="hs",
            page_count=20,
            toc_hash="t",
            first_page_hash="f",
            pdf_bytes_size=0,
            label_hint="",
            features_json="{}",
            created_at="now",
            updated_at="now",
        )
    )
    live = CorpusImprovementPoint(
        fingerprint_hash=fp,
        source_job_id="old",
        locus_key="l1",
        page_no=8,
        category="layout",
        authority="edition",
        status="accepted",
        claim_json="{}",
        evidence_json="{}",
        procedure_json="{}",
        concrete_json="{}",
        summary="章名归主栏",
        created_at="now",
        updated_at="now",
    )
    session.add(live)
    session.flush()
    session.add_all(
        [
            CorpusCorrection(
                fingerprint_id="",
                fingerprint_hash=fp,
                scope="page",
                page_no=8,
                block_id="",
                user_note="有块快照",
                before_json="[]",
                after_json=dumps([{"id": "b1", "type": "paragraph", "page_no": 8}]),
                refine_result_json="{}",
                source_job_id="old",
                ip_id=live.id,
                procedure_json="{}",
                created_at="now",
                updated_at="now",
            ),
            CorpusCorrection(
                fingerprint_id="",
                fingerprint_hash=fp,
                scope="page",
                page_no=9,
                block_id="",
                user_note="只有文字摘要",
                before_json="[]",
                after_json="[]",
                refine_result_json="{}",
                source_job_id="old",
                ip_id=live.id,
                procedure_json="{}",
                created_at="now",
                updated_at="now",
            ),
            CorpusCorrection(
                fingerprint_id="",
                fingerprint_hash=fp,
                scope="page",
                page_no=10,
                block_id="",
                user_note="改进点已被删",
                before_json="[]",
                after_json=dumps([{"id": "b2", "type": "paragraph", "page_no": 10}]),
                refine_result_json="{}",
                source_job_id="old",
                ip_id="gone",
                procedure_json="{}",
                created_at="now",
                updated_at="now",
            ),
        ]
    )
    session.flush()

    stats = cc.correction_ledger_stats(session, fp)

    assert stats["corrections"] == 3
    assert stats["with_after"] == 2
    assert stats["dangling"] == 1
    assert stats["replayable"] == 1
    assert stats["replayable_pages"] == [8]


# --- page inheritance -------------------------------------------------------


def test_improved_pages_come_from_ips_and_patches(session: Session) -> None:
    job = _job(session, "old", meta={"book_fingerprint": {"hash": "fp-book"}})
    session.add(
        CorpusImprovementPoint(
            fingerprint_hash="fp-book",
            source_job_id="old",
            locus_key="l1",
            page_no=8,
            category="layout",
            authority="edition",
            status="accepted",
            claim_json="{}",
            evidence_json="{}",
            procedure_json="{}",
            concrete_json="{}",
            summary="s",
            created_at="now",
            updated_at="now",
        )
    )
    session.add(
        CorpusCorrection(
            fingerprint_id="",
            fingerprint_hash="fp-book",
            scope="page",
            page_no=11,
            block_id="",
            user_note="n",
            before_json="[]",
            after_json="[]",
            refine_result_json="{}",
            source_job_id="old",
            ip_id="x",
            procedure_json="{}",
            created_at="now",
            updated_at="now",
        )
    )
    session.flush()

    assert inherit.improved_pages_for_job(session, job) == [8, 11]


def _patch_block_store(monkeypatch, mapping: dict[str, list[DocumentBlock]]) -> dict:
    saved: dict[str, list[DocumentBlock]] = {}
    import app.services.import_ir_store as store

    monkeypatch.setattr(store, "load_blocks", lambda job: mapping.get(job.id))
    monkeypatch.setattr(
        store,
        "save_blocks",
        lambda job, blocks: saved.__setitem__(job.id, list(blocks)) or "blocks.json",
    )
    return saved


def test_inherit_dry_run_aligns_shifted_pages(session: Session, monkeypatch) -> None:
    old_job = _job(session, "old", meta={"book_fingerprint": {"hash": "fp-book"}})
    new_job = _job(session, "new")
    old_blocks = _blocks(
        {
            8: ["第6章 三角", "本章导语：三角恒等变换"],
            9: ["直角三角形 ABC 中，∠C = 90°", "公式表保持不动"],
        }
    )
    # Same content one page earlier in the new parse.
    new_blocks = _blocks(
        {
            7: ["第6章 三角", "本章导语：三角恒等变变"],
            8: ["直角三角形ABC中∠C=90°与∠A粘连", "公式表保持不动"],
            9: ["完全无关的另一页内容"],
        }
    )
    _patch_block_store(monkeypatch, {"old": old_blocks, "new": new_blocks})

    report = inherit.inherit_pages_from_job(
        session,
        old_job_id=old_job.id,
        new_job_id=new_job.id,
        pages=[8, 9],
        dry_run=True,
    )

    assert report["dry_run"] is True
    assert report["applied_pages"] == []
    assert [(m["old_page"], m["new_page"]) for m in report["matched"]] == [(8, 7), (9, 8)]
    assert report["unmatched"] == []
    assert report["low_confidence"] == []


def test_blank_cover_page_is_low_confidence_until_opted_in(
    session: Session, monkeypatch
) -> None:
    """Image-only covers have no text to align, so they need an explicit opt-in."""
    old_job = _job(session, "old", meta={"book_fingerprint": {"hash": "fp-book"}})
    new_job = _job(session, "new")
    old_blocks = [
        DocumentBlock(id="cover-old", type="figure", page_no=1, order=0, caption=""),
        *_blocks({2: ["正文完全一致的第二页内容，用于确定偏移量"]}),
    ]
    new_blocks = [
        DocumentBlock(id="cover-new", type="figure", page_no=1, order=0, caption=""),
        *_blocks({2: ["正文完全一致的第二页内容，用于确定偏移量"]}),
    ]
    _patch_block_store(monkeypatch, {"old": old_blocks, "new": new_blocks})

    report = inherit.inherit_pages_from_job(
        session,
        old_job_id=old_job.id,
        new_job_id=new_job.id,
        pages=[1, 2],
        dry_run=True,
    )
    assert [m["new_page"] for m in report["matched"]] == [2]
    assert [(lc["old_page"], lc["reason"]) for lc in report["low_confidence"]] == [
        (1, "blank_page_text")
    ]

    opted_in = inherit.inherit_pages_from_job(
        session,
        old_job_id=old_job.id,
        new_job_id=new_job.id,
        pages=[1, 2],
        dry_run=True,
        include_low_confidence=True,
    )
    assert [m["new_page"] for m in opted_in["matched"]] == [1, 2]
    assert opted_in["low_confidence"] == []


def test_inherit_apply_replaces_target_pages(session: Session, monkeypatch) -> None:
    old_job = _job(session, "old", meta={"book_fingerprint": {"hash": "fp-book"}})
    new_job = _job(session, "new", meta={"total_pages": 3})
    old_blocks = _blocks({2: ["修好的第二页 甲", "修好的第二页 乙"]})
    new_blocks = _blocks({1: ["第一页"], 2: ["修好的第二页 甲 但是坏了"], 3: ["第三页"]})
    saved = _patch_block_store(monkeypatch, {"old": old_blocks, "new": new_blocks})

    import app.services.corpus_import_incremental as inc
    import app.services.document_ir as doc_ir

    rendered: list[int] = []
    monkeypatch.setattr(
        doc_ir, "render_page_html", lambda blocks, page_no, profile_id="": f"<p>{page_no}</p>"
    )
    monkeypatch.setattr(
        inc,
        "upsert_page_draft_chunk",
        lambda session, job, **kw: rendered.append(int(kw["page_no"])),
    )

    report = inherit.inherit_pages_from_job(
        session,
        old_job_id=old_job.id,
        new_job_id=new_job.id,
        pages=[2],
        dry_run=False,
    )

    assert report["applied_pages"] == [2]
    assert rendered == [2]
    written = saved["new"]
    page2 = [b for b in written if b.page_no == 2]
    assert [b.text for b in page2] == ["修好的第二页 甲", "修好的第二页 乙"]
    assert {b.page_no for b in written} == {1, 2, 3}
    assert all(b.meta.get("inherited_from_page") == 2 for b in page2)


def test_inherit_refuses_to_shrink_a_page(session: Session, monkeypatch) -> None:
    """The old run is not uniformly better — an emptier old page must not win."""
    old_job = _job(session, "old", meta={"book_fingerprint": {"hash": "fp-book"}})
    new_job = _job(session, "new")
    shared = "封面 普通高中教科书 数学必修 第二册 上海教育出版社"
    old_blocks = _blocks({1: [shared]})
    new_blocks = _blocks({1: [shared, "国家教材委员会审核通过", "2020", "SHUXUE", "数学"]})
    _patch_block_store(monkeypatch, {"old": old_blocks, "new": new_blocks})

    report = inherit.inherit_pages_from_job(
        session,
        old_job_id=old_job.id,
        new_job_id=new_job.id,
        pages=[1],
        dry_run=True,
    )

    assert report["matched"] == []
    assert [(s["new_page"], s["reason"]) for s in report["skipped"]] == [
        (1, "would_shrink_page")
    ]


def test_inherit_rejects_same_job(session: Session) -> None:
    _job(session, "same")
    with pytest.raises(ValueError, match="must differ"):
        inherit.inherit_pages_from_job(
            session, old_job_id="same", new_job_id="same", dry_run=True
        )


def test_inherited_pages_leave_the_escalate_queue(session: Session, monkeypatch) -> None:
    """An inherited page was already reviewed — the agent shouldn't redo it."""
    old_job = _job(session, "old", meta={"book_fingerprint": {"hash": "fp-book"}})
    new_job = _job(
        session,
        "new",
        meta={
            "total_pages": 2,
            "layout_escalate_pages": [1, 2],
            "layout_escalate_summary": {"1": "低置信", "2": "低置信"},
        },
    )
    old_blocks = _blocks({2: ["修好的第二页 甲", "修好的第二页 乙"]})
    new_blocks = _blocks({1: ["第一页"], 2: ["修好的第二页 甲 但是坏了"]})
    _patch_block_store(monkeypatch, {"old": old_blocks, "new": new_blocks})

    import app.services.corpus_import_incremental as inc
    import app.services.document_ir as doc_ir

    monkeypatch.setattr(
        doc_ir, "render_page_html", lambda blocks, page_no, profile_id="": f"<p>{page_no}</p>"
    )
    monkeypatch.setattr(inc, "upsert_page_draft_chunk", lambda session, job, **kw: None)

    report = inherit.inherit_pages_from_job(
        session, old_job_id=old_job.id, new_job_id=new_job.id, pages=[2], dry_run=False
    )

    assert report["escalate_cleared"] == [2]
    meta = loads(new_job.meta_json, {})
    assert meta["layout_escalate_pages"] == [1]
    assert meta["layout_escalate_summary"] == {"1": "低置信"}


# --- pre-parse book identity (Studio "已导入过" 选择) ------------------------


_BODY = {
    # 两册教材正文完全不同（真实情况也是），封面与版式则几乎一样
    "vol2": (
        "空间几何体的表面积与体积：设正三棱锥底面边长为 a，侧棱长为 b，"
        "求其外接球半径；再讨论当 b 趋近于 a 时该半径的变化趋势，并给出证明过程。"
    ),
    "vol1": (
        "集合与常用逻辑用语：已知全集 U 与其子集 A、B，写出补集与交并集的运算律，"
        "并用韦恩图说明充分条件与必要条件的区别，最后判断命题的真假。"
    ),
}


def _page_text(page_no: int, *, book: str = "vol2") -> str:
    """Body page text, comfortably over MIN_SAMPLE_CHARS and book-specific."""
    return f"第 {page_no} 页 例 {page_no}：{_BODY[book]} 练习 {page_no}-{page_no + 1}。"


def _fake_pdf(
    *, pages: int, book: str = "vol2", cover: str = "普通高中教科书 数学"
) -> bytes:
    """Bytes whose stub extractor yields covers on 1..3 and body text after."""
    lines = [f"PAGE{p}::{cover}" for p in (1, 2, 3)]
    lines += [f"PAGE{p}::{_page_text(p, book=book)}" for p in range(4, pages + 1)]
    return ("\n".join(lines)).encode("utf-8")


@pytest.fixture()
def stub_pdf_extract(monkeypatch):
    """Parse the fake PDF bytes above instead of running pypdf."""
    import app.services.pdf_extract as pe

    class _Page:
        def __init__(self, page_no: int, text: str) -> None:
            self.page_no = page_no
            self.text = text

    class _Result:
        def __init__(self, pages: list[_Page]) -> None:
            self.pages = pages
            self.meta = {"page_count": len(pages)}

    def fake(data: bytes, *, filename: str = "") -> _Result:
        pages: list[_Page] = []
        for line in data.decode("utf-8", "ignore").splitlines():
            no, _, text = line.partition("::")
            pages.append(_Page(int(no.removeprefix("PAGE")), text))
        return _Result(pages)

    monkeypatch.setattr(pe, "extract_pdf_bytes", fake)
    return fake


def _job_with_pdf(session: Session, job_id: str, data: bytes) -> CorpusImportJob:
    return _job(
        session,
        job_id,
        meta={"source_pdf": identity.pdf_signature(data, filename=f"{job_id}.pdf")},
    )


def test_probe_recognises_the_same_pdf_file(session: Session, stub_pdf_extract) -> None:
    """Re-uploading the very same file must be recognised without a cloud parse."""
    data = _fake_pdf(pages=60)
    _job_with_pdf(session, "old", data)

    out = identity.probe_known_books(session, data, filename="book.pdf")

    assert [c["job_id"] for c in out["candidates"]] == ["old"]
    assert out["best"]["match"] == "same_file"


def test_identity_ignores_the_cover_and_front_matter(
    session: Session, stub_pdf_extract
) -> None:
    """Same series cover + same page count, different body = different book."""
    vol2 = _fake_pdf(pages=60, book="vol2")
    vol1 = _fake_pdf(pages=60, book="vol1")  # 同一套教材，封面完全一样
    _job_with_pdf(session, "vol1", vol1)

    out = identity.probe_known_books(session, vol2, filename="vol2.pdf")

    assert out["candidates"] == []
    # 取样页只来自正文：前 3 页（封面/扉页/版权页）从不参与判定
    assert min(out["sample_pages"]) > identity.SKIP_LEADING_PAGES
    assert len(out["sample_pages"]) >= 3


def test_body_page_evidence_survives_page_drift(session: Session, stub_pdf_extract) -> None:
    """A re-export that trims pages keeps the same body text → still one book."""
    old = _fake_pdf(pages=60)
    trimmed = _fake_pdf(pages=56)
    _job_with_pdf(session, "old", old)

    out = identity.probe_known_books(session, trimmed, filename="again.pdf")

    best = out["best"]
    assert best is not None
    assert best["match"] == "same_body_pages"
    assert best["shared_pages"] >= identity.MIN_SHARED_PAGES


def test_different_export_matches_on_page_similarity(stub_pdf_extract) -> None:
    """A different export breaks every page hash but not readability."""
    a = identity.pdf_signature(_fake_pdf(pages=40), filename="a.pdf")
    # 另一份导出件：每页尾部多了个页码水印 → 哈希全不命中，文本仍几乎一致
    b = identity.pdf_signature(
        _fake_pdf(pages=40).replace(b"\xe3\x80\x82", b"\xe3\x80\x82(\xe7\xbb\xad)"),
        filename="b.pdf",
    )

    verdict = identity.compare_signatures(a, b)

    assert verdict["shared_pages"] == 0
    assert verdict["kind"] == "similar_body_pages"
    assert verdict["similar_pages"] >= identity.MIN_SHARED_PAGES


def test_fingerprint_falls_back_to_the_source_pdf(
    session: Session, stub_pdf_extract
) -> None:
    """IR-derived identity can miss after rules/AI rewrote the pages; the raw PDF can't."""
    data = _fake_pdf(pages=60)
    old_job = _job_with_pdf(session, "old", data)
    old_fp = cc.upsert_fingerprint(
        session, old_job, _blocks({p: [f"旧解析 第{p}页"] for p in range(1, 61)})
    )

    # 新任务同一份 PDF，但 IR 目录/首页被规则改过 → 目录哈希与首页哈希都不同
    new_job = _job_with_pdf(session, "new", data)
    new_blocks = _blocks({p: [f"新解析 版式已重排 第{p}页"] for p in range(1, 61)})

    row, kind = cc.find_matching_fingerprint(session, new_job, new_blocks)

    assert row is not None
    assert row.fingerprint_hash == old_fp.fingerprint_hash
    assert kind == "soft_pdf_same_file"


def test_a_few_shared_body_pages_are_not_enough(stub_pdf_extract) -> None:
    """Shared exercises/appendix pages must not merge two different books."""
    base = identity.pdf_signature(_fake_pdf(pages=40, book="vol2"), filename="a.pdf")
    other = identity.pdf_signature(_fake_pdf(pages=40, book="vol1"), filename="b.pdf")
    # 人为塞进两页完全相同的正文（如公用附录），仍不足 MIN_SHARED_PAGES
    other["samples"] = base["samples"][:2] + other["samples"][2:]

    assert identity.compare_signatures(base, other)["kind"] == ""


def test_source_signature_is_cached_on_the_job(
    session: Session, monkeypatch, stub_pdf_extract
) -> None:
    """The old job's PDF is read from object storage once, then reused."""
    job = _job(session, "old")
    job.meta_json = dumps({})  # 没有缓存签名的老任务
    session.flush()
    calls: list[str] = []

    import app.clients.minio_client as minio

    monkeypatch.setattr(
        minio,
        "get_object_bytes",
        lambda key: (calls.append(key), _fake_pdf(pages=20))[1],
    )

    first = identity.source_pdf_signature(session, job)
    second = identity.source_pdf_signature(session, job)

    assert first["sha1"] == second["sha1"]
    assert len(calls) == 1
    assert loads(job.meta_json, {})["source_pdf"]["sha1"] == first["sha1"]


# --- pipeline hook ----------------------------------------------------------


def test_pipeline_inherits_only_when_the_job_asked_for_it(
    session: Session, monkeypatch
) -> None:
    from app.services import corpus_import

    seen: list[dict] = []
    monkeypatch.setattr(
        inherit,
        "inherit_pages_from_job",
        lambda session, **kw: seen.append(kw)
        or {"applied_count": 2, "applied_pages": [7, 8], "skipped_count": 0},
    )

    plain = _job(session, "plain")
    corpus_import._inherit_previous_pages(session, plain)
    assert seen == []

    asking = _job(session, "asking", meta={"inherit_from_job_id": "old"})
    corpus_import._inherit_previous_pages(session, asking)

    assert len(seen) == 1
    assert seen[0]["old_job_id"] == "old"
    assert seen[0]["dry_run"] is False
    assert seen[0]["commit"] is False
    assert loads(asking.meta_json, {})["inherit_result"]["applied_pages"] == [7, 8]
