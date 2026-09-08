"""Wash a whole imported book in one call. Do not glob; do not chat per page."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

def _agent_home(root: str) -> Path:
    base = Path(root).resolve() if (root or "").strip() else Path.cwd()
    if (base / "textbooks").is_dir():
        return base
    if base.name == "textbooks":
        return base.parent
    if base.parent.name == "textbooks":
        return base.parent.parent
    try:
        from app.config import settings

        if not (root or "").strip():
            return settings.home
    except Exception:
        pass
    return base


def _load_sibling(name: str) -> Any:
    try:
        return __import__(name)
    except ImportError:
        path = Path(__file__).resolve().with_name(f"{name}.py")
        spec = importlib.util.spec_from_file_location(f"_refine_book_{name}", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}") from None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def refine_book(book_id: str, root: str = "") -> dict[str, Any]:
    """Run refine_page on every page, then audit_mid_syntax(fix) and improve_render scan."""
    sid = (book_id or "").strip()
    if not sid:
        raise ValueError("book_id required")
    home = _agent_home(root)
    book = home / "textbooks" / sid
    if not book.is_dir():
        return {"ok": False, "error": "没有找到教材", "book_id": sid}
    pages_dir = book / "pages"
    if not pages_dir.is_dir():
        return {"ok": False, "error": "没有找到教材", "book_id": sid}

    page_nos: list[int] = []
    empty_pages: list[int] = []
    for path in sorted(pages_dir.glob("*.md")):
        if not path.name[:4].isdigit():
            continue
        page_no = int(path.stem)
        page_nos.append(page_no)
        if path.stat().st_size == 0:
            empty_pages.append(page_no)

    textbooks_root = str(home / "textbooks")
    polish = _load_sibling("refine_page").refine_page
    changed = 0
    refined = 0
    for page_no in page_nos:
        if page_no in empty_pages:
            continue
        out = polish(sid, page_no, root=textbooks_root)
        refined += 1
        if out.get("changed"):
            changed += 1

    audit_mod = _load_sibling("audit_mid_syntax")
    render_mod = _load_sibling("improve_render")
    audit = audit_mod.audit_mid_syntax(book_id=sid, apply="fix", root=textbooks_root)
    render = render_mod.improve_render(book_id=sid, apply="", root=textbooks_root)

    return {
        "ok": True,
        "book_id": sid,
        "pages": len(page_nos),
        "refined": refined,
        "changed": changed,
        "empty_pages": empty_pages,
        "has_book_pdf": (book / "book.pdf").is_file(),
        "audit_ok": bool(audit.get("ok")),
        "audit_issues": (audit.get("totals") or {}).get("issue_count"),
        "render_ok": bool(render.get("ok")),
        "render_issues": sum(int(b.get("issue_count") or 0) for b in (render.get("books") or [])),
    }
