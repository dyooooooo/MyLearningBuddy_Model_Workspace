from __future__ import annotations

from app.kb.workspace_toc import run_workspace_toc


FAKE_BUILDER = """
def build_toc(root: str, book_id: str = "") -> dict:
    from pathlib import Path
    import json
    dest = Path(root) / "textbooks" / book_id / "toc.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "label": "1 集合的概念",
                        "level": 2,
                        "print_page": 2,
                        "pdf_page": 8,
                        "is_chapter": False,
                    }
                ],
                "print_to_pdf": {"2": 8},
                "toc_pages": [5],
            }
        ),
        encoding="utf-8",
    )
    return {"ok": True}
"""


def test_run_workspace_toc_missing_tool(tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "agent_textbooks_dir", str(tmp_path / "textbooks"))
    assert run_workspace_toc("b1") is None


def test_run_workspace_toc_loads_tool(tmp_path, monkeypatch) -> None:
    from app.config import settings
    from app.kb.import_service import create_kb_book

    monkeypatch.setattr(settings, "agent_textbooks_dir", str(tmp_path / "textbooks"))
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "build_toc.py").write_text(FAKE_BUILDER, encoding="utf-8")
    create_kb_book("b1", filename="book.pdf", label="必修第一册")
    toc = run_workspace_toc("b1")
    assert toc is not None
    assert toc["entries"][0]["level"] == 2
    assert toc["entries"][0]["label"] == "1 集合的概念"


def test_run_workspace_toc_rejects_other_book(tmp_path, monkeypatch) -> None:
    from app.config import settings
    from app.kb.import_service import create_kb_book
    from app.kb.workspace_store import write_toc

    monkeypatch.setattr(settings, "agent_textbooks_dir", str(tmp_path / "textbooks"))
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "build_toc.py").write_text(
        """
def build_toc(root: str, book_id: str = "") -> dict:
    return {"ok": True, "book_id": "other"}
""",
        encoding="utf-8",
    )
    create_kb_book("b1", filename="book.pdf", label="必修第一册")
    write_toc(
        "b1",
        {
            "entries": [{"label": "旧目录", "level": 1, "print_page": 1, "pdf_page": 1, "is_chapter": False}],
            "print_to_pdf": {},
            "toc_pages": [],
        },
    )
    assert run_workspace_toc("b1") is None
