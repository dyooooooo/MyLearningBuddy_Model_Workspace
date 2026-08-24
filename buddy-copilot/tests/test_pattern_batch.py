"""改进点抽象的分批推进：计数、指令、编排器批循环。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.loop as loop_mod  # noqa: E402
from app.config import Settings  # noqa: E402
from app.loop import LoopEngine  # noqa: E402
from app.pattern_batch import batch_instruction, unassigned_count  # noqa: E402
from app.store import Store  # noqa: E402
from tests.test_loop_flow import (  # noqa: E402
    BRIEF_TEXT,
    DONE_REPORT,
    FakeAgent,
    _decide,
    _pending_approval,
)


def _wait_finished(engine: LoopEngine, sid: str, timeout: float = 20.0) -> str:
    """等编排线程真正退出——批循环中途会短暂置 done，不能只看状态。"""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sid not in engine._runtimes:
            return engine.store.get_session(sid)["status"]
        time.sleep(0.05)
    raise AssertionError("timeout waiting for engine thread")


def test_unassigned_count_ignores_assigned():
    listing = {
        "items": [
            {"id": "a", "pattern_id": "p1"},
            {"id": "b", "pattern_id": ""},
            {"id": "c"},
        ]
    }
    assert unassigned_count(listing) == (2, 3)


def test_batch_instruction_caps_take_at_remaining():
    text = batch_instruction(round_no=2, remaining=7, total=343, batch_size=25)
    assert "第 2 批" in text
    assert "本轮只处理前 7 条" in text
    assert "还有 7 条没挂模式" in text
    assert "已归属 336 条" in text


class _FakeClient:
    """每被问一次，就少 30 条未挂模式，模拟 Agent 一批批挂上去。"""

    steps = [60, 30, 0]

    def __init__(self, *a, **k):
        pass

    def ip_list(self, **kwargs):
        left = _FakeClient.steps[min(_FakeClient.calls, len(_FakeClient.steps) - 1)]
        _FakeClient.calls += 1
        return {
            "items": [{"id": f"ip{i}", "pattern_id": "" if i < left else "p"} for i in range(60)]
        }


_FakeClient.calls = 0


def _engine(tmp_path, monkeypatch, script):
    monkeypatch.setattr(FakeAgent, "script", script)
    monkeypatch.setattr(loop_mod, "SessionAgent", FakeAgent)
    monkeypatch.setattr(loop_mod, "BuddyApiClient", _FakeClient)
    _FakeClient.calls = 0
    settings = Settings(copilot_db_path=tmp_path / "t.db")
    store = Store(settings.db_path)
    engine = LoopEngine(
        settings=settings,
        store=store,
        task_context_builder=lambda kind, payload: "CTX",
    )
    return engine, store


def test_pattern_abstract_runs_until_no_remaining(tmp_path, monkeypatch):
    engine, store = _engine(tmp_path, monkeypatch, [BRIEF_TEXT, DONE_REPORT])
    sid = store.create_session("pattern_abstract", {"fingerprint_hash": "fp1"})
    engine.start(sid)
    _decide(engine, store, sid, _pending_approval(store, sid), True)

    assert _wait_finished(engine, sid) == "done"
    rounds = [x for x in store.get_audit(sid) if x["action"] == "pattern_batch_start"]
    assert len(rounds) == 2, "60 → 30 → 0 应推两批"
    assert any("全部有归属" in str(m["content"]) for m in store.get_messages(sid))


def test_pattern_abstract_runs_goal_when_queue_already_empty(tmp_path, monkeypatch):
    """队列清空后这条轨还要维护检测器，带目标就得照跑，不能直接收工。"""
    engine, store = _engine(tmp_path, monkeypatch, [BRIEF_TEXT, DONE_REPORT])
    monkeypatch.setattr(_FakeClient, "steps", [0])
    sid = store.create_session(
        "pattern_abstract",
        {"fingerprint_hash": "fp1", "user_goal": "给 CHAPTER_EYEBROW_IN_MAIN 收紧 when 并补反例"},
    )
    engine.start(sid)
    _decide(engine, store, sid, _pending_approval(store, sid), True)

    _wait_finished(engine, sid)
    texts = [str(m["content"]) for m in store.get_messages(sid)]
    assert any("转为按目标执行检测器维护" in t for t in texts)
    assert not any("无需抽象" in t for t in texts)


def test_pattern_abstract_reports_nothing_to_do_without_goal(tmp_path, monkeypatch):
    engine, store = _engine(tmp_path, monkeypatch, [BRIEF_TEXT, DONE_REPORT])
    monkeypatch.setattr(_FakeClient, "steps", [0])
    sid = store.create_session("pattern_abstract", {"fingerprint_hash": "fp1"})
    engine.start(sid)
    _decide(engine, store, sid, _pending_approval(store, sid), True)

    assert _wait_finished(engine, sid) == "done"
    assert any("无需抽象" in str(m["content"]) for m in store.get_messages(sid))


def test_pattern_abstract_stops_when_batch_assigns_nothing(tmp_path, monkeypatch):
    engine, store = _engine(tmp_path, monkeypatch, [BRIEF_TEXT, DONE_REPORT])
    monkeypatch.setattr(_FakeClient, "steps", [40, 40])
    sid = store.create_session("pattern_abstract", {"fingerprint_hash": "fp1"})
    engine.start(sid)
    _decide(engine, store, sid, _pending_approval(store, sid), True)

    assert _wait_finished(engine, sid) == "awaiting_user"
    rounds = [x for x in store.get_audit(sid) if x["action"] == "pattern_batch_start"]
    assert len(rounds) == 1, "零产出应立刻停，不空转"
