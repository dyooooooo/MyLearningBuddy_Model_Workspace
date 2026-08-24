from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from app.kb.import_service import book_to_dict, create_kb_book, process_kb_book
from app.kb.mineru_client import KbParseResult
from app.kb.settings import KbMineruRuntime
from app.kb.workspace_store import page_path, read_meta, read_toc


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
    ):
        out = process_kb_book("b1")

    assert out["status"] == "ready"
    assert out["page_count"] == 2
    assert "例3" in page_path("b1", 2).read_text(encoding="utf-8")
    assert read_meta("b1")["status"] == "ready"
    d = book_to_dict(read_meta("b1"))
    assert d["textbook_hint"] == "沪教版数学必修第一册"
    assert d["toc_entry_count"] >= 1
    assert read_toc("b1")["entries"]


def test_create_kb_book_rejects_bad_id(tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "agent_textbooks_dir", str(tmp_path / "textbooks"))
    try:
        create_kb_book("..", filename="a.pdf")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
