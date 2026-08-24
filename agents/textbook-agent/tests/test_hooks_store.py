from __future__ import annotations

from app.config import settings
from app import hooks_store
from app.workspace import ensure_workspace


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    settings.agent_home = str(home)
    ensure_workspace()
    return home


def test_invalid_event_rejected(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    try:
        hooks_store.write_hooks({"version": 1, "hooks": {"notAnEvent": []}})
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "unknown hook event" in str(exc)


def test_command_outside_home_rejected(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    try:
        hooks_store.write_hooks(
            {
                "version": 1,
                "hooks": {
                    "preToolUse": [{"type": "command", "command": "/etc/passwd", "failClosed": True}]
                },
            }
        )
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "outside" in str(exc)


def test_script_chmod_and_roundtrip(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    row = hooks_store.put_hook_script("demo.sh", "#!/bin/bash\necho ok\n")
    path = home / ".cursor" / "hooks" / "demo.sh"
    assert path.is_file()
    assert path.stat().st_mode & 0o111
    assert row["id"] == "demo.sh"
    hooks_store.add_entry(
        "beforeShellExecution",
        {"type": "command", "command": ".cursor/hooks/demo.sh", "failClosed": True},
    )
    data = hooks_store.read_hooks()
    assert "beforeShellExecution" in data["hooks"]
    assert (home / ".cursor" / "hooks.json.bak").is_file() or data["version"] == 1
