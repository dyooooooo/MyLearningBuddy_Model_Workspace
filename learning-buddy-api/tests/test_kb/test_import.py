from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from app.kb.import_service import book_to_dict, create_kb_book, identify_kb_book, process_kb_book
from app.kb.mineru_client import KbParseResult
from app.kb.settings import KbMineruRuntime
from app.kb.workspace_store import mid_path, page_path, read_meta, read_toc


class _FakePages:
    def page_count(self) -> int:
        return 2

    def page_jpeg(self, page_no: int, dpi: int = 120) -> bytes:
        return b"jpeg" + bytes([page_no])


@contextmanager
def _open_pdf(_raw: bytes):
    yield _FakePages()


def test_process_kb_book_writes_workspace_files(tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "agent_textbooks_dir", str(tmp_path / "textbooks"))
    create_kb_book(
        "b1",
        filename="book.pdf",
        label="必修第一册",
        textbook_hint="沪教版数学必修第一册",
        pdf_bytes=b"%PDF",
    )
    blocks = [
        {
            "type": "index",
            "page_idx": 0,
            "content": {"list_items": ["1.1 集合初步 2"]},
        },
        {"type": "text", "page_idx": 1, "text": "例3"},
    ]
    runtime = KbMineruRuntime(
        api_token="t",
        api_base_url="https://mineru.net",
        model_version="vlm",
        language="ch",
        enable_formula=True,
        enable_table=True,
        is_ocr=False,
        timeout_s=600,
    )

    with (
        patch("app.kb.import_service.require_export_config", return_value=runtime),
        patch(
            "app.kb.import_service.parse_pdf",
            return_value=KbParseResult(markdown="# md", blocks=blocks),
        ),
        patch("app.kb.import_service.open_pdf", _open_pdf),
        patch("app.clients.redis_client.is_kb_book_revoked", return_value=False),
    ):
        out = process_kb_book("b1")

    assert out["status"] == "ready"
    assert out["page_count"] == 2
    assert "例3" in page_path("b1", 2).read_text(encoding="utf-8")
    assert 'class="kb-' not in page_path("b1", 1).read_text(encoding="utf-8")
    assert "1.1 集合初步 2" in page_path("b1", 1).read_text(encoding="utf-8")
    mid1 = mid_path("b1", 1).read_text(encoding="utf-8")
    assert "::: kb-page-toc" in mid1
    assert read_meta("b1")["status"] == "ready"
    d = book_to_dict(read_meta("b1"))
    assert d["textbook_hint"] == "沪教版数学必修第一册"
    assert d["toc_entry_count"] >= 1
    assert read_toc("b1")["entries"]


def test_process_kb_book_uses_workspace_build_toc(tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "agent_textbooks_dir", str(tmp_path / "textbooks"))
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "build_toc.py").write_text(
        """
def build_toc(root: str, book_id: str = "") -> dict:
    from pathlib import Path
    import json
    dest = Path(root) / "textbooks" / book_id / "toc.json"
    dest.write_text(
        json.dumps({"entries": [{"label": "1 集合", "level": 2, "print_page": 2, "pdf_page": 8, "is_chapter": False}], "print_to_pdf": {"2": 8}, "toc_pages": []}),
        encoding="utf-8",
    )
    return {"ok": True}
""",
        encoding="utf-8",
    )
    create_kb_book("b1", filename="book.pdf", label="必修第一册", pdf_bytes=b"%PDF")
    blocks = [{"type": "text", "page_idx": 0, "text": "例3"}]
    runtime = KbMineruRuntime(
        api_token="t",
        api_base_url="https://mineru.net",
        model_version="vlm",
        language="ch",
        enable_formula=True,
        enable_table=True,
        is_ocr=False,
        timeout_s=600,
    )
    with (
        patch("app.kb.import_service.require_export_config", return_value=runtime),
        patch(
            "app.kb.import_service.parse_pdf",
            return_value=KbParseResult(markdown="# md", blocks=blocks),
        ),
        patch("app.kb.import_service.open_pdf", _open_pdf),
        patch("app.clients.redis_client.is_kb_book_revoked", return_value=False),
    ):
        process_kb_book("b1")
    toc = read_toc("b1")
    assert toc["entries"][0]["level"] == 2
    assert toc["entries"][0]["label"] == "1 集合"
    from app.kb.workspace_store import mineru_toc_path

    assert mineru_toc_path("b1").is_file()


def test_create_kb_book_rejects_bad_id(tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "agent_textbooks_dir", str(tmp_path / "textbooks"))
    try:
        create_kb_book("..", filename="a.pdf")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_process_kb_book_wipes_leftover_pages(tmp_path, monkeypatch) -> None:
    from app.config import settings
    from app.kb.workspace_store import page_path, write_page, write_toc

    monkeypatch.setattr(settings, "agent_textbooks_dir", str(tmp_path / "textbooks"))
    create_kb_book("b1", filename="book.pdf", label="旧书", pdf_bytes=b"%PDF")
    write_page("b1", 9, "旧页不该留下", b"old")
    write_toc(
        "b1",
        {
            "entries": [{"label": "旧目录", "level": 1, "print_page": 1, "pdf_page": 1, "is_chapter": False}],
            "print_to_pdf": {},
            "toc_pages": [],
        },
    )
    runtime = KbMineruRuntime(
        api_token="t",
        api_base_url="https://mineru.net",
        model_version="vlm",
        language="ch",
        enable_formula=True,
        enable_table=True,
        is_ocr=False,
        timeout_s=600,
    )
    with (
        patch("app.kb.import_service.require_export_config", return_value=runtime),
        patch(
            "app.kb.import_service.parse_pdf",
            return_value=KbParseResult(
                markdown="# md",
                blocks=[{"type": "text", "page_idx": 0, "text": "新页"}],
            ),
        ),
        patch("app.kb.import_service.open_pdf", _open_pdf),
        patch("app.clients.redis_client.is_kb_book_revoked", return_value=False),
    ):
        process_kb_book("b1")
    assert not page_path("b1", 9).is_file()
    assert "旧目录" not in str(read_toc("b1"))


