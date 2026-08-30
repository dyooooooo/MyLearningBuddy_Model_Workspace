from __future__ import annotations

from app.kb.identify_meta import guess_import_meta


def test_guess_high_school_math_cover() -> None:
    md = "普通高中教科书\n数学\n必修 第一册\n上海教育出版社\n"
    out = guess_import_meta(md, fallback_label="book.pdf")
    assert out["subject"] == "math"
    assert out["grade_band"] == "hs"
    assert "教科书" in out["label"]
    assert "数学" in out["label"] or "数学" in out["textbook_hint"]


def test_guess_junior_math_from_grade() -> None:
    md = "义务教育教科书 数学 七年级 上册"
    out = guess_import_meta(md)
    assert out["subject"] == "math"
    assert out["grade_band"] == "ms"


def test_guess_primary_chinese() -> None:
    md = "义务教育教科书\n语文\n二年级 上册"
    out = guess_import_meta(md)
    assert out["subject"] == "chinese"
    assert out["grade_band"] == "ps"


def test_guess_physics_from_title_block() -> None:
    blocks = [
        {"type": "title", "text": "普通高中教科书"},
        {"type": "title", "text": "物理 必修第一册"},
    ]
    out = guess_import_meta("", blocks)
    assert out["subject"] == "physics"
    assert out["grade_band"] == "hs"
    assert "物理" in out["label"] or "物理" in out["textbook_hint"]


def test_guess_falls_back_to_filename() -> None:
    out = guess_import_meta("", fallback_label="随便一本.pdf", fallback_hint="随便一本")
    assert out["label"] == "随便一本"
    assert out["subject"] == "math"
    assert out["grade_band"] == "hs"


def test_guess_strips_english_ocr_noise() -> None:
    md = "普通高中教科书英语·必修English必修第一册\nENGLISH COMPULSORY 1\n"
    out = guess_import_meta(md)
    assert out["subject"] == "english"
    assert out["grade_band"] == "hs"
    assert out["label"] == "普通高中教科书·英语必修第一册"
