from __future__ import annotations

import time

from app import bridge_cleanup
from app.bridge_cleanup import (
    _collect_orphan_bridge_tree,
    close_sdk_bridge,
    live_bridge_pids,
    sweep_orphan_bridges,
)

MARKER = "/data/agent"


def _tree(sh_pid: int, sh_ppid: int, node_pid: int) -> dict[int, tuple[int, str]]:
    return {
        sh_pid: (sh_ppid, f"sh .../cursor-sdk-bridge --workspace {MARKER}"),
        node_pid: (sh_pid, f"node .../cursor-sdk-bridge.js --workspace {MARKER}"),
    }


def _live(monkeypatch, pids: set[int] | None) -> None:
    monkeypatch.setattr(bridge_cleanup, "live_bridge_pids", lambda: pids)


def test_collect_orphan_bridge_tree_empty() -> None:
    assert _collect_orphan_bridge_tree({}, "/tmp/ws", 1) == set()


def test_collect_orphan_bridge_tree_picks_ppid1(monkeypatch) -> None:
    _live(monkeypatch, set())
    procs = _tree(10, 1, 11) | {99: (1, "unrelated-daemon")}
    victims = _collect_orphan_bridge_tree(procs, MARKER, self_pid=42)
    assert 10 in victims
    assert 11 in victims
    assert 99 not in victims


def test_live_bridge_tree_is_spared_when_we_are_pid1(monkeypatch) -> None:
    """Our own live bridge also has ppid=1 in a container; it must survive."""
    _live(monkeypatch, {10})
    assert _collect_orphan_bridge_tree(_tree(10, 1, 11), MARKER, self_pid=1) == set()


def test_leftover_tree_is_reaped_when_we_are_pid1(monkeypatch) -> None:
    """The old PID-1 guard made this a no-op, so dead bridges piled up forever."""
    procs = _tree(10, 1, 11) | _tree(20, 1, 21)
    _live(monkeypatch, {20})
    victims = _collect_orphan_bridge_tree(procs, MARKER, self_pid=1)
    assert victims == {10, 11}


def test_orphan_node_without_its_sh_is_reaped_when_we_are_pid1(monkeypatch) -> None:
    """SDK close kills only the sh wrapper, so the node worker is what leaks."""
    procs = {
        11: (1, f"node .../cursor-sdk-bridge.js --workspace {MARKER}"),
        20: (1, f"sh .../cursor-sdk-bridge --workspace {MARKER}"),
        21: (20, f"node .../cursor-sdk-bridge.js --workspace {MARKER}"),
    }
    _live(monkeypatch, {20})
    assert _collect_orphan_bridge_tree(procs, MARKER, self_pid=1) == {11}


def test_unknown_live_bridge_spares_everything_when_we_are_pid1(monkeypatch) -> None:
    """If the SDK internals move, prefer leaking over killing the live bridge."""
    _live(monkeypatch, None)
    assert _collect_orphan_bridge_tree(_tree(10, 1, 11), MARKER, self_pid=1) == set()


def test_orphan_mcp_sidecar_is_reaped(monkeypatch) -> None:
    """The MCP sidecar is spawned by the bridge, so ppid=1 always means orphan."""
    _live(monkeypatch, set())
    procs = {77: (1, f"python -m app.mcp_server --home {MARKER}")}
    assert _collect_orphan_bridge_tree(procs, MARKER, self_pid=1) == {77}


class _FakeBridge:
    def __init__(self, pid: int | None) -> None:
        self.process = type("P", (), {"pid": pid})()
        self.endpoint = type("E", (), {"pid": None})()


def _fake_sdk_client(monkeypatch, **attrs: object) -> None:
    import cursor_sdk

    monkeypatch.setattr(cursor_sdk, "_client", type("C", (), attrs)())


def test_live_bridge_pids_reports_none_when_sdk_globals_move(monkeypatch) -> None:
    """No _DEFAULT_BRIDGE means we cannot tell live from dead — say so."""
    _fake_sdk_client(monkeypatch)
    assert live_bridge_pids() is None


def test_live_bridge_pids_reports_empty_when_sdk_owns_no_bridge(monkeypatch) -> None:
    _fake_sdk_client(monkeypatch, _DEFAULT_BRIDGE=None)
    assert live_bridge_pids() == set()


def test_live_bridge_pids_reads_the_bridge_pid(monkeypatch) -> None:
    _fake_sdk_client(monkeypatch, _DEFAULT_BRIDGE=_FakeBridge(4242))
    assert live_bridge_pids() == {4242}


def test_live_bridge_pids_reports_none_for_a_bridge_without_a_pid(monkeypatch) -> None:
    """A pid-less bridge object is not evidence that no bridge is running."""
    _fake_sdk_client(monkeypatch, _DEFAULT_BRIDGE=_FakeBridge(None))
    assert live_bridge_pids() is None


def test_close_sdk_bridge_gives_up_instead_of_blocking(monkeypatch) -> None:
    """A wedged bridge used to hold the caller for httpx's full 60s timeout."""
    import cursor_sdk

    monkeypatch.setattr(cursor_sdk, "close_default_client", lambda: time.sleep(30))
    started = time.monotonic()
    assert close_sdk_bridge(timeout=0.3) is False
    assert time.monotonic() - started < 5


def test_close_sdk_bridge_reports_success(monkeypatch) -> None:
    import cursor_sdk

    monkeypatch.setattr(cursor_sdk, "close_default_client", lambda: None)
    assert close_sdk_bridge(timeout=5) is True


def test_sweep_orphan_bridges_noop() -> None:
    assert isinstance(sweep_orphan_bridges(grace_s=0.1), int)
