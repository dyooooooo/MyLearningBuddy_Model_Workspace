from __future__ import annotations

from pathlib import Path

FORBIDDEN = (
    "corpus_",
    "import_assets",
    "mineru_adapter",
    "pdf_convert",
    "import_35b",
    "app.services.ir",
    "app.services.parsing",
    "convert_pdf_bytes",
    "PdfRenderCache",
)


def test_kb_package_has_no_studio_imports():
    root = Path(__file__).resolve().parents[2] / "app" / "kb"
    hits: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN:
            if needle in text:
                hits.append(f"{path.name}:{needle}")
    assert hits == []
