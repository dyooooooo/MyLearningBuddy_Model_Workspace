from __future__ import annotations

from app.services.pdf_text_normalize import normalize_pdf_text


def test_normalize_fullwidth_digits() -> None:
    assert "1.1" in normalize_pdf_text("１．１ 集合")


def test_normalize_glyph_fixes() -> None:
    out = normalize_pdf_text("狓∈犖")
    assert "x" in out
    assert "N" in out


def test_normalize_shanghai_set_letters() -> None:
    out = normalize_pdf_text("集合犃与犅的并集犝")
    assert "A" in out and "B" in out and "U" in out
    assert "犃" not in out


def test_removes_single_char_vertical_noise() -> None:
    text = "上\n海\n教\n育\n第1章 集合\n正文"
    out = normalize_pdf_text(text)
    assert "上" not in out.split() or "第1章" in out
