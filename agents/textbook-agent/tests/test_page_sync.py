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


def test_mid_new_sentence_never_touches_source(tmp_path, monkeypatch) -> None:
    """派生层新增正文文字 → 记冲突并留档，官方正文一个字节都不动。

    这条从前叫 `test_mid_new_sentence_writes_source`，断言的正是 D1 那个数据丢失
    行为：中间层多出来的句子会被提拔成官方正文。
    """
    book_id, library, page_sync, _watch = _home(tmp_path, monkeypatch)
    library.write_page(book_id, 1, "hello\n", sync=False)
    library.write_mid(book_id, 1, "::: kb-center\nhello\n:::\n", sync=False)
    page_sync.sync_page(book_id, 1)
    before = library.page_path(book_id, 1).read_bytes()

    library.write_mid(book_id, 1, "::: kb-center\nhello\n:::\n\nnew sentence\n", sync=False)
    out = page_sync.sync_page(book_id, 1)

    assert out["action"] == "conflict"
    assert library.page_path(book_id, 1).read_bytes() == before
    assert "new sentence" not in (library.get_page_markdown(book_id, 1) or "")
    conflict = page_sync.conflict_path(book_id, 1)
    assert conflict.is_file()
    assert "new sentence" in conflict.read_text(encoding="utf-8")


def test_layout_blank_lines_are_not_a_conflict(tmp_path, monkeypatch) -> None:
    """打标工具插排版空行不算分家：一个字没改，不能报成冲突。"""
    book_id, library, page_sync, _watch = _home(tmp_path, monkeypatch)
    library.write_page(book_id, 1, "第一段\n第二段\n", sync=False)
    library.write_mid(book_id, 1, "第一段\n第二段\n", sync=False)
    page_sync.sync_page(book_id, 1)
    before = library.page_path(book_id, 1).read_bytes()

    library.write_mid(book_id, 1, "::: kb-center\n第一段\n\n第二段\n:::\n", sync=False)
    out = page_sync.sync_page(book_id, 1)

    assert out["action"] == "stamp_layout"
    assert library.page_path(book_id, 1).read_bytes() == before
    assert not page_sync.conflict_path(book_id, 1).exists()
    assert "::: kb-center" in (library.get_page_mid(book_id, 1) or "")


def test_same_mtime_no_longer_lets_mid_win(tmp_path, monkeypatch) -> None:
    """两边同一秒都被改 → 正文赢。

    从前是 `_mtime(mid) >= _mtime(source)`，平局判中间层赢，脚本批处理时人工订正
    会被自动标注吃掉。现在没有 mtime 仲裁这回事了。
    """
    import os

    book_id, library, page_sync, _watch = _home(tmp_path, monkeypatch)
    library.write_page(book_id, 1, "原句\n", sync=False)
    library.write_mid(book_id, 1, "原句\n", sync=False)
    page_sync.sync_page(book_id, 1)

    library.write_page(book_id, 1, "人工订正后的句子\n", sync=False)
    library.write_mid(book_id, 1, "::: kb-center\n自动标注塞进来的句子\n:::\n", sync=False)
    # 把两边时间戳抹成完全相同，正是旧代码判中间层赢的那个平局条件
    stamp_ts = 1_700_000_000
    for path in (library.page_path(book_id, 1), library.mid_path(book_id, 1)):
        os.utime(path, (stamp_ts, stamp_ts))

    out = page_sync.sync_page(book_id, 1)

    assert out["action"] == "paste_mid"
    source = library.get_page_markdown(book_id, 1) or ""
    assert source == "人工订正后的句子\n"
    assert "自动标注塞进来的句子" not in source
    conflict = page_sync.conflict_path(book_id, 1)
    assert conflict.is_file()
    assert "自动标注塞进来的句子" in conflict.read_text(encoding="utf-8")


