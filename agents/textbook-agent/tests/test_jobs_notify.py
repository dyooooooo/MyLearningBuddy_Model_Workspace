from __future__ import annotations

from app import bus, jobs


def test_notify_keeps_slow_subscriber() -> None:
    bus.reset_for_tests()
    q = bus.subscribe()
    row = {
        "job_id": "j1",
        "kind": "chat",
        "status": "running",
        "payload": {},
        "result": None,
        "error": "",
        "trace": [],
        "created_at": "",
        "updated_at": "",
    }
    jobs._notify(row)
    row["status"] = "running"
    row["trace"] = [{"t": "1", "kind": "tool", "title": "read", "detail": ""}]
    jobs._notify(row)
    row["status"] = "completed"
    jobs._notify(row)
    assert q.qsize() == 1
    snap = q.get_nowait()
    assert snap["status"] == "completed"
    assert snap["type"] == "job"
    bus.unsubscribe(q)


def test_import_meta_survives_later_job_tick() -> None:
    bus.reset_for_tests()
    q = bus.subscribe()
    bus.publish("import_meta", book_id="b1", label="英语必修第一册", subject="english")
    jobs._notify(
        {
            "job_id": "j2",
            "kind": "chat",
            "status": "running",
            "payload": {},
            "result": None,
            "error": "",
            "trace": [],
            "created_at": "",
            "updated_at": "",
        }
    )
    types = []
    while True:
        try:
            types.append(q.get_nowait().get("type"))
        except Exception:
            break
    bus.unsubscribe(q)
    assert types == ["import_meta", "job"]
