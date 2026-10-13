from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "workspace" / "tools"
sys.path.insert(0, str(TOOLS))

import ir_toc  # noqa: E402


def _page(book: Path, no: int, titles: list[str], printed: int | None = None) -> None:
    blocks = [{"id": f"b{i}", "type": "title", "text": t} for i, t in enumerate(titles)]
    chrome = {"page_numbers": [{"text": str(printed)}]} if printed is not None else {}
    path = book / "ir" / "pages" / f"{no:04d}.json"
    path.write_text(json.dumps({"blocks": blocks, "chrome": chrome}, ensure_ascii=False), encoding="utf-8")


def _entry(label: str, level: int, page: int | None, chapter: bool = False) -> dict:
    return {"label": label, "level": level, "print_page": page, "pdf_page": 2, "is_chapter": chapter}


def _book(tmp_path: Path, entries: list[dict], offset: int | None = 5) -> Path:
    book = tmp_path / "textbooks" / "book1"
    (book / "ir" / "pages").mkdir(parents=True)
    doc = {"found": True, "toc_pages": [2], "page_offset": offset, "entries": entries}
    (book / "toc.print").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    _page(book, 1, ["生物学", "选择性必修 3"])
    _page(book, 2, ["目录", "第1章 发酵工程 1"])
    return book


def _rows(toc: dict) -> list[tuple]:
    return [(e["level"], e["title"], e["pdf_page"], e.get("page_from"), bool(e.get("from_body"))) for e in toc["entries"]]


def test_skeleton_rows_come_from_print_and_pages_from_body(tmp_path: Path) -> None:
    book = _book(tmp_path, [
        _entry("第1章 发酵工程", 0, 1, True),
        _entry("第1节 获得纯种微生物", 1, 2),
        _entry("探究· 实验1-1 培养基的配制", 2, 6),
    ])
    _page(book, 6, [])
    _page(book, 7, ["第1节 获得纯种微生物", "抗生素的生产菌种"], 2)
    _page(book, 10, ["1-1 培养基的配制"], 5)
    toc = ir_toc.build_toc(book)
    assert toc["source"] == "ir_toc_print" and toc["toc_pages"] == [2] and toc["print_sha"]
    assert _rows(toc) == [
        (0, "第1章 发酵工程", 6, "offset", False),
        (1, "第1节 获得纯种微生物", 7, "body", False),
        (2, "抗生素的生产菌种", 7, None, True),
        (2, "探究· 实验1-1 培养基的配制", 10, "near", False),
    ]
    assert toc["entries"][0]["is_chapter"] and toc["entries"][0]["print_page"] == "1"
    assert all("生物学" != e["title"] for e in toc["entries"])


def test_rows_without_print_page_follow_old_line(tmp_path: Path) -> None:
    book = _book(tmp_path, [
        _entry("1 No limits", 0, None, True),
        _entry("A Reading and interaction", 1, 6),
        _entry("Reading: Blame your brain", 2, None),
        _entry("B Grammar activity", 1, 12),
    ], offset=3)
    _page(book, 8, ["1 No limits"])
    _page(book, 9, ["A Reading and interaction"], 6)
    _page(book, 15, ["B Grammar activity"], 12)
    got = {e["title"]: (e["pdf_page"], e["page_from"]) for e in ir_toc.build_toc(book)["entries"]}
    assert got["1 No limits"] == (8, "search")
    assert got["Reading: Blame your brain"] == (9, "prev")
    assert got["B Grammar activity"] == (15, "body")


def test_heading_without_page_or_title_takes_next_row(tmp_path: Path) -> None:
    book = _book(tmp_path, [_entry("第1部分 案例", 0, None, True), _entry("1 红绿灯管理", 1, 6)])
    _page(book, 11, ["1 红绿灯管理"], 6)
    got = ir_toc.build_toc(book)["entries"]
    assert (got[0]["pdf_page"], got[0]["page_from"]) == (11, "next")


def test_body_heading_repeated_on_later_pages_is_kept_once(tmp_path: Path) -> None:
    book = _book(tmp_path, [_entry("第1节 甲", 1, 1), _entry("第2节 乙", 1, 3)])
    _page(book, 6, ["第1节 甲", "提出问题"], 1)
    _page(book, 7, ["提出问题", "构建模型"], 2)
    _page(book, 8, ["第2节 乙", "提出问题"], 3)
    kids = [(e["title"], e["pdf_page"]) for e in ir_toc.build_toc(book)["entries"] if e.get("from_body")]
    assert kids == [("提出问题", 6), ("构建模型", 7)]


def test_no_catalog_falls_back_to_headings(tmp_path: Path) -> None:
    book = _book(tmp_path, [])
    (book / "toc.print").write_text(json.dumps({"found": False, "entries": []}), encoding="utf-8")
    _page(book, 6, ["第一章 运动"], 1)
    assert ir_toc.build_toc(book)["source"] == "ir_toc_headings"


def test_missing_offset_is_voted_from_matched_titles(tmp_path: Path) -> None:
    book = _book(tmp_path, [_entry("第1节 甲", 1, 1), _entry("第2节 乙", 1, 3), _entry("第3节 丙", 1, 5)], offset=None)
    _page(book, 6, ["第1节 甲"])
    _page(book, 8, ["第2节 乙"])
    toc = ir_toc.build_toc(book)
    assert toc["page_offset"] == 5
    assert [(e["pdf_page"], e["page_from"]) for e in toc["entries"]] == [(6, "body"), (8, "body"), (10, "offset")]


def test_fix_replaces_reviewed_headings_but_keeps_reviewed_skeleton(tmp_path: Path) -> None:
    book = _book(tmp_path, [_entry("第1节 甲", 1, 1)])
    _page(book, 6, ["第1节 甲"], 1)
    saved = {"source": "ir_toc_review", "reviewed": True, "toc_pages": [], "entries": [{"title": "旧"}]}
    (book / "ir" / "toc.json").write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    got = ir_toc.ir_toc("book1", str(tmp_path), "fix")
    assert got["source"] == "ir_toc_print" and not got.get("skipped")
    backup = tmp_path / "logs" / "toc-backup-book1.json"
    assert json.loads(backup.read_text(encoding="utf-8"))["entries"] == [{"title": "旧"}]
    written = json.loads((book / "ir" / "toc.json").read_text(encoding="utf-8"))
    written["reviewed"] = True
    (book / "ir" / "toc.json").write_text(json.dumps(written, ensure_ascii=False), encoding="utf-8")
    assert ir_toc.ir_toc("book1", str(tmp_path), "fix")["skipped"]