def test_sync_page_can_never_write_source(tmp_path, monkeypatch) -> None:
    """兜底闸：sync_page 的任何分支都不准调用 library.write_page。"""
    book_id, library, page_sync, _watch = _home(tmp_path, monkeypatch)
    assert not hasattr(page_sync, "_write_source"), "反向写回函数不得被重新引入"

    calls: list[tuple] = []
    real = library.write_page
    monkeypatch.setattr(
        library, "write_page", lambda *a, **k: (calls.append(a), real(*a, **k))[1]
    )

    library.write_page(book_id, 1, "正文\n", sync=False)
    calls.clear()
    for mid in (
        "::: kb-center\n正文\n:::\n",            # 只加围栏
        "::: kb-center\n正文\n\n多的一句\n:::\n",  # 改了正文文字
        "完全不同的内容\n",                        # 整段替换
    ):
        library.write_mid(book_id, 1, mid, sync=False)
        page_sync.sync_page(book_id, 1)
    assert calls == [], f"sync_page 写了正文：{calls}"


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
    stamp = page_sync.stamp_path(book_id, 1)
    before = stamp.stat().st_mtime_ns
    again = page_sync.sync_page(book_id, 1)
    assert again["action"] == "ok"
    assert stamp.stat().st_mtime_ns == before
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


def test_paste_fences_keeps_empty_kb_dash(tmp_path, monkeypatch) -> None:
    _book_id, _library, page_sync, _watch = _home(tmp_path, monkeypatch)
    old = (
        "::: kb-section-icon\n![x](a.jpg)\n\n## 想一想\n:::\n\n"
        "条目甲\n\n::: kb-dash\n\n:::\n\n"
        "::: kb-section-icon\n![y](b.jpg)\n\n## 想一想\n:::\n\n"
        "条目乙\n\n::: kb-dash\n\n:::\n\n"
        "## 学习聚焦\n\n√ 一条\n\n::: kb-dash\n\n:::\n\n"
        "## 知识回放\n\n● 回一条\n\n::: kb-dash\n\n:::\n\n"
        "## 资料库\n\n资料\n\n::: kb-dash\n\n:::\n"
    )
    source = (
        "![x](a.jpg)\n\n## 想一想\n\n条目甲\n\n"
        "![y](b.jpg)\n\n## 想一想\n\n条目乙\n\n"
        "## 学习聚焦\n\n√ 一条\n\n"
        "## 知识回放\n\n● 回一条\n\n"
        "## 资料库\n\n资料\n"
    )
    out = page_sync.paste_fences(old, source)
    assert out.count("::: kb-dash") == 5
    assert out.count("::: kb-section-icon") == 2
    assert "条目甲" in out
    assert "条目乙" in out


def test_apply_chemistry_shapes_same_body(tmp_path, monkeypatch) -> None:
    _book_id, _library, page_sync, _watch = _home(tmp_path, monkeypatch)
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "workspace" / "tools" / "tag_chemistry.py"
    spec = importlib.util.spec_from_file_location("tag_chemistry_seed", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = "## 学习聚焦\n\n了解氧化还原反应的概念。\n"
    new_src, new_mid = module.apply_chemistry_shapes(source, source)
    assert "::: kb-dash" in new_mid
    assert page_sync.same_body(new_mid, new_src)


def test_refine_book_reports_empty_pages(tmp_path, monkeypatch) -> None:
    book_id, library, _page_sync, _watch = _home(tmp_path, monkeypatch)
    library.write_page(book_id, 1, "hello\n", sync=False)
    empty = library.page_path(book_id, 5)
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_bytes(b"")
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "workspace" / "tools" / "refine_book.py"
    spec = importlib.util.spec_from_file_location("refine_book_seed", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = module.refine_book(book_id, root=str(library.textbooks_root()))
    assert out.get("ok") is True
    assert out.get("pages") >= 2
    assert 5 in (out.get("empty_pages") or [])
    assert out.get("has_book_pdf") is True


def test_audit_mid_syntax_root_is_textbooks(tmp_path, monkeypatch) -> None:
    book_id, library, _page_sync, _watch = _home(tmp_path, monkeypatch)
    library.write_page(book_id, 1, "hello\n", sync=False)
    from app.workspace_module import load

    audit = load("audit_mid_syntax")
    out = audit.audit_mid_syntax(book_id=book_id, apply="scan", root=str(library.textbooks_root()))
    assert out.get("ok") is True
    assert out.get("error") != "没有找到教材"


def test_parse_textbook_md(tmp_path) -> None:
    from app.page_watch import parse_textbook_md

    root = tmp_path / "textbooks"
    (root / "bookA" / "pages").mkdir(parents=True)
    (root / "bookA" / "mid").mkdir(parents=True)
    assert parse_textbook_md(root / "bookA" / "pages" / "0007.md", root) == ("bookA", 7)
    assert parse_textbook_md(root / "bookA" / "mid" / "0007.md", root) == ("bookA", 7)
    assert parse_textbook_md(root / "bookA" / "mid" / ".stamps" / "0007.json", root) is None
