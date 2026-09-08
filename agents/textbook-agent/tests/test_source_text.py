from __future__ import annotations

from app.workspace import ensure_workspace
from app.workspace_module import load


def _mod(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings

    settings.agent_home = str(tmp_path / "home")
    ensure_workspace()
    return load("source_text")


TORN = (
    " kb-list-item\n"
    "::: kb-list-item\n"
    "（2）$n = 2$\n"
    ":::\n"
    ":::\n"
    "\n"
    "::: kb-center\n"
    "![公式](/x.jpg)\n"
    ":::\n"
    "\n"
    " kb-center\n"
    "$V = 22.4$\n"
    ":::\n"
    ":::\n"
)


def test_sanitize_source_keeps_block_ids(tmp_path, monkeypatch) -> None:
    st = _mod(tmp_path, monkeypatch)
    raw = "选择性必修 ^kb-abcdef\n\n见注释[^1] 与 $n^{2}$\n"
    out = st.sanitize_source(raw)
    assert "^kb-abcdef" in out
    assert "[^1]" in out
    assert "$n^{2}$" in out


def test_sanitize_source_drops_all_fence_tokens(tmp_path, monkeypatch) -> None:
    st = _mod(tmp_path, monkeypatch)
    out = st.sanitize_source(TORN)
    assert "::: " not in out
    assert "kb-list-item" not in out
    assert "kb-center" not in out
    assert "（2）$n = 2$" in out
    assert "![公式](/x.jpg)" in out
    assert "$V = 22.4$" in out


def test_sanitize_mid_keeps_well_formed_fences(tmp_path, monkeypatch) -> None:
    st = _mod(tmp_path, monkeypatch)
    out = st.sanitize_mid(TORN)
    assert "::: kb-list-item\n（2）$n = 2$\n:::" in out
    assert "::: kb-center\n![公式](/x.jpg)\n:::" in out
    padded = "\n" + out
    assert "\n kb-list-item\n" not in padded
    assert "\n kb-center\n" not in padded
    assert out.count(":::") == 4


def test_sanitize_mid_unwraps_nested_fences(tmp_path, monkeypatch) -> None:
    st = _mod(tmp_path, monkeypatch)
    raw = (
        "::: kb-list-item\n"
        "::: kb-center\n"
        "3. 下列对生活中常见现象的正确解释是（　　）。\n"
        ":::\n"
    )
    out = st.sanitize_mid(raw)
    assert out == (
        "::: kb-list-item\n"
        "3. 下列对生活中常见现象的正确解释是（　　）。\n"
        ":::\n"
    )


def test_glued_opener_after_prose_is_detached(tmp_path, monkeypatch) -> None:
    st = _mod(tmp_path, monkeypatch)
    raw = "$n$的物质的量为：::: kb-center\n$$x$$\n:::\n"
    mid = st.sanitize_mid(raw)
    assert "::: kb-center\n$$x$$\n:::" in mid
    assert "量为：:::" not in mid
    assert "物质的量为：" in mid
    src = st.sanitize_source(raw)
    assert "::: " not in src
    assert "$$x$$" in src


def test_trailing_fence_name_on_image_line(tmp_path, monkeypatch) -> None:
    st = _mod(tmp_path, monkeypatch)
    raw = (
        "![分液图1.21　从碘水中萃取碘](/x.jpg) kb-center\n"
        "*分液图1.21　从碘水中萃取碘*\n"
    )
    source = st.sanitize_source(raw)
    mid = st.sanitize_mid(raw)
    assert "kb-center" not in source
    assert "kb-center" not in mid
    assert "![分液图1.21　从碘水中萃取碘](/x.jpg)" in source
    assert "*分液图1.21　从碘水中萃取碘*" in source
    issues = st.audit_mid(raw)
    assert any(item["kind"] == "glued_name" for item in issues)


def test_glued_opener_is_stripped_from_source(tmp_path, monkeypatch) -> None:
    st = _mod(tmp_path, monkeypatch)
    out = st.sanitize_source("（2）::: kb-center\n$$x$$\n:::\n")
    assert "::: " not in out
    assert "（2）" in out
    assert "$$x$$" in out


def test_strip_skin_peels_torn_fences(tmp_path, monkeypatch) -> None:
    _mod(tmp_path, monkeypatch)
    from app.mid_text import strip_skin

    out = strip_skin(TORN)
    assert "::: " not in out
    assert "kb-list-item" not in out
    assert "（2）$n = 2$" in out


def test_write_page_refuses_fences(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PAGE_SYNC_WATCH", "0")
    from app.config import settings
    from app import library, page_sync

    settings.agent_home = str(tmp_path / "home")
    settings.page_sync_watch = False
    page_sync.reset_for_tests()
    ensure_workspace()
    book = library.create_book(
        pdf_bytes=b"%PDF-1.4 demo",
        filename="book.pdf",
        book_id="srcgate",
        label="闸",
    )
    library.write_page(book["id"], 1, "::: kb-center\nhello\n:::\n", sync=False)
    stored = library.get_page_markdown(book["id"], 1) or ""
    assert stored == "hello\n"
    assert "::: " not in stored


def test_write_source_does_not_copy_torn_fences(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PAGE_SYNC_WATCH", "0")
    from app.config import settings
    from app import library, page_sync

    settings.agent_home = str(tmp_path / "home")
    settings.page_sync_watch = False
    page_sync.reset_for_tests()
    ensure_workspace()
    book = library.create_book(
        pdf_bytes=b"%PDF-1.4 demo",
        filename="book.pdf",
        book_id="srcpeel",
        label="剥",
    )
    library.write_page(book["id"], 1, "hello\n", sync=False)
    library.write_mid(book["id"], 1, "::: kb-center\nhello\n:::\n", sync=False)
    page_sync.sync_page(book["id"], 1)
    library.write_mid(
        book["id"],
        1,
        "::: kb-center\nhello\n:::\n\n kb-center\n:::\n\nnew sentence\n",
        sync=False,
    )
    out = page_sync.sync_page(book["id"], 1)
    # 从前这里断言 write_source：中间层的残破围栏和多出来的句子会被写进正文。
    # 现在正文只能由人和导入流水线写，派生层的分歧一律留档。
    assert out["action"] == "conflict"
    source = library.get_page_markdown(book["id"], 1) or ""
    mid = library.get_page_mid(book["id"], 1) or ""
    assert source == "hello\n"
    assert "new sentence" not in source
    assert "::: " not in source
    assert "\n kb-center\n" not in ("\n" + source)
    assert "\n kb-center\n" not in ("\n" + mid)
    assert "::: kb-center\nhello\n:::" in mid
    assert "new sentence" in page_sync.conflict_path(book["id"], 1).read_text(encoding="utf-8")
