"""权限清单加载（agent-env/permissions.yaml）。

权限即数据：分档、闸门、场景可见性、文件白名单、预算全部由 yaml 驱动，
引擎核心不出现任何具体工具名 / 路径的硬编码分支。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ToolPerm:
    name: str
    tier: str
    risk: str = "low"
    gate_note: str = ""

    @property
    def gated(self) -> bool:
        return self.tier == "gated"


@dataclass(frozen=True)
class Budgets:
    run_timeout_s: int = 900
    idle_timeout_s: int = 300  # 无输出空转看门狗（工具执行中 / 等审批不计）
    gate_timeout_s: int = 1800
    max_tool_calls: int = 120
    max_nudges: int = 2
    # 改动面上限（爆炸半径）：一轮改的文件数 / 行数超限即视为越权并回滚
    max_changed_files: int = 12
    max_changed_lines: int = 800


@dataclass(frozen=True)
class Permissions:
    tiers: dict[str, dict] = field(default_factory=dict)
    tools: dict[str, ToolPerm] = field(default_factory=dict)
    scenario_tools: dict[str, list[str]] = field(default_factory=dict)
    budgets: Budgets = field(default_factory=Budgets)
    write_whitelist: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    guarded_roots: tuple[str, ...] = ()
    backup_suffixes: tuple[str, ...] = ()
    auto_allow_suffixes: tuple[str, ...] = ()
    # 白名单内但要走独立批准的子路径（会被执行的代码，不是声明文件）
    gated_subpaths: tuple[str, ...] = ()

    def tool(self, name: str) -> ToolPerm:
        p = self.tools.get(name)
        if p is None:
            raise KeyError(f"工具 {name} 未在 permissions.yaml 声明")
        return p

    def tier_auto(self, tier: str) -> bool:
        return bool((self.tiers.get(tier) or {}).get("auto", False))

    def visible_tools(self, kind: str) -> list[str]:
        """场景级工具可见性；未声明的场景看到全部工具。"""
        return self.scenario_tools.get(kind) or sorted(self.tools)


def load_permissions(agent_env_dir: Path) -> Permissions:
    f = agent_env_dir / "permissions.yaml"
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    tools = {
        name: ToolPerm(
            name=name,
            tier=str((cfg or {}).get("tier", "gated")),
            risk=str((cfg or {}).get("risk", "low")),
            gate_note=str((cfg or {}).get("gate_note", "")),
        )
        for name, cfg in (data.get("tools") or {}).items()
    }
    b = data.get("budgets") or {}
    files = data.get("files") or {}
    scenarios = {
        k: list((v or {}).get("tools") or []) for k, v in (data.get("scenarios") or {}).items()
    }
    perms = Permissions(
        tiers=data.get("tiers") or {},
        tools=tools,
        scenario_tools=scenarios,
        budgets=Budgets(
            run_timeout_s=int(b.get("run_timeout_s", 900)),
            idle_timeout_s=int(b.get("idle_timeout_s", 300)),
            gate_timeout_s=int(b.get("gate_timeout_s", 1800)),
            max_tool_calls=int(b.get("max_tool_calls", 120)),
            max_nudges=int(b.get("max_nudges", 2)),
            max_changed_files=int(b.get("max_changed_files", 12)),
            max_changed_lines=int(b.get("max_changed_lines", 800)),
        ),
        write_whitelist=tuple(files.get("write_whitelist") or ()),
        forbidden=tuple(files.get("forbidden") or ()),
        guarded_roots=tuple(files.get("guarded_roots") or ()),
        backup_suffixes=tuple(files.get("backup_suffixes") or ()),
        auto_allow_suffixes=tuple(files.get("auto_allow_suffixes") or ()),
        gated_subpaths=tuple(files.get("gated_subpaths") or ()),
    )
    # 一致性校验：场景引用的工具必须已声明
    for kind, names in perms.scenario_tools.items():
        unknown = [n for n in names if n not in perms.tools]
        if unknown:
            raise ValueError(f"permissions.yaml 场景 {kind} 引用未声明工具: {unknown}")
    return perms


@lru_cache(maxsize=4)
def get_permissions(agent_env_dir: Path) -> Permissions:
    return load_permissions(agent_env_dir)
