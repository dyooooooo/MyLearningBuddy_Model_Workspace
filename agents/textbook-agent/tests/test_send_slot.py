"""Shared-bridge send slots: queued jobs must not fill the cap or deadlock."""

from __future__ import annotations

import threading
import time

from app import jobs, sdk_runner
from app.config import settings


def _home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    settings.agent_home = str(tmp_path / "home")
    jobs.reset_for_tests()
    sdk_runner.reset_send_slots_for_tests()


def test_queued_running_jobs_do_not_fill_the_cap(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "max_concurrent_sends", 1)
    a = jobs.create_job("chat", {})
    b = jobs.create_job("chat", {})
    jobs._set(a["job_id"], status="running")
    jobs._set(b["job_id"], status="running")
    sdk_runner.wait_send_slot(b["job_id"])
    assert sdk_runner.count_held_slots() == 1
    sdk_runner.release_send_slot(b["job_id"])
    assert sdk_runner.count_held_slots() == 0


def test_second_send_waits_then_claims_after_release(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "max_concurrent_sends", 1)
    first = jobs.create_job("chat", {})
    second = jobs.create_job("chat", {})
    jobs._set(first["job_id"], status="running")
    jobs._set(second["job_id"], status="running")
    sdk_runner.wait_send_slot(first["job_id"])
    got: list[str] = []

    def _claim() -> None:
        sdk_runner.wait_send_slot(second["job_id"])
        got.append("claimed")

    worker = threading.Thread(target=_claim)
    worker.start()
    time.sleep(0.6)
    assert got == []
    traces = [step.get("title") for step in (jobs.get_job(second["job_id"]) or {}).get("trace") or []]
    assert "queued" in traces
    sdk_runner.release_send_slot(first["job_id"])
    worker.join(timeout=3)
    assert got == ["claimed"]
    assert sdk_runner.count_held_slots(exclude=first["job_id"]) == 1
    sdk_runner.release_send_slot(second["job_id"])


def test_cancel_unblocks_waiter(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "max_concurrent_sends", 1)
    first = jobs.create_job("chat", {})
    second = jobs.create_job("chat", {})
    sdk_runner.wait_send_slot(first["job_id"])
    err: list[BaseException] = []

    def _claim() -> None:
        try:
            sdk_runner.wait_send_slot(second["job_id"])
        except BaseException as exc:
            err.append(exc)

    worker = threading.Thread(target=_claim)
    worker.start()
    time.sleep(0.5)
    jobs.request_cancel(second["job_id"], "stopped")
    worker.join(timeout=3)
    assert err and isinstance(err[0], jobs.JobCancelled)
    sdk_runner.release_send_slot(first["job_id"])
