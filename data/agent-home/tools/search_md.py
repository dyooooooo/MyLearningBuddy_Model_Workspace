from __future__ import annotations

from pathlib import Path


def search_md(root: str, query: str, limit: int = 20) -> list[dict[str, str]]:
    """Keyword search over Markdown files. Light glue; no vector index."""
    needle = (query or "").strip()
    if not needle:
        return []
    try:
        cap = int(limit)
    except (TypeError, ValueError):
        cap = 20
    if cap <= 0:
        cap = 20
    hits: list[dict[str, str]] = []
    base = Path(root)
    if not base.is_dir():
        return []
    for path in sorted(base.rglob("*.md")):
        if path.name == "SKILL.md":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if needle not in text and needle.lower() not in path.as_posix().lower():
            continue
        idx = text.find(needle)
        snippet = text[max(0, idx) : max(0, idx) + 160] if idx >= 0 else path.stem
        hits.append({"path": str(path), "snippet": snippet})
        if len(hits) >= cap:
            break
    return hits
