"""Shim. 真正的目录词法实现在 workspace/tools/toc_lexicon.py（agent 可改）。

改词法请改 `workspace/tools/toc_lexicon.py` 与 `workspace/configs/toc_lexicon.yaml`，
不要往这里加逻辑。归属清单见 doc/toc-code-ownership.md。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.workspace_module import load


_REQUIRE = (
    "parse_toc_line",
    "strip_page",
    "match_line",
    "export_for_js",
    "is_column_label",
    "has_skip_prefix",
    "prepare_catalog_lines",
    "repair_jie_heading",
)


def _mod() -> Any:
    return load("toc_lexicon", require=_REQUIRE)


LineHit = _mod().LineHit


def lexicon_path() -> Path:
    return _mod().lexicon_path()


def reset_for_tests() -> None:
    _mod().reset_for_tests()


def lexicon_stamp() -> str:
    return _mod().lexicon_stamp()


def compiled(name: str) -> re.Pattern[str] | None:
    return _mod().compiled(name)


def export_for_js() -> dict[str, Any]:
    return _mod().export_for_js()


def column_labels(book_id: str = "") -> set[str]:
    return _mod().column_labels(book_id)


def skip_prefixes() -> tuple[str, ...]:
    return _mod().skip_prefixes()


def has_skip_prefix(text: str) -> bool:
    return _mod().has_skip_prefix(text)


def strip_page(text: str) -> tuple[str, int | None]:
    return _mod().strip_page(text)


def match_line(text: str) -> Any:
    return _mod().match_line(text)


def match_body(text: str) -> Any:
    return _mod().match_body(text)


def parse_toc_line(text: str) -> dict[str, Any]:
    return _mod().parse_toc_line(text)


def is_column_label(text: str, book_id: str = "") -> bool:
    return _mod().is_column_label(text, book_id)


def normalize_catalog_line(text: str) -> str:
    return _mod().normalize_catalog_line(text)


def prepare_catalog_lines(lines: list[str]) -> list[str]:
    return _mod().prepare_catalog_lines(lines)


def repair_jie_heading(text: str) -> str:
    return _mod().repair_jie_heading(text)
