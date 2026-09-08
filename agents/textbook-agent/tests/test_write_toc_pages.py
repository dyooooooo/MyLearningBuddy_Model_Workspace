from __future__ import annotations

import sys

from app import library, page_sync, surfaces
from app.config import settings
from app.workspace import ensure_workspace


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    settings.agent_home = str(home)
    surfaces.reset_for_tests()
    ensure_workspace()
    return home


def _book(book_id: str = "demo") -> None:
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id=book_id, label="演示")
    library.write_page(book_id, 1, "目录\n第一单元\n沁园春·长沙 9\n")
    library.write_toc(
        book_id,
        {
            "entries": [
                {"label": "第一单元", "print_page": None, "pdf_page": 8, "level": 0, "is_chapter": True},
                {"label": "沁园春·长沙", "print_page": 9, "pdf_page": 16, "level": 2, "is_chapter": False},
                {"label": "跨册附录", "print_page": 117, "pdf_page": 0, "level": 1, "is_chapter": False},
            ],
            "print_to_pdf": {"9": 16},
            "toc_pages": [1],
            "source": "printed",
        },
    )


def test_write_toc_pages_fix_uses_print_page(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    _book()
    sys.path.insert(0, str(settings.tools_dir))
    from write_toc_pages import write_toc_pages

    out = write_toc_pages("demo", apply="fix")
    assert out["ok"] is True
    assert out["wrote"] is True
    source = library.book_dir("demo").joinpath("pages", "0001.md").read_text(encoding="utf-8")
    mid = library.book_dir("demo").joinpath("mid", "0001.md").read_text(encoding="utf-8")
    assert source.startswith("目录\n")
    assert "沁园春·长沙 9\n" in source
    assert "16" not in source
    assert mid.startswith("::: kb-page-toc\n")
    assert mid.rstrip().endswith(":::")
    stamp = page_sync.read_stamp("demo", 1)
    assert stamp["source_sha"]
    assert stamp["mid_sha"]
    assert out["unmapped_count"] == 1
    assert out["unmapped"][0]["print_page"] == 117


def test_write_toc_pages_scan_warns_empty_pages(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    _book()
    library.write_toc(
        "demo",
        {
            "entries": [{"label": "第一单元", "print_page": 1, "level": 0, "is_chapter": True}],
            "print_to_pdf": {},
            "toc_pages": [],
            "source": "printed",
        },
    )
    sys.path.insert(0, str(settings.tools_dir))
    from write_toc_pages import write_toc_pages

    out = write_toc_pages("demo", apply="scan")
    assert out["empty_toc_pages"] is True
    assert out["wrote"] is False


def test_write_toc_pages_shrink_guard(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    _book()
    library.write_page(
        "demo",
        1,
        "目录\n第1章 运动的描述 / 1\n第一节 质点 / 2\n第二节 时间 / 8\n第三节 位移 / 12\n第2章 匀变速 / 25\n",
    )
    library.write_toc(
        "demo",
        {
            "entries": [
                {"label": "第1章 运动的描述 / 1", "print_page": None, "level": 0, "is_chapter": True},
                {"label": "附录", "print_page": None, "level": 0, "is_chapter": True},
            ],
            "print_to_pdf": {},
            "toc_pages": [1],
            "source": "printed",
        },
    )
    sys.path.insert(0, str(settings.tools_dir))
    from write_toc_pages import write_toc_pages

    blocked = write_toc_pages("demo", apply="fix")
    assert blocked["blocked"] is True
    assert blocked["wrote"] is False
    source = library.book_dir("demo").joinpath("pages", "0001.md").read_text(encoding="utf-8")
    assert "第一节 质点 / 2" in source
    forced = write_toc_pages("demo", apply="fix", force=True)
    assert forced["blocked"] is False
    assert forced["wrote"] is True
    source = library.book_dir("demo").joinpath("pages", "0001.md").read_text(encoding="utf-8")
    assert "第一节 质点 / 2" not in source
    assert "附录" in source


_TWO_PAGE_ENTRIES = [
    {"label": "第1章 运动的描述", "print_page": 1, "pdf_page": 9, "level": 0, "is_chapter": True},
    {"label": "第一节 质点", "print_page": 2, "pdf_page": 9, "level": 1, "is_chapter": False},
    {"label": "第二节 位移", "print_page": 8, "pdf_page": 12, "level": 1, "is_chapter": False},
    {"label": "第2章 匀变速", "print_page": 25, "pdf_page": 33, "level": 0, "is_chapter": True},
    {"label": "第一节 自由落体", "print_page": 26, "pdf_page": 34, "level": 1, "is_chapter": False},
]


def _two_page_book(extra: list[dict] | None = None) -> None:
    """印刷目录跨两页，且印刷页写「第一章」而 toc.json 写「第1章」。"""
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="two", label="两页目录")
    library.write_page("two", 6, "目录\n第一章 运动的描述 / 1\n第一节 质点 /2\n第二节 位移 /8\n")
    library.write_page("two", 7, "第二章 匀变速 / 25\n第一节 自由落体 /26\n")
    library.write_toc(
        "two",
        {
            "entries": _TWO_PAGE_ENTRIES + (extra or []),
            "print_to_pdf": {"1": 9, "2": 9, "8": 12, "25": 33, "26": 34},
            "toc_pages": [6, 7],
            "source": "printed",
        },
    )


def test_decorative_figures_survive_the_rewrite(tmp_path, monkeypatch) -> None:
    """目录页配的装饰插图要原位留住 —— 整页按 toc.json 重写不能把它们冲掉。

    第 6 页插图在目录行之后、第 7 页在之前，两种位置都要保住。
    """
    _home(tmp_path, monkeypatch)
    _two_page_book()
    library.write_page(
        "two",
        6,
        "目录\n第一章 运动的描述 / 1\n第一节 质点 /2\n第二节 位移 /8\n\n![插图](tail.jpg)\n",
    )
    library.write_page(
        "two",
        7,
        "![插图](lead.jpg)\n\n第二章 匀变速 / 25\n第一节 自由落体 /26\n",
    )
    sys.path.insert(0, str(settings.tools_dir))
    from write_toc_pages import write_toc_pages

    assert write_toc_pages("two", apply="fix")["wrote"] is True
    page6 = library.book_dir("two").joinpath("pages", "0006.md").read_text(encoding="utf-8")
    page7 = library.book_dir("two").joinpath("pages", "0007.md").read_text(encoding="utf-8")
    assert "![插图](tail.jpg)" in page6
    assert page6.index("第一节 质点 2") < page6.index("![插图](tail.jpg)")
    assert "![插图](lead.jpg)" in page7
    assert page7.index("![插图](lead.jpg)") < page7.index("第2章 匀变速")

    # 再跑一次不能重复插图，也不能把它挪位置
    assert write_toc_pages("two", apply="fix")["wrote"] is True
    again = library.book_dir("two").joinpath("pages", "0006.md").read_text(encoding="utf-8")
    assert again == page6


def test_chapter_page_number_survives_the_rewrite(tmp_path, monkeypatch) -> None:
    """章行要带印刷页码 —— 书上印的就是 `第一章 运动的描述 / 1`。

    原来章行无条件不带页码，重写就把它抹了。
    """
    _home(tmp_path, monkeypatch)
    _two_page_book()
    sys.path.insert(0, str(settings.tools_dir))
    from write_toc_pages import write_toc_pages

    assert write_toc_pages("two", apply="fix")["wrote"] is True
    page6 = library.book_dir("two").joinpath("pages", "0006.md").read_text(encoding="utf-8")
    assert "第1章 运动的描述 1" in page6


def test_rewrite_then_rebuild_keeps_chapter_pages(tmp_path, monkeypatch) -> None:
    """写目录页 -> 再建一次骨架，章的 print_page 不能掉。

    这是整条链的幂等性。章行丢页码时跑一遍看不出来：`toc.json` 是在重写**之前**从
    原文建的，页码还在；等 `build_toc` 第二次读到重写后的页面，章的 `print_page`
    才一起变 null，`print_to_pdf` 跟着掉一条。所以必须跑完整来回才测得出来。

    两页各给足 3 条目录行：`_is_toc_page` 要 `hits >= 3` 才认这一页是目录页，
    行数不够的话第二页压根不参与重建，测的就不是幂等而是漏检了。
    """
    _home(tmp_path, monkeypatch)
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="rt", label="来回")
    library.write_page(
        "rt", 6, "目录\n第一章 运动的描述 / 1\n第一节 质点 /2\n第二节 位移 /8\n"
    )
    library.write_page(
        "rt", 7, "第二章 匀变速 / 25\n第一节 自由落体 /26\n第二节 匀变速规律 /31\n"
    )
    library.write_toc(
        "rt",
        {
            "entries": [
                {"label": "第1章 运动的描述", "print_page": 1, "pdf_page": 9, "level": 0, "is_chapter": True},
                {"label": "第一节 质点", "print_page": 2, "pdf_page": 9, "level": 1, "is_chapter": False},
                {"label": "第二节 位移", "print_page": 8, "pdf_page": 12, "level": 1, "is_chapter": False},
                {"label": "第2章 匀变速", "print_page": 25, "pdf_page": 33, "level": 0, "is_chapter": True},
                {"label": "第一节 自由落体", "print_page": 26, "pdf_page": 34, "level": 1, "is_chapter": False},
                {"label": "第二节 匀变速规律", "print_page": 31, "pdf_page": 39, "level": 1, "is_chapter": False},
            ],
            "print_to_pdf": {"1": 9, "2": 9, "8": 12, "25": 33, "26": 34, "31": 39},
            "toc_pages": [6, 7],
            "source": "printed",
        },
    )
    sys.path.insert(0, str(settings.tools_dir))
    from build_toc import build_toc
    from write_toc_pages import write_toc_pages

    def chapters() -> dict[str, object]:
        return {
            e["label"]: e.get("print_page")
            for e in library.read_toc("rt")["entries"]
            if e.get("is_chapter")
        }

    before = chapters()
    assert before == {"第1章 运动的描述": 1, "第2章 匀变速": 25}

    assert write_toc_pages("rt", apply="fix")["wrote"] is True
    assert build_toc(root=str(settings.agent_home), book_id="rt").get("ok") is not False

    assert library.read_toc("rt").get("toc_pages") == [6, 7]
    assert chapters() == before, f"重建后章页码掉了：{chapters()}"


def test_split_matches_across_chapter_numbering(tmp_path, monkeypatch) -> None:
    """「第一章」对「第1章」要归一后对上，否则两页全塞进第一页。"""
    _home(tmp_path, monkeypatch)
    _two_page_book()
    sys.path.insert(0, str(settings.tools_dir))
    from write_toc_pages import write_toc_pages

    out = write_toc_pages("two", apply="fix")
    assert out["split_failed"] is False
    assert out["wrote"] is True
    page6 = library.book_dir("two").joinpath("pages", "0006.md").read_text(encoding="utf-8")
    page7 = library.book_dir("two").joinpath("pages", "0007.md").read_text(encoding="utf-8")
    assert "第一节 质点 2" in page6
    assert "第2章 匀变速" not in page6
    assert "第2章 匀变速" in page7
    assert "第一节 自由落体 26" in page7


def test_subsections_never_reach_printed_pages(tmp_path, monkeypatch) -> None:
    """toc_extract 从正文扒的 level>=2 小节只活在 toc.json 与左栏，不进印刷目录页。

    判据是 `from_body`，不是有没有 `print_page`：`build_toc` 会给正文扒来的小节按
    offset 反推印张页，所以两批条目都有页码，光看页码区分不出来源。
    """
    _home(tmp_path, monkeypatch)
    _two_page_book(
        extra=[
            # 正文扒来的：即便 build_toc 反推出了印张页，也不该印回目录页
            {"label": "何种条件下可抽象为质点", "print_page": 2, "pdf_page": 9, "level": 2, "from_body": True},
            {"label": "位移与路程的区别", "print_page": 5, "pdf_page": 12, "level": 2, "from_body": True},
            # 印刷目录上确实有的深层条目（语文教材的篇名），没有 from_body 标，必须照印
            {"label": "沁园春·长沙", "print_page": 30, "pdf_page": 40, "level": 2},
        ]
    )
    sys.path.insert(0, str(settings.tools_dir))
    from write_toc_pages import write_toc_pages

    out = write_toc_pages("two", apply="fix")
    assert out["entries"] == 6
    assert out["entries_all"] == 8
    body = "".join(
        library.book_dir("two").joinpath("pages", f"{n:04d}.md").read_text(encoding="utf-8")
        for n in (6, 7)
    )
    assert "何种条件下可抽象为质点" not in body
    assert "位移与路程的区别" not in body
    assert "沁园春·长沙 30" in body


def test_split_failed_leaves_pages_untouched(tmp_path, monkeypatch) -> None:
    """切不出分页时不写盘，宁可不动原文（第 6 页的插图不能被覆盖）。"""
    _home(tmp_path, monkeypatch)
    _two_page_book()
    library.write_page("two", 6, "![插图](a.jpg)\n完全对不上的一行\n")
    library.write_page("two", 7, "![插图](b.jpg)\n也对不上\n")
    sys.path.insert(0, str(settings.tools_dir))
    from write_toc_pages import write_toc_pages

    out = write_toc_pages("two", apply="fix")
    assert out["split_failed"] is True
    assert out["wrote"] is False
    page6 = library.book_dir("two").joinpath("pages", "0006.md").read_text(encoding="utf-8")
    assert "![插图](a.jpg)" in page6
    assert "第一节 质点" not in page6


def test_grow_guard_blocks_page_stuffing(tmp_path, monkeypatch) -> None:
    """单页从 2 条涨到 20 条时拦下来：这正是插图被 80 条目录覆盖的口子。"""
    _home(tmp_path, monkeypatch)
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="grow", label="暴涨")
    library.write_page("grow", 6, "目录\n第1章 运动的描述 1\n")
    library.write_toc(
        "grow",
        {
            "entries": [
                {"label": "第1章 运动的描述", "print_page": 1, "level": 0, "is_chapter": True},
            ]
            + [
                {"label": f"第{i}节 小节{i}", "print_page": i + 1, "level": 1, "is_chapter": False}
                for i in range(1, 20)
            ],
            "print_to_pdf": {"1": 9},
            "toc_pages": [6],
            "source": "printed",
        },
    )
    sys.path.insert(0, str(settings.tools_dir))
    from write_toc_pages import write_toc_pages

    out = write_toc_pages("grow", apply="fix")
    assert out["blocked"] is True
    assert out["wrote"] is False
    assert out["blocked_pages"][0]["warning"] == "grow_guard"
    page6 = library.book_dir("grow").joinpath("pages", "0006.md").read_text(encoding="utf-8")
    assert "第19节" not in page6
    forced = write_toc_pages("grow", apply="fix", force=True)
    assert forced["wrote"] is True
    assert "第19节" in library.book_dir("grow").joinpath("pages", "0006.md").read_text(encoding="utf-8")
