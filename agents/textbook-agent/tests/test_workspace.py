from __future__ import annotations

from app import surfaces, workspace
from app.config import settings
from app.workspace import ensure_workspace, reset_for_tests


def test_ensure_workspace_same_home_skips_rescan(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_home", str(tmp_path / "home"))
    reset_for_tests()
    calls = {"n": 0}
    real = workspace._copy_if_missing

    def _count(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(workspace, "_copy_if_missing", _count)
    ensure_workspace()
    first = calls["n"]
    assert first > 0
    ensure_workspace()
    assert calls["n"] == first


def test_ensure_workspace_new_home_reseeds(tmp_path, monkeypatch) -> None:
    reset_for_tests()
    monkeypatch.setattr(settings, "agent_home", str(tmp_path / "a"))
    ensure_workspace()
    monkeypatch.setattr(settings, "agent_home", str(tmp_path / "b"))
    calls = {"n": 0}
    real = workspace._copy_if_missing

    def _count(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(workspace, "_copy_if_missing", _count)
    ensure_workspace()
    assert calls["n"] > 0


def test_surface_seed_upgrades_only_untouched_files(tmp_path, monkeypatch) -> None:
    """surfaces 与 tools 共用同一本台账：没人动过的随镜像升级，动过的原样保留。"""
    monkeypatch.setattr(settings, "agent_home", str(tmp_path / "home"))
    reset_for_tests()
    surfaces.reset_for_tests()

    slot = tmp_path / "seed" / "page-body"
    (slot / "proposals").mkdir(parents=True)
    (slot / "theme.css").write_text("v1\n", encoding="utf-8")
    (slot / "profile.yaml").write_text("v1\n", encoding="utf-8")
    (slot / "proposals" / "draft.mjs").write_text("draft\n", encoding="utf-8")
    monkeypatch.setattr(surfaces, "SEED", tmp_path / "seed")

    surfaces.ensure_surfaces()
    live = settings.home / "surfaces" / "page-body"
    assert (live / "theme.css").read_text(encoding="utf-8") == "v1\n"
    # 未审草稿归 agent，播种不碰
    assert not (live / "proposals" / "draft.mjs").exists()

    # agent 改了 profile.yaml、没碰 theme.css；随后镜像把两者都升到 v2
    (live / "profile.yaml").write_text("agent edit\n", encoding="utf-8")
    (slot / "theme.css").write_text("v2\n", encoding="utf-8")
    (slot / "profile.yaml").write_text("v2\n", encoding="utf-8")

    surfaces.ensure_surfaces()
    assert (live / "theme.css").read_text(encoding="utf-8") == "v2\n"
    assert (live / "profile.yaml").read_text(encoding="utf-8") == "agent edit\n"
