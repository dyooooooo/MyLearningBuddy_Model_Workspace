from __future__ import annotations

import re

_PAGE_CHUNK_ID = re.compile(r":p(\d+)$")
_PAGE_LABEL = re.compile(r"第\s*(\d+)\s*页")


def page_sort_key(
    *,
    node_id: str = "",
    label: str = "",
    source_page: int | None = None,
) -> tuple[int, int, str]:
    """Numeric page order for import chunks (1, 2, … 10), not lexicographic labels."""
    if source_page is not None:
        try:
            page = int(source_page)
            if page > 0:
                return (0, page, "")
        except (TypeError, ValueError):
            pass
    m = _PAGE_CHUNK_ID.search(node_id)
    if m:
        return (0, int(m.group(1)), "")
    m = _PAGE_LABEL.search(label or "")
    if m:
        return (0, int(m.group(1)), "")
    return (1, 0, label or "")
