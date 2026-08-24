from __future__ import annotations

import json

import pytest

from app.config_store import write_mcp
from app.workspace import ensure_workspace


def test_mcp_json_roundtrip_and_sdk_mapping(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings

    settings.agent_home = str(tmp_path / "home")
    ensure_workspace()
    write_mcp(
        {
            "mcpServers": {
                "fs": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", str(settings.home)],
                },
                "docs": {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer x"}},
            }
        }
    )
    raw = json.loads((settings.cursor_dir / "mcp.json").read_text(encoding="utf-8"))
    assert "fs" in raw["mcpServers"]
    assert raw["mcpServers"]["docs"]["url"].startswith("https://")

    pytest.importorskip("cursor_sdk")
    from app.sdk_runner import mcp_servers_for_sdk

    mapped = mcp_servers_for_sdk()
    assert "fs" in mapped
    assert "docs" in mapped
    assert getattr(mapped["fs"], "command", None) == "npx"
    assert getattr(mapped["docs"], "url", None) == "https://example.com/mcp"
