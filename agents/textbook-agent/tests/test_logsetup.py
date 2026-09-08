from __future__ import annotations

import json
import time

from app.config import settings
from app.logsetup import (
    bind,
    dump_payload,
    event,
    maybe_cleanup,
    setup_logging,
)


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    settings.agent_home = str(home)
    import app.logsetup as logsetup

    logsetup._configured = False
    logsetup._last_cleanup = 0.0
    setup_logging()
    return home


def test_event_and_payload_land_on_disk(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    with bind(trace_id="trace1", book_id="b1", origin="http"):
        event("tool.start", tool="demo", book_id="b1")
        path = dump_payload("demo.error.txt", "boom")
    lines = (home / "logs" / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    row = json.loads(lines[-1])
    assert row["kind"] == "tool.start"
    assert row["trace_id"] == "trace1"
    assert row["book_id"] == "b1"
    assert path.endswith("demo.error.txt")
    assert (home / path).read_text(encoding="utf-8") == "boom"


def test_cleanup_drops_old_payloads(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    old = home / "logs" / "payloads" / "old" / "x.txt"
    old.parent.mkdir(parents=True)
    old.write_text("x", encoding="utf-8")
    past = time.time() - 40 * 86400
    old.touch()
    import os

    os.utime(old, (past, past))
    out = maybe_cleanup(now=time.time())
    assert out["removed"] >= 1
    assert not old.exists()
