"""MCP 通用中间件（_wrap / _gate）离线测试：占位符打回、预算、闸门、step 落库。"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.mcp_server as m  # noqa: E402
from app.store import Store  # noqa: E402
from app.tools.registry import ToolSpec  # noqa: E402


def _setup(tmp_path, monkeypatch) -> tuple[Store, str]:
    store = Store(tmp_path / "mcp.db")
    sid = store.create_session("import_advice", {})
    monkeypatch.setattr(m, "_store", store)
    monkeypatch.setattr(m, "_SESSION_ID", sid)
    monkeypatch.setattr(m, "_local_calls", {"n": 0})
    monkeypatch.setattr(m, "_guard", m.RepeatGuard())
    return store, sid


def _spec(fn, name="corpus.reprocess_page", title="测试工具") -> ToolSpec:
    return ToolSpec(name=name, title=title, fn=fn)


def test_wrap_success_logs_steps(tmp_path, monkeypatch):
    store, sid = _setup(tmp_path, monkeypatch)

    def fn(job_id: str, page_no: int) -> dict:
        return {"ok": True, "job_id": job_id, "page_no": page_no}

    wrapped = m._wrap(_spec(fn), "run", False, "low", "")
    out = json.loads(wrapped(job_id="j1", page_no=3))
    assert out["ok"] is True
    states = [x["content"]["state"] for x in store.get_messages(sid) if x["role"] == "step"]
    assert states == ["start", "done"]
    done = [x["content"] for x in store.get_messages(sid) if x["role"] == "step"][-1]
    assert done["tool"] == "corpus.reprocess_page" and "elapsed_ms" in done


def test_wrap_placeholder_rejected(tmp_path, monkeypatch):
    store, sid = _setup(tmp_path, monkeypatch)
    called = []

    def fn(job_id: str) -> dict:
        called.append(job_id)
        return {"ok": True}

    wrapped = m._wrap(_spec(fn), "run", False, "low", "")
    out = json.loads(wrapped(job_id="<job_id>"))
    assert "占位符" in out["error"]
    assert called == []  # handler 未执行


def test_wrap_error_returned_to_agent(tmp_path, monkeypatch):
    """工具异常不炸进程，作为结果返回给 Agent 自愈。"""
    store, sid = _setup(tmp_path, monkeypatch)

    def fn() -> dict:
        raise RuntimeError("下游 502")

    wrapped = m._wrap(_spec(fn, name="harness.run_golden"), "read", False, "low", "")
    out = json.loads(wrapped())
    assert "下游 502" in out["error"] and "hint" in out
    states = [x["content"]["state"] for x in store.get_messages(sid) if x["role"] == "step"]
    assert states == ["start", "error"]


def test_wrap_budget_exceeded(tmp_path, monkeypatch):
    store, sid = _setup(tmp_path, monkeypatch)
    store.bump_counter(sid, "tool_calls", m._perms.budgets.max_tool_calls)

    def fn() -> dict:
        return {"ok": True}

    wrapped = m._wrap(_spec(fn, name="editions.overview"), "read", False, "low", "")
    out = json.loads(wrapped())
    assert "预算" in out["error"]


def test_budget_counted_across_processes(tmp_path, monkeypatch):
    """预算落库：另一个 MCP 进程用掉的额度也算在同一会话头上。"""
    store, sid = _setup(tmp_path, monkeypatch)
    other_process = Store(tmp_path / "mcp.db")  # 同一个库，不同 Store 实例
    other_process.bump_counter(sid, "tool_calls", m._perms.budgets.max_tool_calls)

    def fn() -> dict:
        return {"ok": True}

    wrapped = m._wrap(_spec(fn, name="editions.overview"), "read", False, "low", "")
    assert "预算" in json.loads(wrapped())["error"]


def test_gate_approved_runs_handler(tmp_path, monkeypatch):
    store, sid = _setup(tmp_path, monkeypatch)

    def fn(edition_id: str, note: str = "") -> dict:
        return {"published": edition_id}

    wrapped = m._wrap(
        _spec(fn, name="editions.publish", title="发布方案"), "gated", True, "medium", "发布影响后续导入"
    )
    result: dict = {}
    th = threading.Thread(target=lambda: result.update(json.loads(wrapped(edition_id="x"))))
    th.start()
    # 等批准卡出现 → 模拟用户批准
    aid = ""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not aid:
        pend = [a for a in store.list_approvals(sid, "editions.publish") if a["status"] == "pending"]
        if pend:
            aid = pend[0]["id"]
        time.sleep(0.05)
    assert aid, "闸门未创建批准卡"
    # 批准卡消息带元数据（前端通用渲染）
    cards = [x["content"] for x in store.get_messages(sid) if x["role"] == "approval"]
    assert cards and cards[0]["title"] == "发布方案" and cards[0]["risk"] == "medium"
    store.decide_approval(aid, True)
    th.join(10)
    assert result.get("published") == "x"


def test_gate_rejected_returns_user_note(tmp_path, monkeypatch):
    store, sid = _setup(tmp_path, monkeypatch)
    called = []

    def fn(paths: list[str], reason: str) -> dict:
        called.append(paths)
        return {"approved": True}

    wrapped = m._wrap(
        _spec(fn, name="fs.request_edit", title="申请修改文件"), "gated", True, "high", ""
    )
    result: dict = {}
    th = threading.Thread(
        target=lambda: result.update(
            json.loads(wrapped(paths=["learning-buddy-api/tests/t.py"], reason="补测试"))
        )
    )
    th.start()
    aid = ""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not aid:
        pend = [a for a in store.list_approvals(sid, "fs.request_edit") if a["status"] == "pending"]
        if pend:
            aid = pend[0]["id"]
        time.sleep(0.05)
    assert aid
    store.decide_approval(aid, False, note="现在别改测试")
    th.join(10)
    assert result.get("approved") is False
    assert "现在别改测试" in result.get("user_note", "")
    assert called == []  # 被拒绝时 handler 不执行
    skipped = [
        x["content"] for x in store.get_messages(sid)
        if x["role"] == "step" and x["content"].get("state") == "skipped"
    ]
    assert skipped and "别改测试" in skipped[0]["note"]


def test_wrap_done_step_carries_result_digest(tmp_path, monkeypatch):
    """done step 落库带结果摘要（前端步骤行展开查看）。"""
    store, sid = _setup(tmp_path, monkeypatch)

    def fn() -> dict:
        return {"ok": True, "detail": "x" * 5000}

    wrapped = m._wrap(_spec(fn, name="editions.overview"), "read", False, "low", "")
    wrapped()
    done = [
        x["content"] for x in store.get_messages(sid)
        if x["role"] == "step" and x["content"].get("state") == "done"
    ][-1]
    assert "result" in done and len(done["result"]) <= 1300  # 摘要截断
    assert '"ok": true' in done["result"]


def test_gate_session_always_allow(tmp_path, monkeypatch):
    """remember=True 批准后，同一动作本会话内自动放行（不再弹卡）。"""
    store, sid = _setup(tmp_path, monkeypatch)
    calls = []

    def fn(edition_id: str) -> dict:
        calls.append(edition_id)
        return {"published": edition_id}

    wrapped = m._wrap(
        _spec(fn, name="editions.publish", title="发布方案"), "gated", True, "medium", ""
    )
    # 第一次：批准并记住
    result: dict = {}
    th = threading.Thread(target=lambda: result.update(json.loads(wrapped(edition_id="a"))))
    th.start()
    aid = ""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not aid:
        pend = [a for a in store.list_approvals(sid, "editions.publish") if a["status"] == "pending"]
        if pend:
            aid = pend[0]["id"]
        time.sleep(0.05)
    assert aid
    store.decide_approval(aid, True, remember=True)
    th.join(10)
    assert result.get("published") == "a"
    assert store.has_session_allow(sid, "editions.publish")

    # 第二次：直接放行，无新批准卡
    before = len(store.list_approvals(sid, "editions.publish"))
    out = json.loads(wrapped(edition_id="b"))
    assert out.get("published") == "b"
    assert len(store.list_approvals(sid, "editions.publish")) == before
    assert calls == ["a", "b"]
    # 不跨动作：其他 gated 工具仍需批准
    assert not store.has_session_allow(sid, "docker.rebuild")


def test_session_update_todos_plugin(tmp_path, monkeypatch):
    """todos 插件：合法清单落库为 todos 消息；非法 JSON 打回。"""
    from app.tools.plugins.session_tools import build as build_session
    from app.tools.registry import ToolContext

    store = Store(tmp_path / "todos.db")
    sid = store.create_session("import_advice", {})
    ctx = ToolContext(settings=None, store=store, client=None, session_id=sid, kind="import_advice")
    (spec,) = build_session(ctx)
    assert spec.name == "session.update_todos"

    out = spec.fn('[{"text": "扫描 PDF", "status": "done"}, {"text": "推荐方案"}]')
    assert out["ok"] is True and out["count"] == 2
    todos = [x["content"] for x in store.get_messages(sid) if x["role"] == "todos"]
    assert todos[-1]["items"][0] == {"text": "扫描 PDF", "status": "done"}
    assert todos[-1]["items"][1]["status"] == "pending"

    bad = spec.fn("not json")
    assert "非法" in bad["error"]


def test_gate_cancelled_session_aborts(tmp_path, monkeypatch):
    store, sid = _setup(tmp_path, monkeypatch)

    def fn() -> dict:
        return {"ok": True}

    wrapped = m._wrap(_spec(fn, name="docker.rebuild", title="重建"), "gated", True, "high", "")
    result: dict = {}
    th = threading.Thread(target=lambda: result.update(json.loads(wrapped())))
    th.start()
    time.sleep(0.3)
    store.update_session(sid, status="cancelled")
    th.join(10)
    assert result.get("approved") is False
    assert "取消" in result.get("user_note", "")
