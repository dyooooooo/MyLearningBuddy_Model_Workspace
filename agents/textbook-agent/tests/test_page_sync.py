from __future__ import annotations

import time
from pathlib import Path

from app.workspace import ensure_workspace


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PAGE_SYNC_WATCH", "0")
    from app.config import settings
    from app import library, page_sync, page_watch

    settings.agent_home = str(tmp_path / "home")
    settings.page_sync_watch = False
    settings.page_sync_debounce_ms = 50
    page_sync.reset_for_tests()
    page_watch.reset_for_tests()
    ensure_workspace()
    book = library.create_book(
        pdf_bytes=b"%PDF-1.4 demo",
        filename="book.pdf",
        book_id="sync1",
        label="同步册",
    )
    return book["id"], library, page_sync, page_watch


def test_fence_only_does_not_rewrite_source(tmp_path, monkeypatch) -> None:
    book_id, library, page_sync, _watch = _home(tmp_path, monkeypatch)
    source = "hello\n"
    mid = "::: kb-center\nhello\n:::\n"
    library.write_page(book_id, 1, source, sync=False)
    before = library.page_path(book_id, 1).read_bytes()
    library.write_mid(book_id, 1, mid, sync=False)
    out = page_sync.sync_page(book_id, 1)
    assert out["action"] == "stamp"
    assert library.page_path(book_id, 1).read_bytes() == before
    assert library.get_page_mid(book_id, 1) == mid


def test_mid_new_sentence_writes_source(tmp_path, monkeypatch) -> None:
    book_id, library, page_sync, _watch = _home(tmp_path, monkeypatch)
    library.write_page(book_id, 1, "hello\n", sync=False)
    library.write_mid(book_id, 1, "::: kb-center\nhello\n:::\n", sync=False)
    page_sync.sync_page(book_id, 1)
    library.write_mid(book_id, 1, "::: kb-center\nhello\n:::\n\nnew sentence\n", sync=False)
    out = page_sync.sync_page(book_id, 1)
    assert out["action"] == "write_source"
    assert "new sentence" in (library.get_page_markdown(book_id, 1) or "")
    assert "::: kb-center" in (library.get_page_mid(book_id, 1) or "")


def test_source_edit_pastes_old_fences(tmp_path, monkeypatch) -> None:
    book_id, library, page_sync, _watch = _home(tmp_path, monkeypatch)
    library.write_page(book_id, 1, "A\nB\nC\n", sync=False)
    library.write_mid(book_id, 1, "::: kb-center\nA\n:::\nB\n::: kb-lead\nC\n:::\n", sync=False)
    page_sync.sync_page(book_id, 1)
    library.write_page(book_id, 1, "A\nB改了\nC\n", sync=False)
    out = page_sync.sync_page(book_id, 1)
    assert out["action"] == "paste_mid"
    mid = library.get_page_mid(book_id, 1) or ""
    assert "::: kb-center\nA\n:::" in mid
    assert "::: kb-lead\nC\n:::" in mid
    assert "B改了" in mid


def test_import_dual_write_keeps_center_fence(tmp_path, monkeypatch) -> None:
    book_id, library, page_sync, _watch = _home(tmp_path, monkeypatch)
    source = "hello\n"
    mid = "::: kb-center\nhello\n:::\n"
    library.write_page(book_id, 1, source, sync=False)
    library.write_mid(book_id, 1, mid, sync=False)
    out = page_sync.sync_page(book_id, 1)
    assert out["action"] == "stamp"
    assert library.get_page_mid(book_id, 1) == mid
    again = page_sync.sync_page(book_id, 1)
    assert again["action"] == "stamp"
    assert library.get_page_mid(book_id, 1) == mid
    assert library.get_page_markdown(book_id, 1) == source


def test_self_write_does_not_loop(tmp_path, monkeypatch) -> None:
    book_id, library, page_sync, _watch = _home(tmp_path, monkeypatch)
    writes = {"n": 0}
    real = library.write_page

    def _count(*args, **kwargs):
        writes["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(library, "write_page", _count)
    library.write_page(book_id, 1, "hello\n", sync=False)
    library.write_mid(book_id, 1, "::: kb-center\nhello\n:::\n", sync=False)
    page_sync.sync_page(book_id, 1)
    after_first = writes["n"]
    page_sync.sync_page(book_id, 1)
    assert writes["n"] == after_first


def test_paste_fences_tags_unmatched(tmp_path, monkeypatch) -> None:
    _book_id, _library, page_sync, _watch = _home(tmp_path, monkeypatch)
    old = "::: kb-center\nA\n:::\nB\n"
    new = "A\nB改了\n"

    def _tag(chunk: str) -> str:
        return f"::: kb-tagged\n{chunk.strip()}\n:::"

    out = page_sync.paste_fences(old, new, _tag)
    assert "::: kb-center\nA\n:::" in out
    assert "::: kb-tagged\nB改了\n:::" in out


def test_refine_page_keeps_mid(tmp_path, monkeypatch) -> None:
    book_id, library, page_sync, _watch = _home(tmp_path, monkeypatch)
    library.write_page(book_id, 1, "hello   \n\n\nworld\n", sync=False)
    library.write_mid(book_id, 1, "::: kb-center\nhello\n:::\n\nworld\n", sync=False)
    page_sync.sync_page(book_id, 1)
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "workspace" / "tools" / "refine_page.py"
    spec = importlib.util.spec_from_file_location("refine_page_seed", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.refine_page(book_id, 1, root=str(library.textbooks_root()))
    assert library.mid_path(book_id, 1).is_file()
    assert "::: kb-center" in (library.get_page_mid(book_id, 1) or "")


def test_watch_handle_path_ignores_stamps_and_tmp(tmp_path, monkeypatch) -> None:
    book_id, library, page_sync, page_watch = _home(tmp_path, monkeypatch)
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(page_watch, "schedule", lambda b, p: calls.append((b, p)))
    root = library.textbooks_root()
    page_watch.handle_path(root / book_id / "mid" / ".stamps" / "0001.json", root=root)
    page_watch.handle_path(root / book_id / "pages" / "0001.md.tmp", root=root)
    assert calls == []
    page_watch.handle_path(root / book_id / "pages" / "0001.md", root=root)
    assert calls == [(book_id, 1)]


def test_watch_debounce_coalesces(tmp_path, monkeypatch) -> None:
    book_id, _library, _page_sync, page_watch = _home(tmp_path, monkeypatch)
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(page_watch, "sync_page", lambda b, p: calls.append((b, p)) or {"action": "stamp"})
    from app.config import settings

    settings.page_sync_debounce_ms = 40
    page_watch.schedule(book_id, 2)
    page_watch.schedule(book_id, 2)
    time.sleep(0.2)
    assert calls == [(book_id, 2)]


def test_parse_textbook_md(tmp_path) -> None:
    from app.page_watch import parse_textbook_md

    root = tmp_path / "textbooks"
    (root / "bookA" / "pages").mkdir(parents=True)
    (root / "bookA" / "mid").mkdir(parents=True)
    assert parse_textbook_md(root / "bookA" / "pages" / "0007.md", root) == ("bookA", 7)
    assert parse_textbook_md(root / "bookA" / "mid" / "0007.md", root) == ("bookA", 7)
    assert parse_textbook_md(root / "bookA" / "mid" / ".stamps" / "0007.json", root) is None
