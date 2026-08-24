from __future__ import annotations

import io
import json
import zipfile

from app.config import settings
from app import plugins_store
from app.workspace import ensure_workspace


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    settings.agent_home = str(home)
    ensure_workspace()
    return home


def _zip_plugin(plugin_id: str = "demo-plugin") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            f"{plugin_id}/plugin.json",
            json.dumps({"name": plugin_id, "version": "1.0.0", "description": "demo"}),
        )
        zf.writestr(f"{plugin_id}/skills/hello/SKILL.md", "# hello\n")
    return buf.getvalue()


def test_zip_install_enable_disable(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    row = plugins_store.install_zip("demo.zip", _zip_plugin())
    assert row["id"] == "demo-plugin"
    assert row["enabled"] is True
    assert (home / ".cursor" / "plugins" / "local" / "demo-plugin" / "plugin.json").is_file()
    off = plugins_store.set_enabled("demo-plugin", False)
    assert off["enabled"] is False
    assert (home / ".cursor" / "plugins" / "disabled" / "demo-plugin").is_dir()
    on = plugins_store.set_enabled("demo-plugin", True)
    assert on["enabled"] is True
    assert plugins_store.remove_plugin("demo-plugin")


def test_zip_path_traversal_rejected(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.json", "{}")
    try:
        plugins_store.install_zip("evil.zip", buf.getvalue())
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "traversal" in str(exc)


def test_git_not_on_allowlist(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    try:
        plugins_store.install_git("https://evil.example/repo.git")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "allowlist" in str(exc)
    plugins_store.write_allowlist(["https://github.com/allowed/"])
    try:
        plugins_store.install_git("https://github.com/other/repo.git")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "allowlist" in str(exc)
