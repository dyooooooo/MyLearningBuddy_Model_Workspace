"""bridge_cleanup 离线单测（不依赖真实 bridge 进程）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bridge_cleanup import _collect_orphan_bridge_tree, sweep_orphan_bridges  # noqa: E402


def test_collect_orphan_bridge_tree_empty():
    assert _collect_orphan_bridge_tree({}, "/tmp/ws", 1) == set()


def test_collect_orphan_bridge_tree_picks_ppid1():
    marker = "/home/x/buddy-copilot"
    procs = {
        10: (1, f"sh .../cursor-sdk-bridge --workspace {marker}"),
        11: (10, f"node .../cursor-sdk-bridge.js --workspace {marker}"),
        99: (1, "unrelated-daemon"),
    }
    victims = _collect_orphan_bridge_tree(procs, marker, self_pid=1)
    assert 10 in victims
    assert 11 in victims
    assert 99 not in victims


def test_sweep_orphan_bridges_noop():
    # 当前环境可能无孤儿；只校验可调用
    assert isinstance(sweep_orphan_bridges(grace_s=0.1), int)
