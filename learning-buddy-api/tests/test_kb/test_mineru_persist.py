from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from app.kb.import_service import create_kb_book, dump_mineru_json, process_kb_book
from app.kb.mineru_client import KbParseResult, persist_workdir, probe_mineru_blocks
from app.kb.settings import KbMineruRuntime
from app.kb.workspace_store import mineru_dir, page_path


def _runtime() -> KbMineruRuntime:
    return KbMineruRuntime(
        api_token="t",
        api_base_url="https://mineru.net",
        model_version="vlm",
        language="ch",
        enable_formula=True,
        enable_table=True,
        is_ocr=False,
        timeout_s=600,
    )


def test_persist_workdir_copies_official_json(tmp_path: Path) -> None:
    work = tmp_path / "zip"
    work.mkdir()
    (work / "book_content_list_v2.json").write_text("[[]]", encoding="utf-8")
    (work / "book_content_list.json").write_text("[]", encoding="utf-8")
    (work / "layout.json").write_text("{}", encoding="utf-8")
    (work / "book_model.json").write_text("[]", encoding="utf-8")
    (work / "chapter.md").write_text("# 官方正文\n", encoding="utf-8")
    (work / "images").mkdir()
    (work / "images" / "fig.jpg").write_bytes(b"\xff\xd8\xff")
    dest = tmp_path / "mineru"
    found = persist_workdir(work, dest)
    assert found["content_list_v2"] == "book_content_list_v2.json"
    assert found["content_list"] == "book_content_list.json"
    assert found["layout"] == "layout.json"
    assert found["model"] == "book_model.json"
    assert found["markdown"] == "full.md"
    assert (dest / "layout.json").is_file()
    assert (dest / "book_content_list.json").read_text(encoding="utf-8") == "[]"
    assert (dest / "chapter.md").read_text(encoding="utf-8") == "# 官方正文\n"
    assert (dest / "full.md").read_text(encoding="utf-8") == "# 官方正文\n"
    assert found["images"] == "1"
    assert (dest / "images" / "fig.jpg").read_bytes() == b"\xff\xd8\xff"


def test_probe_mineru_blocks_counts_bbox_and_aside() -> None:
    probe = probe_mineru_blocks(
        [
            {"type": "text", "bbox": [90, 100, 600, 200], "page_idx": 0},
            {"type": "aside_text", "bbox": [720, 100, 900, 180], "page_idx": 0},
            {"type": "header", "text": "沪教", "page_idx": 0},
        ]
    )
    assert probe["block_count"] == 3
    assert probe["bbox_count"] == 2
    assert probe["aside_count"] == 1
    assert probe["types"]["aside_text"] == 1
    assert probe["pages"][0]["left"] == 1
    assert probe["pages"][0]["right"] == 1


def test_dump_mineru_json_keeps_pages(tmp_path: Path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "agent_textbooks_dir", str(tmp_path / "textbooks"))
    create_kb_book("en1", filename="book.pdf", label="英语", pdf_bytes=b"%PDF")
    page_path("en1", 10).parent.mkdir(parents=True, exist_ok=True)
    page_path("en1", 10).write_text("keep-me", encoding="utf-8")

    def fake_parse(*_args, persist_dir=None, **_kwargs):
        dest = Path(persist_dir)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "demo_content_list.json").write_text("[]", encoding="utf-8")
        (dest / "blocks.json").write_text("[]", encoding="utf-8")
        return KbParseResult(
            markdown="",
            blocks=[{"type": "text", "bbox": [10, 20, 30, 40], "page_idx": 0}],
        )

    with (
        patch("app.kb.import_service.require_export_config", return_value=_runtime()),
        patch("app.kb.import_service.parse_pdf", side_effect=fake_parse),
        patch("app.clients.redis_client.is_kb_book_revoked", return_value=False),
    ):
        out = dump_mineru_json("en1", page_ranges="6-12")

    assert page_path("en1", 10).read_text(encoding="utf-8") == "keep-me"
    assert out["page_ranges"] == "6-12"
    assert out["block_count"] == 1
    assert (mineru_dir("en1") / "manifest.json").is_file()
    manifest = json.loads((mineru_dir("en1") / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["probe"]["bbox_count"] == 1


def test_process_kb_book_passes_persist_dir(tmp_path: Path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "agent_textbooks_dir", str(tmp_path / "textbooks"))
    create_kb_book("b1", filename="book.pdf", label="书", pdf_bytes=b"%PDF")

    class _Pages:
        def page_count(self) -> int:
            return 1

        def page_jpeg(self, page_no: int, dpi: int = 150, quality: int = 92) -> bytes:
            return b"jpeg"

    from contextlib import contextmanager

    @contextmanager
    def _open_pdf(_raw: bytes):
        yield _Pages()

    with (
        patch("app.kb.import_service.require_export_config", return_value=_runtime()),
        patch(
            "app.kb.import_service.parse_pdf",
            return_value=KbParseResult(markdown="", blocks=[{"type": "text", "page_idx": 0, "text": "hi"}]),
        ) as parsed,
        patch("app.kb.import_service.open_pdf", _open_pdf),
        patch("app.clients.redis_client.is_kb_book_revoked", return_value=False),
        patch("app.kb.agent_notify.notify_import_completed"),
    ):
        process_kb_book("b1")

    assert parsed.call_args.kwargs["persist_dir"] == mineru_dir("b1")
    assert (mineru_dir("b1") / "manifest.json").is_file()
