from __future__ import annotations

import fitz

from app.kb.figure_recrop import recrop_figures
from app.kb.workspace_figures import figure_recrop, reset_for_tests


def _box_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.draw_rect(fitz.Rect(20, 20, 80, 80), color=(1, 0, 0), fill=(1, 0, 0))
    data = doc.tobytes()
    doc.close()
    return data


def test_workspace_algo_is_loadable() -> None:
    reset_for_tests()
    algo = figure_recrop()
    assert algo is not None
    assert algo.FIGURE_CROP_DPI == 300
    assert algo.page_jpeg_settings() == (150, 92)


def test_recrop_uploads_sharper_jpeg(monkeypatch) -> None:
    uploaded: list[tuple[str, bytes, str]] = []

    def _put(key: str, data: bytes, *, content_type: str) -> str:
        uploaded.append((key, data, content_type))
        return f"/asset?key={key}"

    monkeypatch.setattr("app.kb.figure_recrop.upload_bytes", _put)
    blocks = [
        {
            "type": "image",
            "page_idx": 0,
            "bbox": [100, 100, 400, 400],
            "content": {"image_source": {"path": "images/red.jpg"}},
        }
    ]
    n = recrop_figures("book1", _box_pdf(), blocks, dpi=150, quality=95)
    assert n == 1
    key, data, ctype = uploaded[0]
    assert key.endswith("/red.jpg")
    assert ctype == "image/jpeg"
    assert data[:2] == b"\xff\xd8"
    assert len(data) > 80


def test_recrop_skips_bad_pdf() -> None:
    assert recrop_figures("book1", b"%PDF-not-a-real-file", []) == 0


def test_recrop_uploads_diagram_equation(monkeypatch) -> None:
    uploaded: list[tuple[str, bytes, str]] = []

    def _put(key: str, data: bytes, *, content_type: str) -> str:
        uploaded.append((key, data, content_type))
        return f"/asset?key={key}"

    monkeypatch.setattr("app.kb.figure_recrop.upload_bytes", _put)
    reset_for_tests()
    blocks = [
        {
            "type": "equation_interline",
            "page_idx": 0,
            "bbox": [100, 100, 400, 400],
            "text": r"\begin{array}{c} A \xlongequal{\text{点燃}} B \end{array}",
        }
    ]
    n = recrop_figures("book1", _box_pdf(), blocks, dpi=150, quality=95)
    assert n == 1
    key, data, ctype = uploaded[0]
    assert key.endswith("/eq-p0001-100-100-400-400.jpg")
    assert ctype == "image/jpeg"
    assert data[:2] == b"\xff\xd8"
