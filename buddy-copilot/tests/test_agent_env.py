"""Agent 环境工程：资源地图 / 规则技能 / 工具注册表 / 权限清单 / 钉扎。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent_env import build_context_pack, load_rules, load_skills, verify_resource_paths  # noqa: E402
from app.agent_runner import build_mcp_servers  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.mcp_server import mcp  # noqa: E402
from app.permissions import load_permissions  # noqa: E402


def test_required_agent_env_files():
    env = get_settings().agent_env_dir
    for rel in (
        "resources.yaml",
        "pinning.yaml",
        "permissions.yaml",
        "rules/00-copilot-core.md",
        "skills/edition-tuning.md",
        "skills/page-diagnosis.md",
        "skills/verify-loop.md",
        "context/editions.md",
        "context/import-pipeline.md",
        "context/reprocess.md",
    ):
        assert (env / rel).is_file(), rel


def test_resource_paths_not_stale():
    s = get_settings()
    assert verify_resource_paths(s.agent_env_dir, s.workspace_root) == []


def test_context_pack_injects_rules_skills_map():
    s = get_settings()
    pack = build_context_pack(s.agent_env_dir, "page_assist", "TASK")
    assert "最终报告" in pack  # 自主执行收尾契约
    assert "fs.request_edit" in pack  # 文件修改协议
    assert "资源地图" in pack
    assert "分诊" in pack or "page-diagnosis" in load_skills(s.agent_env_dir, "page_assist")
    assert "TASK" in pack
    assert "双线隔离" in load_rules(s.agent_env_dir)


def test_mcp_tools_match_permissions():
    """无场景过滤时（COPILOT_SESSION_KIND 为空），MCP 挂载 = permissions 全集。"""
    tools = set(mcp._tool_manager._tools.keys())  # noqa: SLF001
    perms = load_permissions(get_settings().agent_env_dir)
    want = {name.replace(".", "_") for name in perms.tools}
    assert tools == want


def test_pinning_tools_match_permissions():
    import yaml

    env = get_settings().agent_env_dir
    pin = yaml.safe_load((env / "pinning.yaml").read_text(encoding="utf-8"))
    pinned = set((pin.get("mcp") or {}).get("tools") or [])
    perms = load_permissions(env)
    assert pinned == set(perms.tools)


def test_mcp_server_config_pinned_with_session_injection():
    s = get_settings()
    servers = build_mcp_servers(s, session_id="sid-1", kind="page_assist")
    assert "buddy-copilot" in servers
    cfg = servers["buddy-copilot"]
    assert "-m" in (cfg.args or [])
    assert "app.mcp_server" in (cfg.args or [])
    env = cfg.env or {}
    assert env.get("COPILOT_SESSION_ID") == "sid-1"
    assert env.get("COPILOT_SESSION_KIND") == "page_assist"
    assert "ADMIN_TOKEN" in env


def test_model_default_auto_and_resolution():
    """默认「自动」（auto = 服务端选）；会话可显式指定 /models 列表中的模型。"""
    from app.agent_runner import resolve_model
    from app.config import Settings

    s = Settings(cursor_model="auto")
    assert resolve_model(None, s) == "auto"
    assert resolve_model("", s) == "auto"
    assert resolve_model("自动", s) == "auto"
    assert resolve_model("composer-2.5", s) == "composer-2.5"
    # 配置了具体默认模型时，空请求回落到该默认
    s2 = Settings(cursor_model="grok-4.5")
    assert resolve_model("", s2) == "grok-4.5"
