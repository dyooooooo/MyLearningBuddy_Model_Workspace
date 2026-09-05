from __future__ import annotations

from app import jobs, sdk_runner
from app.config import settings


def test_count_sdk_active(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    settings.agent_home = str(tmp_path / "home")
    jobs.reset_for_tests()
    a = jobs.create_job("chat", {})
    b = jobs.create_job("chat", {})
    c = jobs.create_job("page_refine", {"book_id": "x"})
    jobs._set(a["job_id"], status="running")
    jobs._set(b["job_id"], status="running")
    jobs._set(c["job_id"], status="queued")
    assert jobs.count_sdk_active() == 2
    assert jobs.count_sdk_active(exclude=a["job_id"]) == 1


def test_recover_bridge_skips_when_sibling_running(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    settings.agent_home = str(tmp_path / "home")
    jobs.reset_for_tests()
    closed: list[int] = []
    swept: list[int] = []
    monkeypatch.setattr(sdk_runner.sdk_client, "close_owned_client", lambda: closed.append(1))
    monkeypatch.setattr(sdk_runner.sdk_client, "ensure_client", lambda: None)
    monkeypatch.setattr(sdk_runner, "sweep_orphan_bridges", lambda **_k: swept.append(1))
    self_job = jobs.create_job("chat", {})
    other = jobs.create_job("chat", {})
    jobs._set(self_job["job_id"], status="running")
    jobs._set(other["job_id"], status="running")
    sdk_runner.reset_send_slots_for_tests()
    sdk_runner.wait_send_slot(other["job_id"])
    sdk_runner.recover_bridge(self_job["job_id"])
    assert closed == []
    assert swept == []
    sdk_runner.release_send_slot(other["job_id"])
    jobs.request_cancel(other["job_id"], "done")
    sdk_runner.recover_bridge(self_job["job_id"])
    assert closed == [1]
    assert swept == [1]


def test_recover_bridge_runs_when_sibling_is_only_queued(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    settings.agent_home = str(tmp_path / "home")
    jobs.reset_for_tests()
    sdk_runner.reset_send_slots_for_tests()
    closed: list[int] = []
    monkeypatch.setattr(sdk_runner.sdk_client, "close_owned_client", lambda: closed.append(1))
    monkeypatch.setattr(sdk_runner.sdk_client, "ensure_client", lambda: None)
    monkeypatch.setattr(sdk_runner, "sweep_orphan_bridges", lambda **_k: None)
    self_job = jobs.create_job("chat", {})
    other = jobs.create_job("chat", {})
    jobs._set(self_job["job_id"], status="running")
    jobs._set(other["job_id"], status="running")
    sdk_runner.recover_bridge(self_job["job_id"])
    assert closed == [1]


def test_prepare_sdk_turn_skips_sweep_when_sibling_running(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    settings.agent_home = str(tmp_path / "home")
    jobs.reset_for_tests()
    swept: list[int] = []
    monkeypatch.setattr(sdk_runner, "apply_cursor_sdk_patches", lambda: None)
    monkeypatch.setattr(sdk_runner, "sweep_orphan_bridges", lambda **_k: swept.append(1))
    self_job = jobs.create_job("chat", {})
    other = jobs.create_job("chat", {})
    jobs._set(self_job["job_id"], status="running")
    jobs._set(other["job_id"], status="running")
    sdk_runner.reset_send_slots_for_tests()
    sdk_runner.wait_send_slot(other["job_id"])
    sdk_runner.prepare_sdk_turn(self_job["job_id"])
    assert swept == []
    sdk_runner.release_send_slot(other["job_id"])
    jobs.request_cancel(other["job_id"], "done")
    sdk_runner.prepare_sdk_turn(self_job["job_id"])
    assert swept == [1]
