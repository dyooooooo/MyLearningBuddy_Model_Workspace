from __future__ import annotations

from pathlib import Path


def open_pdf_page(path: str, page: int = 1, root: str = "") -> dict[str, str | int | bool]:
    """Resolve a textbook PDF path so the agent can open that page."""
    raw = Path(path or "")
    base = Path(root) if root else Path.cwd()
    target = raw if raw.is_absolute() else (base / raw)
    if not target.is_file() and raw.name:
        matches = list(base.rglob(f"*{raw.name}*"))
        pdfs = [p for p in matches if p.suffix.lower() == ".pdf"]
        if pdfs:
            target = pdfs[0]
    return {
        "path": str(target),
        "exists": target.is_file(),
        "page": int(page or 1),
        "note": "用工作区文件工具打开该 PDF 的这一页，不要另建索引。",
    }
