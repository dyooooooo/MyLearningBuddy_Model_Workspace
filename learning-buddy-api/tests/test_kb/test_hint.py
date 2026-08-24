from __future__ import annotations

from app.kb.hint import clean_textbook_hint, pick_textbook_hint
from app.kb.import_service import create_kb_book
from app.kb.workspace_store import read_meta, write_meta


def test_clean_hint_rejects_pdf_filename():
    assert clean_textbook_hint(hint="book.pdf", label="", filename="book.pdf") == ""
    assert clean_textbook_hint(hint="沪教版数学必修第一册", filename="a.pdf") == "沪教版数学必修第一册"


def test_pick_textbook_hint_prefers_default(tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "agent_textbooks_dir", str(tmp_path / "textbooks"))
    monkeypatch.setattr(settings, "textbook_agent_url", "")
    create_kb_book("old", filename="a.pdf", subject="math", textbook_hint="旧书", pdf_bytes=b"%PDF")
    create_kb_book(
        "cur",
        filename="b.pdf",
        subject="math",
        textbook_hint="沪教版数学必修第一册",
        pdf_bytes=b"%PDF",
    )
    for book_id in ("old", "cur"):
        meta = read_meta(book_id)
        assert meta is not None
        meta["status"] = "ready"
        if book_id == "old":
            meta["is_default"] = True
        write_meta(book_id, meta)
    assert pick_textbook_hint(subject="math") == "旧书"
