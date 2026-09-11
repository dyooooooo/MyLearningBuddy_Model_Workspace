from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from app.workspace import ensure_workspace

_TOOLS = Path(__file__).resolve().parents[1] / "workspace" / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
import official_lib as official  # noqa: E402


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app import surfaces

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    surfaces.reset_for_tests()
    ensure_workspace()
    return settings, surfaces


def _text_block(content: str, typ: str = "text", level: int | None = None) -> dict:
    block = {
        "type": typ,
        "index": 0,
        "lines": [{"spans": [{"type": "text", "content": content}]}],
    }
    if level is not None:
        block["level"] = level
    return block


def _layout(pages: list[dict]) -> dict:
    return {"pdf_info": pages, "_backend": "hybrid", "_version_name": "3.4.4"}


def test_official_line_does_not_import_v2md(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    from app.workspace_module import load

    slicer = load("official_slice")
    lib = load("official_lib")
    src = Path(slicer.__file__).read_text(encoding="utf-8")
    lib_src = Path(lib.__file__).read_text(encoding="utf-8")
    assert "import v2md" not in src and "from v2md" not in src
    assert "import v2md" not in lib_src and "from v2md" not in lib_src
    assert not hasattr(slicer, "load_v2_pages")
    assert not hasattr(lib, "load_v2_pages")
    assert hasattr(lib, "load_v1_pages")
    assert hasattr(lib, "render_official_page")
    assert not hasattr(lib, "page_starts")
    assert not hasattr(lib, "find_official_span")


def test_official_slice_works_without_v2_json(tmp_path, monkeypatch) -> None:
    settings, _surfaces = _home(tmp_path, monkeypatch)
    book_id = "officialonly"
    book = settings.textbooks_dir / book_id
    mineru = book / "mineru"
    mineru.mkdir(parents=True)
    (mineru / "demo_content_list.json").write_text(
        json.dumps(
            [
                {"type": "text", "text": "氓之蚩蚩，抱布贸丝", "page_idx": 0, "bbox": [0, 0, 10, 10]},
                {"type": "page_footnote", "text": "① 选自《诗经注析》。", "page_idx": 0},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (mineru / "full.md").write_text("## 氓 $^{①}$\n\n氓之蚩蚩，抱布贸丝\n", encoding="utf-8")
    (book / "meta.json").write_text(
        json.dumps({"id": book_id, "status": "ready", "page_count": 1}),
        encoding="utf-8",
    )
    from app.workspace_module import load

    out = load("official_slice").slice_official(book_id, 1, root=str(settings.textbooks_dir))
    assert out["ok"] is True
    assert out["via"] == "v1"
    assert "氓之蚩蚩" in out["markdown"]
    assert "诗经注析" not in out["markdown"]
    assert not list(mineru.glob("*_content_list_v2.json"))


def test_layout_page_stays_on_its_para_blocks(tmp_path: Path) -> None:
    book = tmp_path / "laybook"
    mineru = book / "mineru"
    mineru.mkdir(parents=True)
    (mineru / "layout.json").write_text(
        json.dumps(
            _layout(
                [
                    {
                        "page_idx": 0,
                        "para_blocks": [_text_block("夜重复这一恶性循环。")],
                        "discarded_blocks": [{"type": "header", "lines": []}],
                    },
                    {
                        "page_idx": 1,
                        "para_blocks": [
                            _text_block("《百年孤独》是一部充满奇思妙想的小说有着奇特的人物和离奇的故事")
                        ],
                    },
                ]
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    a, via_a = official.render_official_page(book, 1)
    b, via_b = official.render_official_page(book, 2)
    assert via_a == via_b == "layout"
    assert "恶性循环" in a
    assert "百年孤独" not in a
    assert "百年孤独" in b
    assert "恶性循环" not in b


def test_layout_beats_v1_on_same_page(tmp_path: Path) -> None:
    book = tmp_path / "prefer"
    mineru = book / "mineru"
    mineru.mkdir(parents=True)
    (mineru / "layout.json").write_text(
        json.dumps(
            _layout([{"page_idx": 0, "para_blocks": [_text_block("版式正文属于这一页")]}]),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (mineru / "demo_content_list.json").write_text(
        json.dumps([{"type": "text", "text": "清单正文不该盖过版式", "page_idx": 0}]),
        encoding="utf-8",
    )
    md, via = official.render_official_page(book, 1)
    assert via == "layout"
    assert "版式正文" in md
    assert "清单正文" not in md


def test_empty_para_blocks_cannot_open(tmp_path: Path) -> None:
    book = tmp_path / "chrome"
    mineru = book / "mineru"
    mineru.mkdir(parents=True)
    (mineru / "layout.json").write_text(
        json.dumps(
            _layout(
                [
                    {"page_idx": 0, "para_blocks": [_text_block("氓之蚩蚩抱布贸丝直到第三章结束")]},
                    {
                        "page_idx": 1,
                        "para_blocks": [],
                        "discarded_blocks": [
                            {
                                "type": "page_footnote",
                                "lines": [{"spans": [{"type": "text", "content": "① 选自诗经注析"}]}],
                            }
                        ],
                    },
                ]
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    empty, via = official.render_official_page(book, 2)
    assert via == "layout"
    assert empty == ""
    from official_slice import slice_official

    out = slice_official("chrome", 2, root=str(tmp_path))
    assert out["ok"] is False
    assert out["markdown"] == ""


def test_missing_layout_page_does_not_fall_to_v1(tmp_path: Path) -> None:
    book = tmp_path / "gap"
    mineru = book / "mineru"
    mineru.mkdir(parents=True)
    (mineru / "layout.json").write_text(
        json.dumps(_layout([{"page_idx": 0, "para_blocks": [_text_block("只有第一页")]}])),
        encoding="utf-8",
    )
    (mineru / "demo_content_list.json").write_text(
        json.dumps([{"type": "text", "text": "第二页只在清单里", "page_idx": 1}]),
        encoding="utf-8",
    )
    md, via = official.render_official_page(book, 2)
    assert via == "layout"
    assert md == ""


def test_title_and_equation_from_layout(tmp_path: Path) -> None:
    book = tmp_path / "eqbook"
    mineru = book / "mineru"
    mineru.mkdir(parents=True)
    eq = {
        "type": "interline_equation",
        "lines": [
            {
                "spans": [
                    {
                        "type": "interline_equation",
                        "content": "\\sin A = a/c",
                    }
                ]
            }
        ],
    }
    (mineru / "layout.json").write_text(
        json.dumps(
            _layout(
                [
                    {
                        "page_idx": 0,
                        "para_blocks": [_text_block("正弦定义", "title", 1), eq],
                    }
                ]
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    md, _via = official.render_official_page(book, 1)
    assert md.startswith("# 正弦定义")
    assert "$$" in md
    assert "sin A" in md


def test_table_and_image_from_layout(tmp_path: Path) -> None:
    book = tmp_path / "vis"
    mineru = book / "mineru"
    mineru.mkdir(parents=True)
    image = {
        "type": "image",
        "index": 1,
        "blocks": [
            {
                "type": "image_body",
                "index": 1,
                "lines": [{"spans": [{"type": "image", "image_path": "abc.jpg"}]}],
            },
            {
                "type": "image_caption",
                "index": 2,
                "lines": [{"spans": [{"type": "text", "content": "图1 搬家"}]}],
            },
        ],
    }
    table = {
        "type": "table",
        "index": 2,
        "blocks": [
            {
                "type": "table_body",
                "index": 1,
                "lines": [
                    {
                        "spans": [
                            {
                                "type": "table",
                                "html": '<table><tr><td>虚词</td></tr></table>',
                                "image_path": "tab.jpg",
                            }
                        ]
                    }
                ],
            }
        ],
    }
    (mineru / "layout.json").write_text(
        json.dumps(_layout([{"page_idx": 0, "para_blocks": [image, table]}])),
        encoding="utf-8",
    )
    md, _via = official.render_official_page(book, 1)
    assert "![](images/abc.jpg)" in md
    assert "图1 搬家" in md
    assert "<table>" in md
    assert "虚词" in md


def test_v1_skips_chrome_and_keeps_verse() -> None:
    blocks = [
        {"type": "text", "text": "氓之蚩蚩，抱布贸丝", "page_idx": 0},
        {"type": "page_footnote", "text": "① 选自《诗经注析》。", "page_idx": 0},
        {"type": "page_header", "text": "普通高中教科书", "page_idx": 0},
    ]
    md = official.make_from_v1_blocks(blocks)
    assert "氓之蚩蚩" in md
    assert "诗经注析" not in md
    assert "普通高中教科书" not in md


def test_rewrite_markdown_and_html_images() -> None:
    md = '![](images/abc.jpg)\n<img src="images/abc.jpg">\n'
    out = official.rewrite_image_urls(md, "book1")
    assert "images/abc.jpg" not in out
    assert out.count("/api/v1/kb/proxy/asset?key=") == 2


def test_slice_does_not_write_three_lines(tmp_path: Path) -> None:
    book = tmp_path / "nowrite"
    mineru = book / "mineru"
    mineru.mkdir(parents=True)
    (book / "pages").mkdir()
    (book / "v2md" / "pages").mkdir(parents=True)
    (mineru / "full.md").write_text("全书稿不应被改\n", encoding="utf-8")
    (mineru / "demo_content_list.json").write_text(
        json.dumps([{"type": "text", "text": "氓之蚩蚩抱布贸丝直到第三章结束", "page_idx": 0}]),
        encoding="utf-8",
    )
    from official_slice import slice_official

    before_full = (mineru / "full.md").read_text(encoding="utf-8")
    out = slice_official("nowrite", 1, root=str(tmp_path))
    assert out["ok"] is True
    assert (mineru / "full.md").read_text(encoding="utf-8") == before_full
    assert list((book / "pages").iterdir()) == []
    assert list((book / "v2md" / "pages").iterdir()) == []


def test_slice_rejects_page_zero(tmp_path: Path) -> None:
    book = tmp_path / "zero"
    (book / "mineru").mkdir(parents=True)
    (book / "mineru" / "full.md").write_text("abc\n", encoding="utf-8")
    (book / "mineru" / "demo_content_list.json").write_text("[]", encoding="utf-8")
    from official_slice import slice_official

    out = slice_official("zero", 0, root=str(tmp_path))
    assert out["ok"] is False
    assert out["markdown"] == ""


def _span_block(spans: list[dict], typ: str = "text") -> dict:
    return {
        "type": typ,
        "index": 0,
        "lines": [{"spans": spans}],
    }


def test_cjk_spans_join_without_spaces(tmp_path: Path) -> None:
    """中文 span 之间、公式与汉字之间都不加空格（防「撕裂」）。"""
    book = tmp_path / "cjkbook"
    mineru = book / "mineru"
    mineru.mkdir(parents=True)
    block = _span_block(
        [
            {"type": "text", "content": "处于"},
            {"type": "inline_equation", "content": "2 \\mathrm{p}"},
            {"type": "text", "content": "轨道上的电子"},
        ]
    )
    (mineru / "layout.json").write_text(
        json.dumps(_layout([{"page_idx": 0, "para_blocks": [block]}]), ensure_ascii=False),
        encoding="utf-8",
    )
    md, via = official.render_official_page(book, 1)
    assert via == "layout"
    assert "处于$2 \\mathrm{p}$轨道上的电子" in md
    assert "处于 $" not in md


def test_english_spans_keep_word_spaces(tmp_path: Path) -> None:
    """西文保留词间空格与公式两侧空格。"""
    book = tmp_path / "enbook"
    mineru = book / "mineru"
    mineru.mkdir(parents=True)
    block = _span_block(
        [
            {"type": "text", "content": "the energy"},
            {"type": "inline_equation", "content": "E"},
            {"type": "text", "content": "is"},
        ]
    )
    (mineru / "layout.json").write_text(
        json.dumps(_layout([{"page_idx": 0, "para_blocks": [block]}]), ensure_ascii=False),
        encoding="utf-8",
    )
    md, _via = official.render_official_page(book, 1)
    assert "the energy $E$ is" in md


def test_layout_cache_respects_mtime_and_bounds(tmp_path: Path, monkeypatch) -> None:
    """缓存按 mtime 失效、LRU 有上限、页查找不再每次全量扫。"""
    import official_lib

    monkeypatch.setattr(official_lib, "_LAYOUT_CACHE_MAX", 2)
    official_lib._layout_cache.clear()
    try:
        books = []
        for i in range(3):
            book = tmp_path / f"cache{i}"
            mineru = book / "mineru"
            mineru.mkdir(parents=True)
            (mineru / "layout.json").write_text(
                json.dumps(
                    _layout([{"page_idx": 0, "para_blocks": [_text_block(f"缓存书{i}正文")]}]),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            books.append(book)
        # 读 3 本，上限 2 → 最早一本被淘汰
        for book in books:
            official.render_official_page(book, 1)
        assert len(official_lib._layout_cache) == 2
        # mtime 变化（重新写入）→ 缓存失效，内容跟着变
        target = books[-1]
        (target / "mineru" / "layout.json").write_text(
            json.dumps(_layout([{"page_idx": 0, "para_blocks": [_text_block("刷新后的正文")]}])),
            encoding="utf-8",
        )
        os.utime(target / "mineru" / "layout.json", (2_000_000_000, 2_000_000_000))
        md, _via = official.render_official_page(target, 1)
        assert "刷新后的正文" in md
    finally:
        official_lib._layout_cache.clear()


def test_no_write_on_read(tmp_path: Path) -> None:
    book = tmp_path / "readonly"
    mineru = book / "mineru"
    mineru.mkdir(parents=True)
    (mineru / "layout.json").write_text(
        json.dumps(_layout([{"page_idx": 0, "para_blocks": [_text_block("只读出稿")]}])),
        encoding="utf-8",
    )
    before = {p.name: p.read_bytes() for p in mineru.iterdir()}
    official.render_official_page(book, 1)
    assert {p.name: p.read_bytes() for p in mineru.iterdir()} == before
