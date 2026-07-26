"""工具层（v2：插件注册表）。

- 注册表与上下文：registry.py（ToolSpec / ToolContext / build_specs）
- 插件：plugins/（editions / corpus / harness / system）
- 装配与中间件（gate / step / 预算 / 占位符）：app/mcp_server.py
- 文件守护：fs_guard.py（白名单来自 agent-env/permissions.yaml）
"""

from __future__ import annotations

from .registry import ToolContext, ToolSpec, build_specs  # noqa: F401
