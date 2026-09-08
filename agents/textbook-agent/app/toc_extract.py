"""Shim. 真正的小节抽取实现在 workspace/tools/toc_extract.py（agent 可改）。

本文件把自己在 `sys.modules` 里替换成 workspace 模块，所以
`from app import toc_extract` / `from app.toc_extract import run_book` 拿到的是同一个
对象，测试里的 monkeypatch 也直接作用在真实实现上。归属清单见 doc/toc-code-ownership.md。
"""

from __future__ import annotations

import sys

from app.workspace_module import load

sys.modules[__name__] = load(
    "toc_extract",
    require=("run_book", "sibling_intervals", "scan_interval_entries", "write_agent_sidecar"),
)
