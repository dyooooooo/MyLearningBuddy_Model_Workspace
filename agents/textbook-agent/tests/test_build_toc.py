from __future__ import annotations

import sys

from app import library
from app.config import settings
from app.workspace import ensure_workspace


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    settings.agent_home = str(home)
    ensure_workspace()
    sys.path.insert(0, str(settings.tools_dir))
    return home


CHAPTERS = [
    ("第1章 运动的描述", 1, ["质点 物理模型", "位移与位置", "速度", "加速度"]),
    ("第2章 匀变速直线运动", 33, ["自由落体", "匀变速规律", "图像", "追及问题"]),
    ("第3章 相互作用与力的平衡", 46, ["生活中常见的力", "力的合成", "力的分解", "共点力的平衡"]),
    ("第4章 牛顿运动定律", 74, ["牛顿第一定律", "牛顿第二定律", "力学单位制", "牛顿第三定律"]),
]
CN = ["一", "二", "三", "四"]


def _make_book(book_id: str = "phy4x4") -> tuple[list[str], list[int]]:
    """4 章 × 4 节：印刷目录在第 1 页，每章一页章首页，每节各占一页。"""
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id=book_id, label="四章四节")
    toc_lines = ["目录"]
    want_labels: list[str] = []
    want_pages: list[int] = []
    body: list[tuple[int, str]] = []
    pdf_page = 2  # 第 1 页是目录页，正文从第 2 页起 → offset = 1
    for chapter, chapter_print, sections in CHAPTERS:
        toc_lines.append(f"{chapter} / {chapter_print}")
        body.append((pdf_page, f"{chapter}\n本章导语。\n"))
        pdf_page += 1
        print_page = chapter_print
        for index, title in enumerate(sections):
            print_page += 1
            label = f"第{CN[index]}节 {title}"
            toc_lines.append(f"{label} /{print_page}")
            want_labels.append(label)
            want_pages.append(pdf_page)
            body.append((pdf_page, f"{label}\n正文若干。\n"))
            pdf_page += 1
    library.write_page(book_id, 1, "\n".join(toc_lines) + "\n")
    for page_no, text in body:
        library.write_page(book_id, page_no, text)
    library.patch_book(book_id, status="ready", page_count=pdf_page - 1)
    (library.book_dir(book_id) / "mineru_toc.json").write_text(
        '{"toc_pages": [1], "print_to_pdf": {}}',
        encoding="utf-8",
    )
    return want_labels, want_pages


def test_section_pdf_pages_do_not_collide_across_chapters(tmp_path, monkeypatch) -> None:
    """每章的「第一节」都是同一个节号，num 必须带章号才不会互相覆盖。"""
    _home(tmp_path, monkeypatch)
    want_labels, want_pages = _make_book()
    from build_toc import build_toc

    out = build_toc(str(settings.home), "phy4x4")
    assert out["ok"] is True

    toc = library.read_toc("phy4x4")
    sections = [
        row
        for row in toc["entries"]
        if int(row.get("level") or 0) == 1 and str(row.get("label") or "").startswith("第")
    ]
    assert [row["label"] for row in sections] == want_labels
    pages = [int(row.get("pdf_page") or 0) for row in sections]
    assert pages == want_pages
    assert len(set(pages)) == 16, f"节页码有重复：{pages}"
    assert pages == sorted(pages)


def test_section_intervals_cover_every_section(tmp_path, monkeypatch) -> None:
    """节页码不塌，兄弟节区间才会是 16 个而不是 5 个。"""
    _home(tmp_path, monkeypatch)
    _make_book()
    from build_toc import build_toc

    build_toc(str(settings.home), "phy4x4")
    from app import toc_extract

    intervals = toc_extract.sibling_intervals(library.read_toc("phy4x4")["entries"])
    assert len(intervals) == 16
    assert len({item["key"] for item in intervals}) == 16
    for item in intervals:
        assert int(item["end_pdf"]) > int(item["start_pdf"])


def test_agent_kids_are_marked_from_body(tmp_path, monkeypatch) -> None:
    """toc_agent.json 里的小节进 toc.json 时必须带 from_body 标。

    `build_toc` 会给这些正文扒来的小节按 offset 反推印张页，所以「有没有 print_page」
    区分不出来源；`write_toc_pages` 靠这个标才知道哪些不能印回目录页。
    """
    import json

    _home(tmp_path, monkeypatch)
    _make_book()
    from build_toc import build_toc

    build_toc(str(settings.home), "phy4x4")
    (library.book_dir("phy4x4") / "toc_agent.json").write_text(
        json.dumps(
            {
                "source": "printed",
                "skeleton_entries": [],
                "intervals": [
                    {
                        "start_pdf": 3,
                        "end_pdf": 4,
                        "section_label": "第一节 质点 物理模型",
                        "entries": [{"label": "何种条件下可抽象为质点", "pdf_page": 3, "level": 2}],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    build_toc(str(settings.home), "phy4x4")

    kids = [
        row
        for row in library.read_toc("phy4x4")["entries"]
        if str(row.get("label") or "") == "何种条件下可抽象为质点"
    ]
    assert len(kids) == 1, "正文小节没有并进 toc.json"
    assert kids[0].get("from_body") is True
    assert kids[0].get("print_page") is not None, "反推的印张页仍应保留（左栏要显示）"

    printed = [row for row in library.read_toc("phy4x4")["entries"] if not row.get("from_body")]
    assert all(int(row.get("level") or 0) <= 1 for row in printed)


def test_parse_catalog_section_num_carries_chapter(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    import build_toc as bt

    lines = [
        "目录",
        "第1章 运动的描述 / 1",
        "第一节 质点 /2",
        "第2章 匀变速直线运动 / 33",
        "第一节 自由落体 /34",
    ]
    _pages, skeleton, _subs = bt._parse_catalog([(1, tmp_path / "0001.md", lines)], known_toc_pages=[1])
    nums = [str(row.get("num") or "") for row in skeleton if row.get("kind") == "section"]
    assert nums == ["jie:1:1", "jie:2:1"]
    assert len(set(nums)) == len(nums)


def test_parse_catalog_splits_glued_jie_and_keeps_physics_shape(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    import build_toc as bt

    lines = [
        "目录",
        "第 1 章 走进生物学 1",
        "第 1 节生物学是与人类生活密切相关的自然科学 2",
        "第 2 节实验探究是学习生物学的重要途径 8",
        "探究 · 实验 1-1 用高倍镜观察动植物细胞 12",
        "第 1 节 细胞由质膜包裹 50第 2 节 细胞各部分结构既分工又合作 55",
    ]
    _pages, skeleton, _subs = bt._parse_catalog(
        [(6, tmp_path / "0006.md", lines)],
        known_toc_pages=[6],
    )
    labels = [str(row.get("label") or "") for row in skeleton]
    assert any("第1章" in item and "走进生物学" in item for item in labels)
    assert sum(1 for row in skeleton if row.get("kind") == "section") >= 4
    assert any("探究" in item and "1-1" in item for item in labels)


def test_assign_jie_by_title_not_zip_order() -> None:
    import build_toc as bt

    rows = [
        {"label": "第 1 节 质点 物理模型", "kind": "section", "num": "jie:1:1"},
        {"label": "第 2 节 自由落体", "kind": "section", "num": "jie:2:1"},
    ]
    body = [(10, "本章导语"), (12, "自由落体"), (3, "质点 物理模型")]
    bt._assign_jie_by_title(rows, body)
    assert rows[0]["pdf_page"] == 3
    assert rows[1]["pdf_page"] == 12
