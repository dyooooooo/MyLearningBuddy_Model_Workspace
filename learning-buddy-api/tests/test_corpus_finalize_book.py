"""定稿 (finalize): keep one import of a book, delete the other versions."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    Base,
    CorpusBookEditionBind,
    CorpusBookFingerprint,
    CorpusCorrection,
    CorpusImportFeedback,
    CorpusImportJob,
    CorpusImprovementPoint,
    Edition,
    EditionDraft,
)
from app.services import corpus_admin as admin
from app.services import corpus_finalize as finalize
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


@pytest.fixture(autouse=True)
def no_side_effects(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Keep subtree deletion, object storage and Redis out of the unit test."""
    calls: dict[str, list] = {"nodes": [], "prefixes": []}

    def fake_delete_node(session, node_id, *, cascade=False, force=False):
        calls["nodes"].append((node_id, cascade, force))
        return {"deleted": [node_id], "count": 3}

    def fake_purge(prefix: str) -> int:
        calls["prefixes"].append(prefix)
        return 7

    monkeypatch.setattr(admin, "delete_node", fake_delete_node)
    monkeypatch.setattr(
        "app.clients.minio_client.delete_objects_by_prefix", fake_purge
    )
    monkeypatch.setattr("app.services.pipeline_telemetry.request_cancel", lambda *_: None)
    monkeypatch.setattr("app.services.pipeline_telemetry.clear_job_live", lambda *_: None)
    monkeypatch.setattr("app.clients.redis_client.purge_import_queue", lambda *_: 0)
    return calls


def _job(
    session: Session,
    job_id: str,
    *,
    fingerprint: str = "",
    sha1: str = "",
    label: str = "必修第二册",
    edition_id: str = "",
    status: str = "chunked",
) -> CorpusImportJob:
    meta: dict = {
        # A unique signature keeps the raw-PDF identity tier off object storage.
        "source_pdf": {
            "v": 2,
            "sha1": sha1 or f"sha-{job_id}",
            "size": 1,
            "page_count": 180,
            "samples": [],
        }
    }
    if fingerprint:
        meta["book_fingerprint"] = {"hash": fingerprint}
    job = CorpusImportJob(
        id=job_id,
        minio_key=f"corpus/imports/staging/{job_id}/book.pdf",
        textbook_node_id=f"textbook:{job_id}",
        subject="math",
        grade_band="hs",
        label=label,
        edition_id=edition_id,
        status=status,
        markdown="",
        meta_json=dumps(meta),
        created_at=f"2026-01-{job_id[-1]}0T00:00:00Z",
    )
    session.add(job)
    session.flush()
    return job


def _fingerprint(session: Session, fp_hash: str) -> None:
    session.add(
        CorpusBookFingerprint(id=f"fp-{fp_hash}", fingerprint_hash=fp_hash, page_count=180)
    )
    session.flush()


def _ledger(session: Session, fp_hash: str, job_id: str, count: int) -> None:
    for i in range(count):
        ip_id = f"ip-{job_id}-{i}"
        session.add(
            CorpusImprovementPoint(
                id=ip_id,
                fingerprint_hash=fp_hash,
                source_job_id=job_id,
                page_no=i + 1,
                status="accepted",
            )
        )
        session.add(
            CorpusCorrection(
                id=f"corr-{job_id}-{i}",
                fingerprint_hash=fp_hash,
                source_job_id=job_id,
                ip_id=ip_id,
                page_no=i + 1,
                after_json=dumps([{"id": "b1", "text": "fixed"}]),
            )
        )
    session.flush()


def test_preview_gathers_same_book_versions_only(session: Session) -> None:
    keep = _job(session, "keep1", fingerprint="fp-A")
    _fingerprint(session, "fp-A")
    _job(session, "loser1", fingerprint="fp-A")
    _job(session, "other1", fingerprint="fp-B", label="必修第一册")
    _fingerprint(session, "fp-B")

    report = finalize.preview_finalize(session, keep.id)

    assert [r["job_id"] for r in report["remove"]] == ["loser1"]
    assert report["remove"][0]["match"] == "fingerprint"
    assert report["warnings"] == []


def test_preview_warns_when_the_kept_version_has_fewer_improvements(
    session: Session,
) -> None:
    keep = _job(session, "keep1", fingerprint="fp-A")
    _fingerprint(session, "fp-A")
    _ledger(session, "fp-A", "keep1", 2)
    _job(session, "loser1", fingerprint="fp-B")
    _fingerprint(session, "fp-B")
    _ledger(session, "fp-B", "loser1", 9)

    # Same file, so identity holds even though the fingerprints differ.
    for jid in ("keep1", "loser1"):
        job = session.get(CorpusImportJob, jid)
        meta = loads(job.meta_json, {})
        meta["source_pdf"]["sha1"] = "same-pdf"
        job.meta_json = dumps(meta)
    session.flush()

    report = finalize.preview_finalize(session, keep.id)

    assert [r["job_id"] for r in report["remove"]] == ["loser1"]
    assert report["remove"][0]["match"] == "same_file"
    assert any("改进点比保留的这版多" in w for w in report["warnings"])


def test_apply_refuses_until_warnings_are_acknowledged(session: Session) -> None:
    keep = _job(session, "keep1", fingerprint="fp-A", status="uploaded")
    _fingerprint(session, "fp-A")

    with pytest.raises(ValueError, match="告警未确认"):
        finalize.finalize_book(session, keep.id, dry_run=False)

    # Dry run always answers, warnings and all.
    report = finalize.finalize_book(session, keep.id, dry_run=True)
    assert report["applied"] is False
    assert report["warnings"]


