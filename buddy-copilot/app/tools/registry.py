"""工具插件注册表（OCP 核心）。

每个工具是一个独立声明：canonical 名（点号）、展示文案、带类型签名的 handler。
- 档位（tier）/ 闸门 / 场景可见性由 agent-env/permissions.yaml 驱动，不写在这里；
- mcp_server 做通用装配：遍历注册表挂载、按 tier 套 gate 中间件、统一 step 落库。

新增工具 = plugins/ 下加一个 build 函数条目 + permissions.yaml 加一行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import Settings
from ..store import Store
from .buddy_api import BuddyApiClient


@dataclass
class ToolContext:
    """插件运行上下文（依赖注入，插件不自行构造全局对象）。"""

    settings: Settings
    store: Store
    client: BuddyApiClient
    session_id: str = ""
    kind: str = ""

    @property
    def workspace(self) -> Path:
        return self.settings.workspace_root

    def session_payload(self) -> dict[str, Any]:
        if not self.session_id:
            return {}
        s = self.store.get_session(self.session_id)
        return (s or {}).get("payload") or {}


@dataclass
class ToolSpec:
    """一个工具的完整声明。fn 须带类型注解签名（MCP schema 由此生成）并返回可 JSON 化对象。"""

    name: str                       # canonical 点号名，如 "corpus.start_import"
    title: str                      # 展示文案（step 行 / 批准卡标题）
    fn: Callable[..., Any]          # handler（闭包持有 ToolContext）
    description: str = ""           # MCP 工具描述（缺省取 fn.__doc__）
    # 闸门前的只读自检：入参 → 附加参数键，并进批准卡的「参数」块。
    # 给「批的东西人眼看不出对不对」的闸门用（例如词插件的正反例结果），
    # 让人不必盲签。只在 tier=gated 时调用，抛异常不阻断闸门。
    preflight: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    @property
    def mcp_name(self) -> str:
        return self.name.replace(".", "_")

    def resolved_description(self) -> str:
        return self.description or (self.fn.__doc__ or self.title).strip()


# 插件 build 函数签名：ToolContext -> list[ToolSpec]
Builder = Callable[[ToolContext], list[ToolSpec]]

_BUILDERS: list[Builder] = []


def register(builder: Builder) -> Builder:
    _BUILDERS.append(builder)
    return builder


def build_specs(ctx: ToolContext) -> dict[str, ToolSpec]:
    """装配全部插件工具；返回 canonical 名 → ToolSpec。"""
    # 触发插件包导入（各模块 import 时自行 register）
    from . import plugins  # noqa: F401

    specs: dict[str, ToolSpec] = {}
    for builder in _BUILDERS:
        for spec in builder(ctx):
            if spec.name in specs:
                raise ValueError(f"工具重名: {spec.name}")
            specs[spec.name] = spec
    return specs
