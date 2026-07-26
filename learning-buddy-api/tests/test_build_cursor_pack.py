"""Tests for Cursor Repair Pack builder (mocked MinIO)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.import_35b import store
from app.import_35b.cursor_pack.builder import build_cursor_pack


@pytest.fixture
def db(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import models  # noqa: F401
    from app.db.models import Base

    monkeypatch.setattr("app.config.settings.buddy_data_dir", str(tmp_path))
    monkeypatch.setattr(
        "app.config.settings.import35b_cursor_pack_dir",
        str(tmp_path / "cursor-packs"),
    )
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.close()


def _fake_minio(debug_prefix: str) -> dict[str, bytes]:
    raw = {"page_no": 3, "blocks": [{"id": "p3_b1", "kind": "text", "content": {"text": {"paragraphs": ["hi"]}}}]}
    return {
        f"{debug_prefix}/raw_model.json": json.dumps(raw).encode(),
        f"{debug_prefix}/blueprint.json": json.dumps(raw).encode(),
        f"{debug_prefix}/debug.json": json.dumps({"findings": []}).encode(),
        f"{debug_prefix}/page.jpg": b"\xff\xd8\xff",
    }


def test_build_cursor_pack_writes_exec_plan(db, tmp_path):
    job = store.create_job(
        db,
        minio_key="import35b/u1/book.pdf",
        label="测试书",
        subject="math",
        grade_band="hs",
    )
    debug_prefix = "import35b/u1/assets/35b/debug/p0003"
    fb = store.create_feedback(
        db,
        job_id=job.id,
        page_no=3,
        textbook_key=job.textbook_key,
        label=job.label,
        body="主栏空白",
        debug_prefix=debug_prefix,
        input_page_key=f"{debug_prefix}/page.jpg",
        t1_snapshot=[{"suspect_type": "MAIN_EMPTY"}],
    )
    db.commit()

    blobs = _fake_minio(debug_prefix)

    def _get(key: str) -> bytes | None:
        return blobs.get(key)

    with patch("app.import_35b.cursor_pack.builder.get_object_bytes", side_effect=_get):
        out = build_cursor_pack(db, fb)

    root = tmp_path / "cursor-packs" / fb.id
    assert (root / "README.md").is_file()
    assert (root / "EXEC.plan.md").is_file()
    assert (root / "manifest.json").is_file()
    assert (root / "evidence" / "p0003" / "raw_model.json").is_file()
    assert out["pack_path"].endswith(fb.id)
    db.refresh(fb)
    assert fb.pack_path