def test_finalize_deletes_the_other_versions_and_locks_the_keeper(
    session: Session, no_side_effects: dict[str, list]
) -> None:
    keep = _job(session, "keep1", fingerprint="fp-A")
    _fingerprint(session, "fp-A")
    _ledger(session, "fp-A", "keep1", 5)
    _job(session, "loser1", fingerprint="fp-B")
    _fingerprint(session, "fp-B")
    _ledger(session, "fp-B", "loser1", 3)
    session.add(CorpusImportFeedback(id="fb1", job_id="loser1", page_no=4))
    for jid in ("keep1", "loser1"):
        job = session.get(CorpusImportJob, jid)
        meta = loads(job.meta_json, {})
        meta["source_pdf"]["sha1"] = "same-pdf"
        job.meta_json = dumps(meta)
    session.flush()

    report = finalize.finalize_book(
        session, keep.id, dry_run=False, ack_warnings=True, note="校对完成"
    )

    assert report["applied"] is True
    assert session.get(CorpusImportJob, "loser1") is None
    assert session.get(CorpusImportJob, "keep1") is not None
    # The dead book's ledger and identity are gone; the kept book's survive.
    assert (
        session.query(CorpusImprovementPoint)
        .filter(CorpusImprovementPoint.fingerprint_hash == "fp-B")
        .count()
        == 0
    )
    assert (
        session.query(CorpusCorrection)
        .filter(CorpusCorrection.fingerprint_hash == "fp-B")
        .count()
        == 0
    )
    assert (
        session.query(CorpusBookFingerprint)
        .filter(CorpusBookFingerprint.fingerprint_hash == "fp-B")
        .count()
        == 0
    )
    assert (
        session.query(CorpusImprovementPoint)
        .filter(CorpusImprovementPoint.fingerprint_hash == "fp-A")
        .count()
        == 5
    )
    assert session.query(CorpusImportFeedback).count() == 0
    assert ("textbook:loser1", True, False) in no_side_effects["nodes"]
    assert "corpus/imports/staging/loser1/" in no_side_effects["prefixes"]

    record = finalize.finalized_record(session.get(CorpusImportJob, "keep1"))
    assert record["note"] == "校对完成"
    assert record["removed_jobs"] == ["loser1"]


def test_finalized_book_refuses_ordinary_deletion(session: Session) -> None:
    keep = _job(session, "keep1", fingerprint="fp-A")
    _fingerprint(session, "fp-A")
    finalize.finalize_book(session, keep.id, dry_run=False, ack_warnings=True)

    with pytest.raises(ValueError, match="已定稿"):
        admin.delete_import_job(session, "keep1")
    locked = finalize.finalized_job_for_node(session, ["textbook:keep1", "chunk:x"])
    assert locked is not None and locked.id == "keep1"

    finalize.unfinalize_book(session, "keep1")
    assert finalize.finalized_job_for_node(session, ["textbook:keep1"]) is None


def test_a_shared_staging_folder_is_never_purged(session: Session) -> None:
    keep = _job(session, "keep1", fingerprint="fp-A")
    loser = _job(session, "loser1", fingerprint="fp-A")
    loser.minio_key = keep.minio_key  # re-import of the same staged upload
    session.flush()

    report = finalize.preview_finalize(session, keep.id)

    assert report["remove"][0]["minio_prefix"] == ""


def test_deleting_a_job_takes_its_objects_with_it(
    session: Session, no_side_effects: dict[str, list]
) -> None:
    """删任务要把它那份 PDF 和图片一起删掉，否则对象存储会一直攒垃圾。"""
    job = _job(session, "solo1", fingerprint="fp-A")

    out = admin.delete_import_job(session, job.id)

    assert out["objects_removed"] == 7
    assert no_side_effects["prefixes"] == ["corpus/imports/staging/solo1/"]


def test_deleting_one_of_two_imports_keeps_the_shared_upload(
    session: Session, no_side_effects: dict[str, list]
) -> None:
    """两次导入共用同一份上传目录时，删其中一次不能动那份 PDF。"""
    keep = _job(session, "keep1", fingerprint="fp-A")
    loser = _job(session, "loser1", fingerprint="fp-A")
    loser.minio_key = keep.minio_key
    session.flush()

    out = admin.delete_import_job(session, loser.id)

    assert out["objects_removed"] == 0
    assert no_side_effects["prefixes"] == []


def test_only_orphaned_book_forks_are_dropped(session: Session) -> None:
    keep = _job(session, "keep1", fingerprint="fp-A", edition_id="hujiao_base")
    _fingerprint(session, "fp-A")
    _job(session, "loser1", fingerprint="fp-B", edition_id="fork_dead")
    _job(session, "loser2", fingerprint="fp-C", edition_id="hujiao_base")
    _fingerprint(session, "fp-B")
    _fingerprint(session, "fp-C")
    for jid in ("keep1", "loser1", "loser2"):
        job = session.get(CorpusImportJob, jid)
        meta = loads(job.meta_json, {})
        meta["source_pdf"]["sha1"] = "same-pdf"
        job.meta_json = dumps(meta)
    session.add(Edition(id="fork_dead", display_name="第二册专书", base_id="hujiao_base"))
    session.add(EditionDraft(edition_id="fork_dead", bundle_json="{}"))
    session.add(
        CorpusBookEditionBind(fingerprint_hash="fp-B", edition_id="fork_dead")
    )
    session.flush()

    report = finalize.finalize_book(
        session, keep.id, dry_run=False, ack_warnings=True
    )

    dropped = [e["edition_id"] for e in report["editions_to_drop"]]
    assert dropped == ["fork_dead"]
    assert session.get(Edition, "fork_dead") is None
    assert session.query(EditionDraft).count() == 0
