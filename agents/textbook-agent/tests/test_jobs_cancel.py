from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("AGENT_HOME", "/tmp/textbook-agent-test")
os.environ.setdefault("TEXTBOOK_AGENT_TOKEN", "test-token")

from fastapi.testclient import TestClient

from app import jobs
from app.main import app
from app.workspace import ensure_workspace


def test_request_cancel_marks_cancelled() -> None:
    jobs.reset_for_tests()
    row = jobs.create_job("chat", {"message": "hi"})
    out = jobs.request_cancel(row["job_id"], "stopped")
    assert out is not None
    assert out["status"] == "cancelled"
    assert jobs.is_cancelled(row["job_id"])


def test_start_background_skips_when_cancelled() -> None:
    jobs.reset_for_tests()
    row = jobs.create_job("chat", {})
    jobs.request_cancel(row["job_id"])
    called: list[int] = []
    jobs.start_background(row["job_id"], lambda: called.append(1) or {})
    time.sleep(0.15)
    assert called == []
    assert jobs.get_job(row["job_id"])["status"] == "cancelled"


def test_start_background_does_not_complete_after_cancel() -> None:
    jobs.reset_for_tests()
    row = jobs.create_job("chat", {})
    started = threading.Event()

    def fn() -> dict:
        started.wait(1)
        return {"ok": True}

    jobs.start_background(row["job_id"], fn)
    for _ in range(30):
        if (jobs.get_job(row["job_id"]) or {}).get("status") == "running":
            break
        time.sleep(0.03)
    jobs.request_cancel(row["job_id"])
    started.set()
    time.sleep(0.2)
    assert jobs.get_job(row["job_id"])["status"] == "cancelled"


def test_attach_run_cancel_calls_sdk() -> None:
    class Run:
        def __init__(self) -> None:
            self.n = 0

        def cancel(self) -> None:
            self.n += 1

    jobs.reset_for_tests()
    row = jobs.create_job("chat", {})
    run = Run()
    jobs.attach_run(row["job_id"], run)
    jobs.request_cancel(row["job_id"])
    assert run.n == 1


def test_cancel_active_clears_queued() -> None:
    jobs.reset_for_tests()
    a = jobs.create_job("chat", {})
    b = jobs.create_job("chat", {})
    jobs._set(b["job_id"], persist=True, status="completed", result={})
    n = jobs.cancel_active("interrupted")
    assert n == 1
    assert jobs.get_job(a["job_id"])["status"] == "cancelled"
    assert jobs.get_job(b["job_id"])["status"] == "completed"


def test_cancel_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TEXTBOOK_AGENT_TOKEN", "test-token")
    from app.config import settings

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    jobs.reset_for_tests()
    ensure_workspace()
    row = jobs.create_job("chat", {"message": "x"})
    client = TestClient(app)
    r = client.post(
        f"/v1/jobs/{row['job_id']}/cancel",
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
