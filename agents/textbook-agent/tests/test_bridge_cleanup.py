from __future__ import annotations

from app.bridge_cleanup import _collect_orphan_bridge_tree, sweep_orphan_bridges


def test_collect_orphan_bridge_tree_empty() -> None:
    assert _collect_orphan_bridge_tree({}, "/tmp/ws", 1) == set()


def test_collect_orphan_bridge_tree_picks_ppid1() -> None:
    marker = "/data/agent"
    procs = {
        10: (1, f"sh .../cursor-sdk-bridge --workspace {marker}"),
        11: (10, f"node .../cursor-sdk-bridge.js --workspace {marker}"),
        99: (1, "unrelated-daemon"),
    }
    victims = _collect_orphan_bridge_tree(procs, marker, self_pid=1)
    assert 10 in victims
    assert 11 in victims
    assert 99 not in victims


def test_sweep_orphan_bridges_noop() -> None:
    assert isinstance(sweep_orphan_bridges(grace_s=0.1), int)
