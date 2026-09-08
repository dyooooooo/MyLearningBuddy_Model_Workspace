"""Shim. 真正的插图尺寸/同行实现在 workspace/tools/fig_size.py（agent 可改）。

改算法请改 `workspace/tools/fig_size.py` 与
`workspace/surfaces/page-body/lib/fig-size.mjs`，不要往这里加逻辑。
归属清单见 doc/fig-code-ownership.md。
"""

from __future__ import annotations

from typing import Any

from app.workspace_module import load

_REQUIRE = (
    "is_enabled",
    "book_fig_widths",
    "book_fig_rows",
    "page_fig_widths",
    "page_fig_rows",
    "reset_for_tests",
)


def _mod() -> Any:
    return load("fig_size", require=_REQUIRE)


def is_enabled(book_id: str) -> bool:
    return _mod().is_enabled(book_id)


def book_fig_widths(book_id: str) -> dict[int, dict[str, float]]:
    return _mod().book_fig_widths(book_id)


def book_fig_rows(book_id: str) -> dict[int, list[list[str]]]:
    return _mod().book_fig_rows(book_id)


def page_fig_widths(book_id: str, page_no: int) -> dict[str, float]:
    return _mod().page_fig_widths(book_id, page_no)


def page_fig_rows(book_id: str, page_no: int) -> list[list[str]]:
    return _mod().page_fig_rows(book_id, page_no)


def reset_for_tests() -> None:
    _mod().reset_for_tests()
