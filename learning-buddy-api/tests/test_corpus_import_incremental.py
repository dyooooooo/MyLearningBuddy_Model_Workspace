"""Tests for incremental corpus PDF import."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.corpus_import import create_import_job, process_import_job
from app.services.corpus_import_incremental import (
    ensure_import_textbook_shell,
    group_mineru_blocks_by_page,
    mineru_blocks_to_page_markdown,
    page_chunk_id,
    upsert_page_draft_chunk,
)


@pytest.fixture
def import_session(monkeypatch, tmp_path):
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


def test_page_chunk_id_format():
    assert page_chunk_id("tb:import:math:hs:abc", 3) == "chunk:tb:import:math:hs:abc:p0003"


def test_mineru_blocks_group_and_markdown():
    blocks = [
        {"page_idx": 0, "type": "text", "text": "Hello"},
        {"page_idx": 0, "type": "text", "text": "World"},
        {"page_idx": 1, "type": "text", "text": "Page two"},
    ]
    grouped = group_mineru_blocks_by_page(blocks)
    assert 1 in grouped and 2 in grouped
    md = mineru_blocks_to_page_markdown(grouped[1])
    assert "Hello" in md and "World" in md


def test_mineru_blocks_include_figures():
    blocks = [
        {"type": "text", "text": "见下图"},
        {
            "type": "image",
            "img_path": "/api/v1/studio/proxy/import/asset?key=corpus%2Fimports%2Fx%2Fassets%2Fmineru%2Ffig1.png",
            "image_caption": ["图 1-1 函数图像"],
        },
        {
            "type": "table",
            "table_caption": ["表 1-1"],
            "table_body": "| a | b |\n|---|---|",
            "img_path": "/api/v1/studio/proxy/import/asset?key=corpus%2Fimports%2Fx%2Fassets%2Fmineru%2Ftbl.png",
        },
    ]
    md = mineru_blocks_to_page_markdown(blocks)
    assert "见下图" in md
    assert "![图 1-1 函数图像]" in md
    assert "表 1-1" in md
    assert "| a | b |" in md
    assert "![表 1-1]" in md


def test_ensure_textbook_shell(import_session):
    job = create_import_job(
        import_session,
        minio_key="corpus/imports/x/book.pdf",
        subject="math",
        edition_id="hujiao_math_hs_v1",
        grade_band="hs",
        label="测试教材",
    )
    tb_id = ensure_import_textbook_shell(import_session, job)
    import_session.commit()
    assert job.textbook_node_id == tb_id
    meta = __import__("json").loads(job.meta_json)
    assert len(meta.get("node_ids") or []) >= 2


def test_upsert_page_draft_chunk_grows_meta(import_session):
    job = create_import_job(
        import_session,
        minio_key="corpus/imports/x/book.pdf",
        subject="math",
        edition_id="hujiao_math_hs_v1",
        grade_band="hs",
        label="测试",
    )
    ensure_import_textbook_shell(import_session, job)
    upsert_page_draft_chunk(
        import_session,
        job,
        page_no=1,
        page_md="# 第一页\n\n内容",
        total_pages=3,
        engine="vision_ocr",
    )
    import_session.commit()
    meta = __import__("json").loads(job.meta_json)
    assert meta["pages_ready"] == [1]
    assert meta["chunk_progress"] == "1/3"
    assert page_chunk_id(job.textbook_node_id, 1) in meta["node_ids"]


def test_process_import_job_incremental_vision(import_session, monkeypatch):
    job = create_import_job(
        import_session,
        minio_key="corpus/imports/x/book.pdf",
        subject="math",
        edition_id="hujiao_math_hs_v1",
        grade_band="hs",
        label="增量测试",
        parse_engine="mineru_cloud",
    )
    import_session.commit()

    fake_md = "# P1\n\n---\n\n# P2"
    fake_meta = {"converter": "mineru_cloud", "page_count": 2, "char_count": len(fake_md)}

    def fake_convert(raw, *, filename, engine, progress_cb=None, page_done_cb=None, asset_prefix="", page_limit=None, page_ranges=None):
        if page_done_cb:
            page_done_cb(1, "# P1", 2)
            page_done_cb(2, "# P2", 2)
        if progress_cb:
            progress_cb({"ocr_progress": "2/2", "ocr_page": 2, "ocr_total": 2})
        return fake_md, fake_meta

    monkeypatch.setattr(
        "app.services.corpus_import.get_object_bytes",
        lambda key: b"%PDF fake",
    )
    monkeypatch.setattr(
        "app.services.corpus_import.convert_pdf_bytes",
        fake_convert,
    )

    with patch("app.services.corpus_import_incremental.parse_textbook_markdown") as mock_parse:
        mock_parse.return_value = (
            {"id": job.textbook_node_id, "label": "增量测试", "summary": "", "text": "", "tags": [], "links": {}},
            [],
        )
        result = process_import_job(import_session, job.id)
        import_session.commit()

    assert result.status == "chunked"
    meta = __import__("json").loads(result.meta_json)
    assert meta.get("pages_ready") == [1, 2]
    assert meta.get("structure_merged") is True
