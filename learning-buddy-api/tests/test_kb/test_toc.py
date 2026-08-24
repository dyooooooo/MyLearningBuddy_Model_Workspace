from __future__ import annotations

from app.kb.toc import extract_toc, looks_like_toc_blob, parse_entry, refine_toc


def test_parse_entry_trailing_page():
    out = parse_entry("1.1 集合初步 2")
    assert out["label"] == "1.1 集合初步"
    assert out["print_page"] == 2
    assert out["level"] == 1


def test_parse_entry_chapter():
    out = parse_entry("第1章 集合与逻辑")
    assert out["is_chapter"] is True
    assert out["print_page"] is None
    assert out["level"] == 0


def test_looks_like_toc_blob():
    blob = "第1章 集合与逻辑 1\n1.1 集合初步 2\n1.2 命题 8"
    assert looks_like_toc_blob(blob) is True
    assert looks_like_toc_blob("这只是普通正文。") is False


def test_extract_toc_from_index_and_page_numbers():
    blocks = [
        {
            "type": "index",
            "page_idx": 5,
            "content": {
                "list_items": ["第1章 集合与逻辑 1", "1.1 集合初步 2"],
            },
        },
        {"type": "page_number", "page_idx": 8, "text": "2"},
        {"type": "text", "page_idx": 8, "text": "集合初步"},
    ]
    toc = extract_toc(blocks)
    labels = [e["label"] for e in toc["entries"]]
    assert "1.1 集合初步" in labels
    assert toc["print_to_pdf"][2] == 9
    first = next(e for e in toc["entries"] if e["label"] == "1.1 集合初步")
    assert first["pdf_page"] == 9
    assert 6 in toc["toc_pages"]


def test_extract_toc_ignores_exercise_blobs():
    blocks = [
        {"type": "text", "page_idx": 5, "text": "目录"},
        {
            "type": "index",
            "page_idx": 5,
            "content": {"list_items": ["1.1 集合初步 2"]},
        },
        {
            "type": "text",
            "page_idx": 40,
            "text": "解（1）因为2^{3} = 8，即3是方程2^{x} = 8的唯一解，所以由定义得\\log_28 = 3\n(1)x^{3} = 27(2)x^4 = 121",
        },
    ]
    toc = extract_toc(blocks)
    labels = [e["label"] for e in toc["entries"]]
    assert labels == ["1.1 集合初步"]
    assert 41 not in toc["toc_pages"]


def test_extract_toc_does_not_pin_chapter_to_outline_page():
    blocks = [
        {"type": "text", "page_idx": 5, "text": "目录"},
        {
            "type": "index",
            "page_idx": 5,
            "content": {"list_items": ["第1章 集合与逻辑", "1.1 集合初步 2"]},
        },
    ]
    toc = extract_toc(blocks)
    chapter = next(e for e in toc["entries"] if e["is_chapter"])
    assert chapter["pdf_page"] is None


def test_refine_toc_matches_section_number_when_ocr_drifts():
    toc = {
        "entries": [
            {"label": "第3章 幕、指数与对数", "print_page": None, "is_chapter": True, "level": 0},
            {"label": "3.1 幕与指数", "print_page": 60, "is_chapter": False, "level": 1},
        ],
        "print_to_pdf": {},
        "toc_pages": [6, 7],
    }
    pages = {
        6: "目录\n第3章 幕、指数与对数 60",
        67: "第3章 幂、指数与对数",
        68: "3.1 幂与指数\n例1",
    }
    out = refine_toc(toc, pages)
    assert out["entries"][0]["pdf_page"] == 67
    assert out["entries"][1]["pdf_page"] == 68


def test_refine_toc_summary_does_not_skip_later_chapters():
    toc = {
        "entries": [
            {"label": "2.3 基本不等式及其应用", "print_page": 46, "is_chapter": False, "level": 1},
            {"label": "内容提要", "print_page": 55, "is_chapter": False, "level": 1},
            {"label": "第3章 幕、指数与对数", "print_page": None, "is_chapter": True, "level": 0},
            {"label": "3.1 幕与指数", "print_page": 60, "is_chapter": False, "level": 1},
        ],
        "print_to_pdf": {},
        "toc_pages": [6],
    }
    pages = {
        53: "2.3 基本不等式及其应用",
        64: "练习",
        67: "3.1 幂与指数",
        83: "内容提要",
    }
    out = refine_toc(toc, pages)
    assert out["entries"][2]["pdf_page"] == 67
    assert out["entries"][3]["pdf_page"] == 67


def test_refine_toc_maps_headings():
    toc = {
        "entries": [
            {"label": "第1章 集合与逻辑", "print_page": None, "is_chapter": True, "level": 0},
            {"label": "1.1 集合初步", "print_page": 2, "is_chapter": False, "level": 1},
        ],
        "print_to_pdf": {},
        "toc_pages": [6],
    }
    pages = {
        6: "目录\n1.1 集合初步 2",
        8: "集合与逻辑",
        9: "1.1 集合初步\n例1",
    }
    out = refine_toc(toc, pages)
    assert out["entries"][0]["pdf_page"] == 8
    assert out["entries"][1]["pdf_page"] == 9
    assert out["print_to_pdf"][2] == 9
