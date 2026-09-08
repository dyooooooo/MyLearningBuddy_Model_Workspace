"""workspace/tools 的动态加载器：运行态优先、seed 兜底、缺属性不算加载成功。"""

from __future__ import annotations

import sys

import pytest

from app import workspace_module
from app.config import settings


@pytest.fixture
def tools(tmp_path, monkeypatch):
    """把运行态 tools/ 指到 tmp，seed 指到另一个 tmp，两边都放同名模块。"""
    # tools_dir 是 agent_home 派生出来的 property，只能改 agent_home
    home = tmp_path / "home"
    runtime = home / "tools"
    seed = tmp_path / "seed"
    runtime.mkdir(parents=True)
    seed.mkdir()
    monkeypatch.setattr(settings, "agent_home", str(home))
    monkeypatch.setattr(workspace_module, "_SEED", seed)
    assert settings.tools_dir == runtime
    for path in (str(runtime), str(seed)):
        while path in sys.path:
            sys.path.remove(path)
    yield runtime, seed
    for name in ("probe_mod",):
        sys.modules.pop(name, None)
    for path in (str(runtime), str(seed)):
        while path in sys.path:
            sys.path.remove(path)


def test_runtime_workspace_wins_over_seed_on_sys_path(tools) -> None:
    """ensure_on_path 必须让运行态排在 seed 前面。

    两个目录都插在 0 位时，正序插会把 seed 顶到最前，于是普通 `import toc_lexicon`
    拿到的是镜像里的旧代码 —— 改了 workspace 却不生效。
    """
    runtime, seed = tools
    workspace_module.ensure_on_path()
    assert sys.path.index(str(runtime)) < sys.path.index(str(seed))


def test_ensure_on_path_reorders_preexisting_entry(tools) -> None:
    """调用方先手插过运行态目录时，seed 不能反超它。"""
    runtime, seed = tools
    sys.path.insert(0, str(runtime))
    workspace_module.ensure_on_path()
    assert sys.path.index(str(runtime)) < sys.path.index(str(seed))
    assert sys.path.count(str(runtime)) == 1


def test_load_prefers_runtime_copy(tools) -> None:
    runtime, seed = tools
    (runtime / "probe_mod.py").write_text("WHO = 'runtime'\n", encoding="utf-8")
    (seed / "probe_mod.py").write_text("WHO = 'seed'\n", encoding="utf-8")
    module = workspace_module.load("probe_mod", require=("WHO",))
    assert module.WHO == "runtime"


def test_load_falls_back_to_seed_when_runtime_incomplete(tools) -> None:
    """运行态副本缺了调用方要的属性（旧版本 seed 或坏改动）时退回镜像，别整进程崩掉。"""
    runtime, seed = tools
    (runtime / "probe_mod.py").write_text("OTHER = 1\n", encoding="utf-8")
    (seed / "probe_mod.py").write_text("WHO = 'seed'\n", encoding="utf-8")
    module = workspace_module.load("probe_mod", require=("WHO",))
    assert module.WHO == "seed"


def test_load_falls_back_when_runtime_raises(tools) -> None:
    runtime, seed = tools
    (runtime / "probe_mod.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    (seed / "probe_mod.py").write_text("WHO = 'seed'\n", encoding="utf-8")
    module = workspace_module.load("probe_mod", require=("WHO",))
    assert module.WHO == "seed"


def test_load_missing_module_raises(tools) -> None:
    with pytest.raises(ImportError):
        workspace_module.load("probe_mod", require=("WHO",))


def test_load_is_cached(tools) -> None:
    runtime, _seed = tools
    (runtime / "probe_mod.py").write_text("WHO = 'runtime'\n", encoding="utf-8")
    first = workspace_module.load("probe_mod", require=("WHO",))
    (runtime / "probe_mod.py").write_text("WHO = 'changed'\n", encoding="utf-8")
    second = workspace_module.load("probe_mod", require=("WHO",))
    assert first is second
    assert second.WHO == "runtime"
