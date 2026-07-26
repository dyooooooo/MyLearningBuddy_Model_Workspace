"""工具插件包：import 即注册（registry.register 装饰）。

新增插件模块后在此 import 一行即可。
"""

from __future__ import annotations

from . import (  # noqa: F401
    corpus_tools,
    editions_tools,
    harness_tools,
    layout_tools,
    preview_tools,
    session_tools,
    system_tools,
)
