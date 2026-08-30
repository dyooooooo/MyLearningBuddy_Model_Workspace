from __future__ import annotations

import json

from app import cursor_auth
from app.config import settings


def test_ensure_writes_cli_and_sdk_auth(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr(settings, "agent_home", str(tmp_path))
    monkeypatch.setattr(settings, "cursor_api_key", "crsr_test_key")
    cursor_auth.ensure_cursor_credentials()
    cli = json.loads((tmp_path / ".config" / "cursor" / "auth.json").read_text())
    sdk = json.loads((tmp_path / ".cursor" / "sdk" / "auth.json").read_text())
    assert cli["apiKey"] == "crsr_test_key"
    assert "accessToken" not in cli
    assert "refreshToken" not in cli
    assert sdk["apiKey"] == "crsr_test_key"
    assert sdk["version"] == 1


def test_ensure_drops_tokens_even_when_key_matches(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr(settings, "agent_home", str(tmp_path))
    monkeypatch.setattr(settings, "cursor_api_key", "crsr_same")
    path = tmp_path / ".config" / "cursor" / "auth.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"apiKey": "crsr_same", "accessToken": "keep-me", "refreshToken": "keep-too"}),
        encoding="utf-8",
    )
    cursor_auth.ensure_cursor_credentials()
    data = json.loads(path.read_text())
    assert data["apiKey"] == "crsr_same"
    assert "accessToken" not in data
    assert "refreshToken" not in data


def test_ensure_drops_tokens_when_key_changes(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr(settings, "agent_home", str(tmp_path))
    monkeypatch.setattr(settings, "cursor_api_key", "crsr_new")
    path = tmp_path / ".config" / "cursor" / "auth.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"apiKey": "crsr_old", "accessToken": "stale", "refreshToken": "stale"}),
        encoding="utf-8",
    )
    cursor_auth.ensure_cursor_credentials()
    data = json.loads(path.read_text())
    assert data["apiKey"] == "crsr_new"
    assert "accessToken" not in data


def test_live_api_key_prefers_env(monkeypatch) -> None:
    monkeypatch.setattr(settings, "cursor_api_key", "from-settings")
    monkeypatch.setenv("CURSOR_API_KEY", " from-env ")
    assert cursor_auth.live_api_key() == "from-env"


def test_assert_ready_skips_probe_under_pytest(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr(settings, "agent_home", str(tmp_path))
    monkeypatch.setattr(settings, "cursor_api_key", "crsr_test_key")

    def boom(**_k: object) -> dict:
        raise AssertionError("probe should not run under pytest")

    monkeypatch.setattr(cursor_auth, "probe_status", boom)
    cursor_auth.assert_cursor_ready()


def test_probe_status_reports_empty_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_home", str(tmp_path))
    monkeypatch.setattr(settings, "cursor_api_key", "")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    cursor_auth._probe_cache = None
    out = cursor_auth.probe_status(force=True)
    assert out["key_present"] is False
    assert out["cloud_ok"] is False
    assert out["reason"] == "empty"


BRIDGE_DOWN = "Bridge request failed: ConnectError: [Errno 111] Connection refused"


def _probe(monkeypatch, tmp_path, key: str, results: list[object]) -> dict:
    """Drive probe_status with a scripted Cursor.me(); results pop in order."""
    monkeypatch.setattr(settings, "agent_home", str(tmp_path))
    monkeypatch.setenv("CURSOR_API_KEY", key)
    monkeypatch.setattr(cursor_auth, "_drop_dead_bridge", lambda: True)
    queue = list(results)

    def fake_probe(key_arg: str) -> dict:
        assert key_arg == key
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def me(*, api_key: str):
        return fake_probe(api_key)

    monkeypatch.setattr(cursor_auth, "_cursor_me", me)
    cursor_auth._probe_cache = None
    out = cursor_auth.probe_status(force=True)
    out["_left"] = len(queue)
    return out


def test_probe_reports_bridge_not_auth_when_bridge_is_down(tmp_path, monkeypatch) -> None:
    out = _probe(
        monkeypatch, tmp_path, "crsr_k", [RuntimeError(BRIDGE_DOWN), RuntimeError(BRIDGE_DOWN)]
    )
    assert out["cloud_ok"] is False
    assert out["reason"] == "bridge"
    assert "登录失效" not in out["detail"]


def test_probe_retries_after_dropping_dead_bridge(tmp_path, monkeypatch) -> None:
    class Me:
        api_key_name = "mylearningbuddy"

    out = _probe(monkeypatch, tmp_path, "crsr_k", [RuntimeError(BRIDGE_DOWN), Me()])
    assert out["cloud_ok"] is True
    assert out["reason"] == "ok"
    assert out["detail"] == "mylearningbuddy"
    assert out["_left"] == 0


def test_probe_reports_auth_when_key_is_rejected(tmp_path, monkeypatch) -> None:
    out = _probe(monkeypatch, tmp_path, "crsr_k", [RuntimeError("Unauthorized: invalid api key")])
    assert out["reason"] == "auth"
    assert out["_left"] == 0


def test_bridge_failure_does_not_block_a_run(tmp_path, monkeypatch) -> None:
    """A dead bridge is recoverable downstream; assert_cursor_ready must let it pass."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(settings, "agent_home", str(tmp_path))
    monkeypatch.setenv("CURSOR_API_KEY", "crsr_k")
    monkeypatch.setattr(
        cursor_auth,
        "probe_status",
        lambda **_k: {"cloud_ok": False, "reason": "bridge", "detail": "Cursor 本地桥接失败，请重试"},
    )
    cursor_auth.assert_cursor_ready()


def test_auth_failure_still_blocks_a_run(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(settings, "agent_home", str(tmp_path))
    monkeypatch.setenv("CURSOR_API_KEY", "crsr_k")
    monkeypatch.setattr(
        cursor_auth, "probe_status", lambda **_k: {"cloud_ok": False, "reason": "auth", "detail": "x"}
    )
    try:
        cursor_auth.assert_cursor_ready()
    except RuntimeError as exc:
        assert "登录失效" in str(exc)
    else:
        raise AssertionError("auth failure must block the run")


def _no_pytest_marker(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(settings, "agent_home", str(tmp_path))
    monkeypatch.setenv("CURSOR_API_KEY", "crsr_k")


def test_key_is_live_only_false_when_the_key_is_rejected(tmp_path, monkeypatch) -> None:
    _no_pytest_marker(monkeypatch, tmp_path)
    monkeypatch.setattr(cursor_auth, "probe_status", lambda **_k: {"reason": "auth"})
    assert cursor_auth.key_is_live() is False


def test_key_is_live_when_only_the_bridge_is_down(tmp_path, monkeypatch) -> None:
    """A dead bridge says nothing about the key, so the caller may restart it."""
    _no_pytest_marker(monkeypatch, tmp_path)
    monkeypatch.setattr(cursor_auth, "probe_status", lambda **_k: {"reason": "bridge"})
    assert cursor_auth.key_is_live() is True


def test_key_is_live_when_the_cloud_accepts_it(tmp_path, monkeypatch) -> None:
    _no_pytest_marker(monkeypatch, tmp_path)
    monkeypatch.setattr(cursor_auth, "probe_status", lambda **_k: {"reason": "ok"})
    assert cursor_auth.key_is_live() is True


def test_key_is_live_false_without_a_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_home", str(tmp_path))
    monkeypatch.setattr(settings, "cursor_api_key", "")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    assert cursor_auth.key_is_live() is False


def test_key_is_live_treats_an_unknown_probe_failure_as_live(tmp_path, monkeypatch) -> None:
    """Only an explicit rejection is evidence; anything else must stay recoverable."""
    _no_pytest_marker(monkeypatch, tmp_path)
    monkeypatch.setattr(cursor_auth, "probe_status", lambda **_k: {"reason": "unknown"})
    assert cursor_auth.key_is_live() is True