def test_process_kb_book_notifies_agent(tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "agent_textbooks_dir", str(tmp_path / "textbooks"))
    create_kb_book("b1", filename="book.pdf", label="必修", pdf_bytes=b"%PDF")
    runtime = KbMineruRuntime(
        api_token="t",
        api_base_url="https://mineru.net",
        model_version="vlm",
        language="ch",
        enable_formula=True,
        enable_table=True,
        is_ocr=False,
        timeout_s=600,
    )
    with (
        patch("app.kb.import_service.require_export_config", return_value=runtime),
        patch(
            "app.kb.import_service.parse_pdf",
            return_value=KbParseResult(markdown="# md", blocks=[{"type": "text", "page_idx": 0, "text": "例"}]),
        ),
        patch("app.kb.import_service.open_pdf", _open_pdf),
        patch("app.clients.redis_client.is_kb_book_revoked", return_value=False),
        patch("app.kb.agent_notify.notify_import_completed") as notify,
    ):
        process_kb_book("b1")
    notify.assert_called_once_with("b1", conversation_id="")


def test_process_kb_book_notifies_existing_conversation(tmp_path, monkeypatch) -> None:
    from app.config import settings
    from app.kb.workspace_store import write_meta

    monkeypatch.setattr(settings, "agent_textbooks_dir", str(tmp_path / "textbooks"))
    create_kb_book("b2", filename="book.pdf", label="必修", pdf_bytes=b"%PDF")
    meta = read_meta("b2")
    assert meta is not None
    meta["conversation_id"] = "conv-keep"
    write_meta("b2", meta)
    runtime = KbMineruRuntime(
        api_token="t",
        api_base_url="https://mineru.net",
        model_version="vlm",
        language="ch",
        enable_formula=True,
        enable_table=True,
        is_ocr=False,
        timeout_s=600,
    )
    with (
        patch("app.kb.import_service.require_export_config", return_value=runtime),
        patch(
            "app.kb.import_service.parse_pdf",
            return_value=KbParseResult(markdown="# md", blocks=[{"type": "text", "page_idx": 0, "text": "例"}]),
        ),
        patch("app.kb.import_service.open_pdf", _open_pdf),
        patch("app.clients.redis_client.is_kb_book_revoked", return_value=False),
        patch("app.kb.agent_notify.notify_import_completed") as notify,
    ):
        process_kb_book("b2")
    notify.assert_called_once_with("b2", conversation_id="conv-keep")


def test_identify_kb_book_uses_first_pages_only(tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "agent_textbooks_dir", str(tmp_path / "textbooks"))
    create_kb_book("id1", filename="book.pdf", label="待确认", pdf_bytes=b"%PDF")
    runtime = KbMineruRuntime(
        api_token="t",
        api_base_url="https://mineru.net",
        model_version="vlm",
        language="ch",
        enable_formula=True,
        enable_table=True,
        is_ocr=False,
        timeout_s=600,
    )
    with (
        patch("app.kb.import_service.require_export_config", return_value=runtime),
        patch("app.kb.import_service.slice_pdf_pages", return_value=b"%PDF-preview") as sliced,
        patch(
            "app.kb.import_service.parse_pdf",
            return_value=KbParseResult(
                markdown="普通高中教科书\n数学\n必修第一册",
                blocks=[],
            ),
        ) as parsed,
        patch("app.clients.redis_client.is_kb_book_revoked", return_value=False),
        patch("app.kb.agent_notify.notify_import_completed") as notify,
    ):
        out = identify_kb_book("id1")
    sliced.assert_called_once()
    kwargs = parsed.call_args.kwargs
    assert kwargs["page_ranges"] == "1-3"
    assert kwargs["upload_figures"] is False
    assert out["preview"].startswith("普通高中教科书")
    assert out["preview_path"] == "textbooks/id1/identify_preview.md"
    assert out["pages_used"] == 3
    assert out["status"] == "uploaded"
    assert out["subject"] == "math"
    assert out["label"] == "待确认"
    notify.assert_not_called()
    assert read_meta("id1")["label"] == "待确认"
    assert (tmp_path / "textbooks" / "id1" / "identify_preview.md").read_text(
        encoding="utf-8"
    ).startswith("普通高中教科书")


def test_purge_kb_book_revokes_and_clears(tmp_path, monkeypatch) -> None:
    from app.config import settings
    from app.kb.import_service import purge_kb_book
    from app.kb.workspace_store import book_dir

    monkeypatch.setattr(settings, "agent_textbooks_dir", str(tmp_path / "textbooks"))
    create_kb_book("gone1", filename="book.pdf", label="待删", pdf_bytes=b"%PDF")
    with (
        patch("app.clients.redis_client.revoke_kb_book") as revoke,
        patch("app.clients.redis_client.purge_kb_import_queue", return_value=1),
        patch("app.kb.assets.delete_book_assets", return_value=3),
    ):
        out = purge_kb_book("gone1")
    revoke.assert_called_once_with("gone1")
    assert out["queue_removed"] == 1
    assert out["minio_removed"] == 3
    assert out["workspace_removed"] is True
    assert not book_dir("gone1").exists()
