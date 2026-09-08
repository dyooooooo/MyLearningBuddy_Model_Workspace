from __future__ import annotations

from app.block_ids import attach_ids, extract_ids, find_id_span, stamp_text, strip_ids
from app.page_sync import paste_fences, same_body
from app.workspace import ensure_workspace


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PAGE_SYNC_WATCH", "0")
    from app.config import settings
    from app import library, page_sync, page_watch

    settings.agent_home = str(tmp_path / "home")
    settings.page_sync_watch = False
    page_sync.reset_for_tests()
    page_watch.reset_for_tests()
    ensure_workspace()
    book = library.create_book(
        pdf_bytes=b"%PDF-1.4 demo",
        filename="book.pdf",
        book_id="bid1",
        label="块号册",
    )
    return book["id"], library, page_sync


def test_typo_keeps_fence_when_id_present() -> None:
    source = "选择性必修 ^kb-aaaaaa\n"
    mid = "::: kb-title\n选择性必修 ^kb-aaaaaa\n:::\n"
    edited = "选X性必修 ^kb-aaaaaa\n"
    out = paste_fences(mid, edited)
    assert "::: kb-title\n选X性必修 ^kb-aaaaaa\n:::" in out
    assert extract_ids(out) == ["kb-aaaaaa"]


def test_without_id_still_drops_on_typo() -> None:
    mid = "::: kb-title\n选择性必修\n:::\n"
    out = paste_fences(mid, "选X性必修\n")
    assert "::: kb-title" not in out
    assert "选X性必修" in out


def test_table_standalone_id_covers_table() -> None:
    table = "<table><tr><td>A</td></tr></table>"
    source = f"{table}\n\n^kb-aabbcc\n"
    mid = f"::: kb-table-wrap\n{table}\n\n^kb-aabbcc\n:::\n"
    edited = f"<table><tr><td>AX</td></tr></table>\n\n^kb-aabbcc\n"
    out = paste_fences(mid, edited)
    assert "::: kb-table-wrap" in out
    assert "AX" in out
    assert "^kb-aabbcc" in out


def test_multiline_list_item_does_not_eat_neighbors() -> None:
    source = (
        "9. 第一行\n"
        "续行内容 ^kb-aaaaaa\n"
        "10. 下一题 ^kb-bbbbbb\n"
    )
    mid = (
        "::: kb-list-item\n9. 第一行\n续行内容 ^kb-aaaaaa\n:::\n"
        "::: kb-list-item\n10. 下一题 ^kb-bbbbbb\n:::\n"
    )
    out = paste_fences(mid, source.replace("续行", "续X", 1))
    assert out.count("::: kb-list-item") == 2
    assert "续X内容" in out
    assert "下一题" in out


def test_consecutive_list_items_do_not_overlap() -> None:
    source = (
        "9. 第一题题干 ^kb-aaaaaa\n"
        "10. 第二题题干 ^kb-bbbbbb\n"
    )
    mid = (
        "::: kb-list-item\n9. 第一题题干 ^kb-aaaaaa\n:::\n"
        "::: kb-list-item\n10. 第二题题干 ^kb-bbbbbb\n:::\n"
    )
    edited = (
        "9. 第X题题干 ^kb-aaaaaa\n"
        "10. 第二题题干 ^kb-bbbbbb\n"
    )
    out = paste_fences(mid, edited)
    assert out.count("::: kb-list-item") == 2
    assert "第X题题干" in out
    assert "第二题题干" in out


def test_multi_block_start_end_survives_middle_edit() -> None:
    source = "第一段 ^kb-111111\n\n中间段原文\n\n最后一段 ^kb-222222\n"
    mid = "::: kb-verse\n第一段 ^kb-111111\n\n中间段原文\n\n最后一段 ^kb-222222\n:::\n"
    edited = "第一段 ^kb-111111\n\n中间段改过了\n\n最后一段 ^kb-222222\n"
    out = paste_fences(mid, edited)
    assert "中间段改过了" in out
    assert out.count("::: kb-verse") == 1


def test_stamp_duplicate_needles_get_distinct_ids() -> None:
    source = "解\n\n中间\n\n解\n"
    mid = "::: kb-solution\n解\n:::\n\n中间\n\n::: kb-solution\n解\n:::\n"
    new_src, new_mid, stats = stamp_text(source, mid, seed="dup:1")
    assert stats["stamped"] == 2
    assert same_body(new_mid, new_src)
    ids = extract_ids(new_src)
    assert len(ids) == 2
    assert ids[0] != ids[1]
    again_src, again_mid, again = stamp_text(new_src, new_mid, seed="dup:1")
    assert again["changed"] is False
    assert again_src == new_src
    assert again_mid == new_mid


def test_stamp_is_idempotent_and_keeps_same_body() -> None:
    source = "标题行\n\n正文一句\n"
    mid = "::: kb-title\n标题行\n:::\n\n::: kb-center\n正文一句\n:::\n"
    once_src, once_mid, stats = stamp_text(source, mid, seed="book:1")
    assert stats["stamped"] == 2
    assert stats["changed"] is True
    assert same_body(once_mid, once_src)
    assert extract_ids(once_src)
    twice_src, twice_mid, again = stamp_text(once_src, once_mid, seed="book:1")
    assert again["stamped"] == 0
    assert again["already"] == 2
    assert twice_src == once_src
    assert twice_mid == once_mid


def test_strip_ids_leaves_math_and_footnotes() -> None:
    text = "面积是 $n^{2}$ 平方米 ^kb-abcdef\n\n见注释[^1]\n"
    out = strip_ids(text)
    assert "^kb-abcdef" not in out
    assert "$n^{2}$" in out
    assert "[^1]" in out


def test_attach_skips_empty() -> None:
    used: set[str] = set()
    out, ids = attach_ids("\n\n", used, "x")
    assert out == "\n\n"
    assert ids == []


def test_stamp_tool_then_typo_keeps_fence(tmp_path, monkeypatch) -> None:
    book_id, library, page_sync = _home(tmp_path, monkeypatch)
    library.write_page(book_id, 1, "选择性必修\n", sync=False)
    library.write_mid(book_id, 1, "::: kb-title\n选择性必修\n:::\n", sync=False)
    page_sync.sync_page(book_id, 1)
    from app.workspace_module import load

    tool = load("stamp_block_ids")
    out = tool.stamp_block_ids(book_id, apply="fix", root=str(library.textbooks_root()))
    assert out["stamped"] == 1
    source = library.get_page_markdown(book_id, 1) or ""
    assert extract_ids(source)
    bid = extract_ids(source)[0]
    edited = source.replace("择", "X", 1)
    library.write_page(book_id, 1, edited, sync=False)
    synced = page_sync.sync_page(book_id, 1)
    assert synced["action"] in {"paste_mid", "ok", "stamp"}
    mid = library.get_page_mid(book_id, 1) or ""
    assert "::: kb-title" in mid
    assert "选X性必修" in mid
    assert bid in mid
    assert library.get_page_markdown(book_id, 1) == edited


def test_find_span_requires_all_ids() -> None:
    source = "甲 ^kb-aaaaaa\n\n乙\n"
    assert find_id_span(source, ["kb-aaaaaa"]) is not None
    assert find_id_span(source, ["kb-aaaaaa", "kb-bbbbbb"]) is None
