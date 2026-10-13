from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "workspace" / "tools"
sys.path.insert(0, str(TOOLS))

import ir_print_toc as ipt  # noqa: E402


def _entry(label: str, level: int, page: int | None, pdf: int, chapter: bool = False) -> dict:
    return {"label": label, "level": level, "print_page": page, "pdf_page": pdf, "is_chapter": chapter}


def _doc(entries: list[dict], offset: int | None = 5) -> dict:
    return {"found": True, "arch": "v2", "page_offset": offset, "entries": entries}


def test_rows_group_by_catalog_page_and_jump_by_offset() -> None:
    doc = _doc([
        _entry("第1章 发酵工程", 0, 1, 4, True),
        _entry("探究· 实验1-1", 2, 6, 4),
        _entry("第3章 基因工程", 0, 65, 5, True),
    ])
    got = ipt.compile_print_toc(doc, {4: "目录"}, "abc")
    assert sorted(got["pages"]) == ["4", "5"]
    first = got["pages"]["4"]
    assert first["heading"] == "目录" and got["pages"]["5"]["heading"] == ""
    assert first["rows"][0] == {
        "label": "第1章 发酵工程", "level": 0, "chapter": True,
        "print_page": 1, "goto_page": 6, "needs_review": False,
    }
    assert first["rows"][1]["goto_page"] == 11


def test_no_page_or_no_offset_means_no_jump() -> None:
    row = ipt.compile_row(_entry("Unit 1", 0, None, 5), 3)
    assert row["print_page"] is None and row["goto_page"] is None
    assert ipt.compile_row(_entry("Unit 1", 0, 2, 5), None)["goto_page"] is None
    assert ipt.compile_row(_entry("深", 9, 2, 5), 0)["level"] == ipt.LEVEL_MAX


def test_nothing_found_compiles_to_none() -> None:
    assert ipt.compile_print_toc({"found": False, "entries": []}, {}) is None
    assert ipt.compile_print_toc(_doc([_entry("", 0, 1, 4)]), {}) is None


def test_heading_ignores_spaces_and_case() -> None:
    assert ipt.page_heading({"blocks": [{"text": "第 章 1"}, {"text": "目 录"}]}) == "目录"
    assert ipt.page_heading({"blocks": [{"text": "CONTENTS"}]}) == "CONTENTS"
    assert ipt.page_heading({"blocks": [{"text": "目录与说明"}]}) == ""


def test_tool_writes_and_removes(tmp_path: Path) -> None:
    book = tmp_path / "textbooks" / "book1"
    (book / "ir" / "pages").mkdir(parents=True)
    (book / "ir" / "pages" / "0004.json").write_text(json.dumps({"blocks": [{"text": "目录"}]}), encoding="utf-8")
    (book / "toc.print").write_text(json.dumps(_doc([_entry("第1章", 0, 1, 4, True)])), encoding="utf-8")
    got = ipt.ir_print_toc("book1", str(tmp_path))
    assert got["written"] and got["pages"] == [4] and got["rows"] == 1
    saved = json.loads((book / "ir" / "print_toc.json").read_text(encoding="utf-8"))
    assert saved["pages"]["4"]["heading"] == "目录" and saved["source_sha"]
    (book / "toc.print").write_text(json.dumps({"found": False, "entries": []}), encoding="utf-8")
    again = ipt.ir_print_toc("book1", str(tmp_path))
    assert again["removed"] and not (book / "ir" / "print_toc.json").exists()
