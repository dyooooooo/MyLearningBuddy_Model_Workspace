from __future__ import annotations

import re

from app import surfaces, toc_lexicon
from app.config import settings
from app.workspace import ensure_workspace

SAMPLES = [
    ("第1章 集合 2", {"label": "第1章 集合", "print_page": 2, "level": 0, "is_chapter": True}),
    ("1.2 常用逻辑用语 15", {"label": "1.2 常用逻辑用语", "print_page": 15, "level": 1, "is_chapter": False}),
    (
        "第三章·相互作用与力的平衡 / 46",
        {"label": "第三章·相互作用与力的平衡", "print_page": 46, "level": 0, "is_chapter": True},
    ),
    (
        "第一节 生活中常见的力 /47",
        {"label": "第一节 生活中常见的力", "print_page": 47, "level": 1, "is_chapter": False},
    ),
    (
        "A Reading and interaction 12",
        {"label": "A Reading and interaction", "print_page": 12, "level": 1, "is_chapter": False},
    ),
    ("第一单元", {"label": "第一单元", "print_page": None, "level": 0, "is_chapter": True}),
    ("1", {"label": "1", "print_page": None, "level": 1, "is_chapter": False}),
]

_ROW = re.compile(
    r'kb-page-toc-row--lv(\d+)[^"]*"[^>]*>'
    r'.*?<span class="kb-page-toc-title">([^<]*)</span>'
    r'(?:.*?<span class="kb-page-toc-page">(\d+)</span>)?',
    re.S,
)


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    settings.agent_home = str(home)
    toc_lexicon.reset_for_tests()
    surfaces.reset_for_tests()
    ensure_workspace()
    return home


def test_python_lexicon_samples(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    for raw, expect in SAMPLES:
        got = toc_lexicon.parse_toc_line(raw)
        assert got["label"] == expect["label"], raw
        assert got["print_page"] == expect["print_page"], raw
        assert got["level"] == expect["level"], raw
        assert got["is_chapter"] is expect["is_chapter"], raw


def test_js_prepare_matches_python(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    folder = surfaces.slot_dir("page-body")
    md = "目录\n\n" + "\n".join(raw for raw, _expect in SAMPLES) + "\n"
    html = surfaces._run_js(
        folder,
        "prepare",
        markdown=md,
        ctx={"profile": {}, "lexicon": toc_lexicon.export_for_js(), "printToPdf": {}},
    )
    rows = []
    for match in _ROW.finditer(str(html or "")):
        page = int(match.group(3)) if match.group(3) else None
        rows.append(
            {
                "label": match.group(2),
                "print_page": page,
                "level": int(match.group(1)),
                "is_chapter": "kb-page-toc-row--chapter" in match.group(0),
            }
        )
    assert len(rows) == len(SAMPLES)
    for (_raw, expect), got in zip(SAMPLES, rows):
        assert got["label"] == expect["label"]
        assert got["print_page"] == expect["print_page"]
        assert got["level"] == expect["level"]
        assert got["is_chapter"] is expect["is_chapter"]
        py = toc_lexicon.parse_toc_line(_raw)
        assert py["label"] == got["label"]
        assert py["print_page"] == got["print_page"]
        assert py["level"] == got["level"]
        assert py["is_chapter"] is got["is_chapter"]


def test_prepare_catalog_repairs_shangke_biology_layout(tmp_path, monkeypatch) -> None:
    """沪科/上科装饰章号 + 粘行：版式行与 MinerU 粘行都能拆开。"""
    _home(tmp_path, monkeypatch)
    layout = toc_lexicon.prepare_catalog_lines(
        [
            "目录",
            "第 1 章 走 进生物学 1",
            "第 1 节生物学是与人类生活密切相关的自然科学 2",
            "第 2 节实验探究是学习生物学的重要途径 8",
            "探究 · 实验 1-1 用高倍镜观察动植物细胞 12",
            "第1 节 C、H、O、N、P、S 等元素组成复杂的",
            "生物分子 24",
            "第 2 节细胞各部分结构既 分工又 合作55",
            "第 2 节细胞通过分化形成多细胞生物体 11 4",
            "第 3 节细胞衰老和死亡是自然的生理过程11 9",
            "第 章 1",
            "走进生物学 1",
        ]
    )
    labels = [toc_lexicon.strip_page(line)[0] for line in layout]
    pages = [toc_lexicon.strip_page(line)[1] for line in layout]
    assert "第1章 走进生物学" in labels or any(item.startswith("第1章") for item in labels)
    assert any("生物学是与人类生活密切相关的自然科学" in item for item in labels)
    assert any("C、H、O、N、P、S" in item and "生物分子" in item for item in labels)
    assert any(item.endswith("合作") or "分工又合作" in item for item in labels)
    assert 114 in pages
    assert 119 in pages

    glued = toc_lexicon.prepare_catalog_lines(
        [
            "第 1 节 细胞由质膜包裹 50第 2 节 细胞各部分结构既分工又合作 55"
            "探究 · 实验 3-1 观察叶绿体和细胞质流动 59"
        ]
    )
    assert len(glued) >= 3
    assert toc_lexicon.strip_page(glued[0])[1] == 50
    assert toc_lexicon.strip_page(glued[1])[1] == 55


def test_repair_jie_heading_decorative_number(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    assert (
        toc_lexicon.repair_jie_heading("第 节 2 实验探究是学习生物学的重要途径")
        == "第2节 实验探究是学习生物学的重要途径"
    )
