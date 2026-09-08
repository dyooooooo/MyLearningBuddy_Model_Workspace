from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_TOOL = Path(__file__).resolve().parents[1] / "workspace" / "tools" / "fill_unit_toc.py"
_spec = importlib.util.spec_from_file_location("fill_unit_toc", _TOOL)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_mod)
fill_unit_toc = _mod.fill_unit_toc

_SKIN = (
    "目录\n"
    '<ol class="kb-page-toc kb-page-toc-list">'
    '<li class="kb-page-toc-row kb-page-toc-row--lv0">'
    '<span class="kb-page-toc-title">第一单元</span></li>'
    '<li class="kb-page-toc-row kb-page-toc-row--lv1">'
    '<span class="kb-page-toc-title">1</span></li>'
    '<li class="kb-page-toc-row kb-page-toc-row--lv2">'
    '<span class="kb-page-toc-title">沁园春·长沙 / 毛泽东</span>'
    '<span class="kb-page-toc-page">9</span></li>'
    "</ol>\n"
)

_TOC = {
    "entries": [
        {
            "label": "第一单元",
            "print_page": None,
            "is_chapter": True,
            "level": 0,
            "pdf_page": 8,
        },
        {
            "label": "1",
            "print_page": 9,
            "is_chapter": False,
            "level": 1,
            "pdf_page": 9,
        },
        {
            "label": "沁园春·长沙 / 毛泽东",
            "print_page": 9,
            "is_chapter": False,
            "level": 2,
            "pdf_page": 9,
        },
    ],
    "print_to_pdf": {"9": 9},
    "toc_pages": [4],
    "source": "printed",
}


def test_fill_unit_toc_rewrites_layers_and_keeps_pages(tmp_path: Path) -> None:
    book = tmp_path / "textbooks" / "demo"
    pages = book / "pages"
    mid = book / "mid"
    pages.mkdir(parents=True)
    mid.mkdir(parents=True)
    (book / "toc.json").write_text(json.dumps(_TOC, ensure_ascii=False), encoding="utf-8")
    (pages / "0004.md").write_text(_SKIN, encoding="utf-8")
    (mid / "0004.md").write_text(_SKIN, encoding="utf-8")
    (pages / "0009.md").write_text("沁园春·长沙\n", encoding="utf-8")

    before_source = (pages / "0004.md").read_text(encoding="utf-8")
    before_mid = (mid / "0004.md").read_text(encoding="utf-8")
    out = fill_unit_toc("demo", root=str(tmp_path))
    assert out["skipped"] is False
    assert out["reused_toc_json"] is True
    assert out["wrote"] == "toc.json"
    assert out["toc_pages_dirty"] == [4]
    assert out["entries"] == 3

    source = (pages / "0004.md").read_text(encoding="utf-8")
    tagged = (mid / "0004.md").read_text(encoding="utf-8")
    toc = json.loads((book / "toc.json").read_text(encoding="utf-8"))
    assert source == before_source
    assert tagged == before_mid
    assert toc["entries"][2]["print_page"] == 9
    assert toc["entries"][2]["pdf_page"] == 9
