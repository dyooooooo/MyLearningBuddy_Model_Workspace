from __future__ import annotations

import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def convert_pdf_to_markdown(pdf_bytes: bytes, *, filename: str = "import.pdf") -> tuple[str, dict]:
    """PDF bytes → Markdown via MarkItDown. Returns (markdown, meta)."""
    try:
        from markitdown import MarkItDown
    except ImportError as e:
        raise RuntimeError("markitdown not installed; pip install 'markitdown[pdf]'") from e

    suffix = Path(filename).suffix or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        md = MarkItDown()
        result = md.convert(tmp_path)
        text = getattr(result, "text_content", None) or getattr(result, "markdown", "") or ""
        meta = {
            "converter": "markitdown",
            "char_count": len(text),
            "filename": filename,
        }
        return text, meta
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def convert_file_path_to_markdown(path: str) -> tuple[str, dict]:
    with open(path, "rb") as f:
        return convert_pdf_to_markdown(f.read(), filename=Path(path).name)
