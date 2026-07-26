#!/usr/bin/env bash
# Agent 环境工程一致性守护：资源地图路径、必选文件、MCP 工具清单
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3

echo "[check-agent-env] root=$ROOT"

"$PY" - <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

from app.agent_env import verify_resource_paths
from app.config import get_settings
from app.mcp_server import mcp

s = get_settings()
env = s.agent_env_dir
required = [
    "resources.yaml",
    "pinning.yaml",
    "permissions.yaml",
    "rules/00-copilot-core.md",
    "rules/10-isolation.md",
    "rules/20-host-docker-sync.md",
    "rules/30-mineru-lessons.md",
    "skills/edition-tuning.md",
    "skills/page-diagnosis.md",
    "skills/verify-loop.md",
    "context/editions.md",
    "context/import-pipeline.md",
    "context/reprocess.md",
    "context/resource-map.md",
]
missing = [r for r in required if not (env / r).is_file()]
if missing:
    print("MISSING:", missing, file=sys.stderr)
    sys.exit(1)

stale = verify_resource_paths(env, s.workspace_root)
if stale:
    print("STALE PATHS:", stale, file=sys.stderr)
    sys.exit(1)

# 三方核对：permissions.yaml（权威）↔ 插件注册表 ↔ pinning.yaml 工具清单
import yaml

from app.permissions import load_permissions
from app.store import Store
from app.tools.buddy_api import BuddyApiClient
from app.tools.registry import ToolContext, build_specs

perms = load_permissions(env)
ctx = ToolContext(s, Store(Path("/tmp/copilot-check.db")), BuddyApiClient(s))
specs = build_specs(ctx)
if set(perms.tools) != set(specs):
    print("permissions.yaml 与插件注册表不一致", file=sys.stderr)
    print("  perms-only:", sorted(set(perms.tools) - set(specs)), file=sys.stderr)
    print("  specs-only:", sorted(set(specs) - set(perms.tools)), file=sys.stderr)
    sys.exit(1)

pin = yaml.safe_load((env / "pinning.yaml").read_text(encoding="utf-8"))
pinned = set((pin.get("mcp") or {}).get("tools") or [])
if pinned != set(perms.tools):
    print("pinning.yaml 工具清单与 permissions.yaml 不一致", file=sys.stderr)
    print("  pin-only:", sorted(pinned - set(perms.tools)), file=sys.stderr)
    print("  perms-only:", sorted(set(perms.tools) - pinned), file=sys.stderr)
    sys.exit(1)

# 无场景过滤时（kind 为空），MCP 挂载数 = 全集
tools = sorted(mcp._tool_manager._tools.keys())  # noqa: SLF001 — 守护用
want = {name.replace(".", "_") for name in perms.tools}
if set(tools) != want:
    print("MCP tools mismatch", file=sys.stderr)
    print("  got:", tools, file=sys.stderr)
    print("  want:", sorted(want), file=sys.stderr)
    sys.exit(1)

print(f"OK model={s.cursor_model} mcp_tools={len(tools)} tiers_ok stale=0")
PY
