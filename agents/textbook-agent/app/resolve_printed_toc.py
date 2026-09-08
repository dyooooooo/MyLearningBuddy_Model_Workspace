"""Shim. 印刷目录看图核对在 workspace/tools/resolve_printed_toc.py（agent 可改）。"""

from __future__ import annotations

import sys

from app.workspace_module import load

sys.modules[__name__] = load(
    "resolve_printed_toc",
    require=("run_book", "resolve_printed_toc", "assemble_read_user", "assemble_reconcile_user"),
)
