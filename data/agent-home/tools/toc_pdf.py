"""Layout-preserving text from a PDF catalog page.

Prefer poppler ``pdftotext -layout`` when the binary exists; otherwise use
pypdf ``extraction_mode='layout'``. Both are mature extractors — do not add
a hand-rolled PDF parser here.

Library only (not in manifest.yaml).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def page_layout_candidates(pdf_path: Path | str, page_no: int) -> list[str]:
    """Return one or two layout texts for the 1-based page (empty list if none)."""
    path = Path(pdf_path)
    try:
        index = int(page_no)
    except (TypeError, ValueError):
        return []
    if not path.is_file() or index < 1:
        return []
    out: list[str] = []
    for blob in (_pypdf_layout(path, index), _pdftotext_layout(path, index)):
        text = (blob or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def page_layout_text(pdf_path: Path | str, page_no: int) -> str:
    found = page_layout_candidates(pdf_path, page_no)
    return found[0] if found else ""


def _pdftotext_layout(path: Path, page_no: int) -> str:
    binary = shutil.which("pdftotext")
    if not binary:
        return ""
    try:
        proc = subprocess.run(
            [
                binary,
                "-layout",
                "-f",
                str(page_no),
                "-l",
                str(page_no),
                str(path),
                "-",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.debug("pdftotext failed path=%s page=%s", path, page_no, exc_info=True)
        return ""
    return proc.stdout or ""


def _pypdf_layout(path: Path, page_no: int) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        index = page_no - 1
        if index < 0 or index >= len(reader.pages):
            return ""
        return (
            reader.pages[index].extract_text(
                extraction_mode="layout",
                layout_mode_space_vertically=False,
            )
            or ""
        )
    except Exception:
        logger.debug("pypdf layout failed path=%s page=%s", path, page_no, exc_info=True)
        return ""
