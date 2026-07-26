"""Vision OCR figure attachment — page scan + embedded images."""
from __future__ import annotations

from unittest.mock import patch

import fitz

from app.services.pdf_import_figures import append_page_figure_markdown


def test_append_page_figure_markdown_uses_studio_proxy_url():
    fake_jpeg = b"\xff\xd8\xff" + b"\x00" * 100
    fake_pdf = b"%PDF-1.4 minimal"

    uploaded: list[tuple[str, bytes, str]] = []

    def fake_upload(key: str, data: bytes, *, content_type: str) -> str:
        uploaded.append((key, data, content_type))
        return f"/api/v1/studio/proxy/import/asset?key={key}"

    with patch(
        "app.services.pdf_import_figures.upload_import_asset",
        side_effect=fake_upload,
    ), patch.object(fitz, "open", side_effect=RuntimeError("bad pdf")):
        md = append_page_figure_markdown(
            fake_pdf,
            page_no=10,
            page_jpeg=fake_jpeg,
            asset_prefix="corpus/imports/job1/assets",
        )

    assert "### 第 10 页原图" in md
    assert "![第 10 页原图](/api/v1/studio/proxy/import/asset" in md
    assert "/proxy/corpus/import/asset" not in md
    assert any("p0010_scan.jpg" in k for k, _, _ in uploaded)
