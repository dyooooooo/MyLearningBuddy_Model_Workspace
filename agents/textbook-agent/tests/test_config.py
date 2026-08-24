from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.workspace import ensure_workspace


def test_config_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    ensure_workspace()
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    r = client.get("/v1/config", headers=headers)
    assert r.status_code == 200
    assert "mcp" in r.json()
    assert any(s["id"] == "grade" for s in r.json()["skills"])
    assert any(s["id"] == "manage-self" for s in r.json()["skills"])

    put = client.put(
        "/v1/config/mcp",
        headers=headers,
        json={"mcpServers": {"fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]}}},
    )
    assert put.status_code == 200
    assert "fs" in put.json()["mcpServers"]

    skill = client.put(
        "/v1/config/skills/demo",
        headers=headers,
        json={"content": "# demo\n"},
    )
    assert skill.status_code == 200
    rule = client.put(
        "/v1/config/rules/extra",
        headers=headers,
        json={"content": "---\nalwaysApply: true\n---\n# extra\n"},
    )
    assert rule.status_code == 200
    snap = client.get("/v1/config", headers=headers).json()
    assert "fs" in snap["mcp"]["mcpServers"]
    assert any(s["id"] == "demo" for s in snap["skills"])
    assert any(r["id"] == "extra" for r in snap["rules"])
    reload_r = client.post("/v1/config/reload", headers=headers)
    assert reload_r.status_code == 200
    assert reload_r.json()["generation"] >= 1
    gone = client.delete("/v1/config/skills/demo", headers=headers)
    assert gone.status_code == 200
    snap2 = client.get("/v1/config", headers=headers).json()
    assert all(s["id"] != "demo" for s in snap2["skills"])
