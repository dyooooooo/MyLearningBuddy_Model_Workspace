from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Callable


def _load_strip_sub_sup_tags() -> Callable[[str], str]:
    """Load sibling strip_sub_sup.py even if tools/ is not on sys.path."""
    candidates = [
        Path(__file__).resolve().with_name("strip_sub_sup.py"),
        Path("/data/agent/tools/strip_sub_sup.py"),
    ]
    for path in candidates:
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("_refine_page_strip_sub_sup", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.strip_sub_sup_tags
    raise ImportError("cannot load strip_sub_sup.py")


strip_sub_sup_tags = _load_strip_sub_sup_tags()

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_JSON_FENCE = re.compile(r"```(?:json)?\s*\{[\s\S]*?\}\s*```", re.I)
_BAKED_DIV = re.compile(r"</?div\b[^>]*>", re.I)
_MULTI_NL = re.compile(r"\n{3,}")


def refine_page(book_id: str, page_no: int, root: str = "") -> dict[str, object]:
    """Deterministic one-page polish. Writes Markdown atomically."""
    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    n = int(page_no)
    if n < 1:
        raise ValueError("page_no must be >= 1")
    base = Path(root).resolve() if root else Path.cwd()
    if (base / "textbooks").is_dir():
        path = base / "textbooks" / sid / "pages" / f"{n:04d}.md"
    else:
        path = base / sid / "pages" / f"{n:04d}.md"
    if not path.is_file():
        raise FileNotFoundError(str(path))
    original = path.read_text(encoding="utf-8")
    cleaned = _polish(original)
    changed = cleaned != original
    if changed:
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(cleaned, encoding="utf-8")
        tmp.replace(path)
    return {
        "book_id": sid,
        "page_no": n,
        "path": str(path),
        "changed": changed,
        "bytes": len(cleaned.encode("utf-8")),
    }


def _polish(text: str) -> str:
    out = text.replace("\r\n", "\n").replace("\r", "\n")
    out = _JSON_FENCE.sub("", out)
    out = _BAKED_DIV.sub("", out)
    out = strip_sub_sup_tags(out)
    out = "\n".join(line.rstrip() for line in out.split("\n"))
    out = _MULTI_NL.sub("\n\n", out)
    return out.strip() + ("\n" if out.strip() else "")
