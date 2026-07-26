"""Tests for import35b user feedback store CRUD."""
from __future__ import annotations

import pytest

from app.import_35b import store


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


def test_feedback_crud_and_purge(db):
    job = store.create_job(
        db,
        minio_key="import35b/u1/book.pdf",
        label="测试书",
        subject="math",
        grade_band="hs",
    )
    row = store.create_feedback(
        db,
        job_id=job.id,
        page_no=3,
        textbook_key=job.textbook_key,
        label=job.label,
        body="主栏空白",
        tags=["layout", "blank_page"],
        debug_prefix="import35b/x/assets/35b/debug/p0003",
        t1_snapshot=[{"suspect_type": "MAIN_EMPTY", "severity": "high"}],
    )
    db.commit()
    fid = row.id

    listed = store.list_feedback(db, job_id=job.id)
    assert len(listed) == 1
    assert listed[0].id == row.id
    assert listed[0].status == "open"

    d = store.feedback_to_dict(row)
    assert d["body"] == "主栏空白"
    assert d["tags"] == ["layout", "blank_page"]
    assert d["t1_snapshot"][0]["suspect_type"] == "MAIN_EMPTY"

    store.set_feedback_status(db, row, status="ack", pack_path="data/cursor-packs/x")
    db.commit()
    got = store.get_feedback(db, row.id)
    assert got is not None
    assert got.status == "ack"
    assert got.pack_path == "data/cursor-packs/x"

    counts = store.purge_job_data(db, job)
    db.commit()
    assert counts["feedback"] == 1
    assert store.get_feedback(db, fid) is None
