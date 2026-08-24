"""重启清扫：执行中残留停成可续，不标 failed。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline_tick import symptom_rows_for_tick  # noqa: E402
from app.session_sweep import RESTART_INTERRUPT_TEXT, sweep_zombie_sessions  # noqa: E402
from app.store import Store  # noqa: E402


def test_sweep_parks_executing_as_awaiting_user(tmp_path):
    store = Store(tmp_path / "c.db")
    sid = store.create_session("import_advice", {"minio_key": "k"})
    store.update_session(sid, status="executing")
    parked = store.create_session("import_advice", {})
    store.update_session(parked, status="awaiting_user")
    done = store.create_session("import_advice", {})
    store.update_session(done, status="done")
    live = store.create_session("import_advice", {})
    store.update_session(live, status="planning")

    n = sweep_zombie_sessions(store, is_running=lambda s: s == live)
    assert n == 1

    zombie = store.get_session(sid)
    assert zombie["status"] == "awaiting_user"
    assert zombie["payload"]["interrupted_by_restart"] is True
    msgs = store.get_messages(sid, after_seq=0)
    assert msgs[-1]["role"] == "system"
    assert msgs[-1]["content"]["restart_interrupt"] is True
    assert "服务重启" in msgs[-1]["content"]["text"]
    assert RESTART_INTERRUPT_TEXT in msgs[-1]["content"]["text"]
    assert store.get_session(parked)["status"] == "awaiting_user"
    assert store.get_session(done)["status"] == "done"
    assert store.get_session(live)["status"] == "planning"


def test_symptom_rows_for_tick_groups_by_type():
    rows = symptom_rows_for_tick(
        {
            "known": [
                {
                    "suspect_type": "aside_in_main",
                    "page_no": 3,
                    "playbook": "把块标 aside",
                    "remediation_kind": "playbook",
                    "landing_target": "layout_rules",
                },
                {
                    "suspect_type": "aside_in_main",
                    "page_no": 7,
                    "playbook": "把块标 aside",
                    "remediation_kind": "playbook",
                },
            ],
            "unknown": [{"suspect_type": "mystery", "page_no": 9}],
        }
    )
    by_name = {r["suspect_type"]: r for r in rows}
    assert by_name["aside_in_main"]["count"] == 2
    assert by_name["aside_in_main"]["pages"] == [3, 7]
    assert by_name["aside_in_main"]["known"] is True
    assert "aside" in by_name["aside_in_main"]["playbook"]
    assert by_name["mystery"]["known"] is False
    assert by_name["mystery"]["count"] == 1
