"""35B job purge: DB rows, MinIO prefixes, Redis queue, pageaudit mirror."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.import_35b import store
from app.import_35b.assets import job_minio_prefixes
from app.import_35b.purge import purge_pageaudit_mirror
from app.import_35b.service import purge_import_job


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


def test_job_minio_prefixes_includes_canonical_and_upload_dir():
    key = "import35b/abc123/书.pdf"
    prefixes = job_minio_prefixes(key, "job-uuid")
    assert "import35b/job-uuid/" in prefixes
    assert "import35b/abc123/" in prefixes


def test_purge_job_data_removes_rows_and_overrides_when_last_job(db):
    job = store.create_job(
        db,
        minio_key="import35b/u1/book.pdf",
        label="测试书",
        subject="math",
        grade_band="hs",
    )
    store.upsert_page(
        db,
        textbook_key=job.textbook_key,
        job_id=job.id,
        page_no=1,
        html="<p>1</p>",
    )
    store.save_review_items(
        db,
        job_id=job.id,
        textbook_key=job.textbook_key,
        page_no=1,
        findings=[
            {
                "block_id": "b1",
                "suspect_type": "X",
                "severity": "low",
                "fingerprint": "fp1",
                "fingerprint_exact": "fpe1",
            }
        ],
    )
    store.add_override(
        db,
        textbook_key=job.textbook_key,
        page_no=1,
        block_id="b1",
        target="latex",
        payload={"old": "a", "new": "b"},
    )
    store.add_caselaw(
        db,
        kind="misjudgment",
        suspect_type="X",
        subject="math",
        grade_band="hs",
        fingerprint="fp1",
        case_text="sample",
        decision={},
        job_id=job.id,
        page_no=1,
    )

    counts = store.purge_job_data(db, job)
    db.commit()

    assert counts["pages"] == 1
    assert counts["review_items"] == 1
    assert counts["caselaw"] == 1
    assert counts["overrides"] == 1
    assert store.get_job(db, job.id) is None
    assert store.count_pages(db, job.textbook_key) == 0


def test_purge_pageaudit_mirror_clears_latest_when_meta_matches(monkeypatch, tmp_path):
    root = tmp_path / "pageaudit"
    latest = root / "latest"
    latest.mkdir(parents=True)
    (latest / "job_meta.json").write_text(
        json.dumps({"job_id": "job-1", "label": "书"}),
        encoding="utf-8",
    )
    (latest / "p0001.html").write_text("<p>x</p>", encoding="utf-8")

    monkeypatch.setattr("app.config.settings.import35b_pageaudit_dir", str(root))
    out = purge_pageaudit_mirror("job-1")

    assert out["skipped"] is False
    assert out["removed_files"] == 2
    assert not (latest / "p0001.html").exists()
    assert (latest / "job_meta.json").exists() is False


def test_purge_import_job_calls_external_purge(monkeypatch, db):
    job = store.create_job(
        db,
        minio_key="import35b/u2/book.pdf",
        label="purge-me",
    )
    db.commit()

    calls: dict[str, list] = {"minio": [], "redis": [], "pageaudit": []}

    def _fake_minio(**kw):
        calls["minio"].append(kw)
        return 3

    monkeypatch.setattr(
        "app.import_35b.purge.purge_minio_for_job",
        lambda **kw: _fake_minio(**kw),
    )
    monkeypatch.setattr(
        "app.import_35b.purge.purge_import35b_queue",
        lambda job_id: calls["redis"].append(job_id) or 1,
    )
    monkeypatch.setattr(
        "app.import_35b.purge.caselaw_vec_store.delete_case_vectors",
        lambda ids: 0,
    )
    monkeypatch.setattr(
        "app.import_35b.purge.purge_pageaudit_mirror",
        lambda job_id: calls["pageaudit"].append(job_id) or {"removed_files": 0},
    )

    result = purge_import_job(db, job.id)
    assert result is not None
    assert result["deleted"] is True
    assert store.get_job(db, job.id) is None
    assert calls["redis"] == [job.id]
    assert calls["pageaudit"] == [job.id]
    assert calls["minio"][0]["job_id"] == job.id
