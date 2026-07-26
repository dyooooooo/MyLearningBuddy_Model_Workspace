"""自主运行监督器端到端演练（模拟 Agent，不需要 Cursor API key）。

验证 v2 流程：简报 → 确认卡 → 连续自主运行 → 最终报告（done/blocked/追问）→
fs_guard 终检（越权回滚）→ 会话状态机。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.loop as loop_mod  # noqa: E402
from app.config import Settings  # noqa: E402
from app.loop import LoopEngine  # noqa: E402
from app.store import Store  # noqa: E402

BRIEF_TEXT = "行动简报：先看方案清单，再对第 3 页做 content_correct，最后 page_diff 验收。"

DONE_REPORT = """已执行完成。
```json
{"status": "done", "summary": "第 3 页已重处理", "evidence": ["page_diff 相似度 0.97"]}
```"""

BLOCKED_REPORT = """需要你决定。
```json
{"status": "blocked", "summary": "两个候选方案", "question": "用方案 A 还是 fork 新方案？"}
```"""


class FakeAgent:
    """按脚本逐轮回复；script 用完后重复最后一条。"""

    script: list[str] = [BRIEF_TEXT, DONE_REPORT]

    def __init__(self, *a, **k):
        self.agent_id = "fake-agent"
        self.model = k.get("model") or "auto"
        self.recreated = False
        self.asked: list[str] = []

    def ask(self, prompt, on_text=None, *, force: bool = False):
        self.asked.append(prompt)
        i = min(len(self.asked) - 1, len(self.script) - 1)
        text = self.script[i]
        if on_text:
            on_text(text)
        return text

    def cancel_current(self):
        pass

    def close(self):
        pass


def _wait_status(store: Store, sid: str, want: set[str], timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = store.get_session(sid)
        if s and s["status"] in want:
            return s["status"]
        time.sleep(0.05)
    raise AssertionError(f"timeout waiting {want}, last={store.get_session(sid)['status']}")


def _pending_approval(store: Store, sid: str, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for m in store.get_messages(sid):
            if m["role"] == "approval":
                a = store.get_approval(m["content"]["approval_id"])
                if a and a["status"] == "pending":
                    return a["id"]
        time.sleep(0.05)
    raise AssertionError("no pending approval")


def _decide(engine: LoopEngine, store: Store, sid: str, aid: str, approved: bool, note: str = ""):
    """模拟 main.py 的批准端点：决定写库 + 唤醒。"""
    store.decide_approval(aid, approved, note)
    engine.notify_approval(sid)


def _make_engine(tmp_path, monkeypatch, script: list[str]) -> tuple[LoopEngine, Store]:
    monkeypatch.setattr(FakeAgent, "script", script)
    monkeypatch.setattr(loop_mod, "SessionAgent", FakeAgent)
    settings = Settings(copilot_db_path=tmp_path / "t.db")
    store = Store(settings.db_path)
    engine = LoopEngine(
        settings=settings,
        store=store,
        task_context_builder=lambda kind, payload: "CTX",
    )
    return engine, store


def test_brief_approved_then_done(tmp_path, monkeypatch):
    engine, store = _make_engine(tmp_path, monkeypatch, [BRIEF_TEXT, DONE_REPORT])
    sid = store.create_session("page_assist", {"job_id": "j", "page_no": 3})
    engine.start(sid)

    aid = _pending_approval(store, sid)
    a = store.get_approval(aid)
    assert a["action_key"] == "brief_confirm"
    _decide(engine, store, sid, aid, True)

    assert _wait_status(store, sid, {"done", "failed"}) == "done"
    roles = [m["role"] for m in store.get_messages(sid)]
    assert "assistant" in roles and "approval" in roles and "result" in roles
    # 最终报告落在 session.plan
    assert store.get_session(sid)["plan"]["status"] == "done"
    acts = [x["action"] for x in store.get_audit(sid)]
    assert "session_done" in acts


def test_brief_rejected_then_revised(tmp_path, monkeypatch):
    engine, store = _make_engine(
        tmp_path, monkeypatch, [BRIEF_TEXT, "修订后的简报：改用方案草稿通道。", DONE_REPORT]
    )
    sid = store.create_session("page_assist", {"job_id": "j", "page_no": 3})
    engine.start(sid)

    aid1 = _pending_approval(store, sid)
    _decide(engine, store, sid, aid1, False, note="别改文件，先试便宜通道")

    # 修订后再次出确认卡
    deadline = time.monotonic() + 10
    aid2 = ""
    while time.monotonic() < deadline:
        pend = [
            m["content"]["approval_id"]
            for m in store.get_messages(sid)
            if m["role"] == "approval" and m["content"]["approval_id"] != aid1
        ]
        if pend:
            aid2 = pend[0]
            break
        time.sleep(0.05)
    assert aid2, "修订后未再出确认卡"
    _decide(engine, store, sid, aid2, True)
    assert _wait_status(store, sid, {"done", "failed"}) == "done"
    # 打回意见作为用户消息回显 + 带给 Agent
    texts = [m["content"].get("text", "") for m in store.get_messages(sid) if m["role"] == "user"]
    assert any("便宜通道" in t for t in texts)


def test_blocked_then_user_reply_continues(tmp_path, monkeypatch):
    engine, store = _make_engine(tmp_path, monkeypatch, [BRIEF_TEXT, BLOCKED_REPORT, DONE_REPORT])
    sid = store.create_session("import_advice", {})
    engine.start(sid)

    aid = _pending_approval(store, sid)
    _decide(engine, store, sid, aid, True)

    assert _wait_status(store, sid, {"awaiting_user"}) == "awaiting_user"
    engine.post_user_message(sid, "用方案 A")
    assert _wait_status(store, sid, {"done", "failed"}) == "done"
    # blocked 报告也以 result 卡呈现
    results = [m["content"] for m in store.get_messages(sid) if m["role"] == "result"]
    assert any(r.get("blocked") for r in results)
    assert results[-1]["report"]["status"] == "done"


def test_missing_report_nudged_then_failed(tmp_path, monkeypatch):
    """Agent 一直不交最终报告：追问 max_nudges 次后判 failed。"""
    engine, store = _make_engine(tmp_path, monkeypatch, [BRIEF_TEXT, "呃我还在想", "还在想", "仍然没有"])
    sid = store.create_session("page_assist", {"job_id": "j", "page_no": 3})
    engine.start(sid)
    aid = _pending_approval(store, sid)
    _decide(engine, store, sid, aid, True)
    assert _wait_status(store, sid, {"done", "failed"}) == "failed"
    assert store.get_session(sid)["plan"]["status"] == "failed"


def test_fs_violation_rolls_back_and_fails(tmp_path, monkeypatch):
    """终检发现未经批准的文件改动 → 回滚 + failed。"""

    class FakeSnap:
        def __init__(self, *a, **k):
            pass

        def backup_guarded(self):
            pass

        def changed_since(self):
            return ["learning-buddy-api/configs/editions/x/config.json"], []

        def rollback_all_violations(self, files):
            return list(files), []

        def cleanup(self):
            pass

    monkeypatch.setattr(loop_mod, "WorktreeSnapshot", FakeSnap)
    engine, store = _make_engine(tmp_path, monkeypatch, [BRIEF_TEXT, DONE_REPORT])
    sid = store.create_session("page_assist", {"job_id": "j", "page_no": 3})
    engine.start(sid)
    aid = _pending_approval(store, sid)
    _decide(engine, store, sid, aid, True)
    assert _wait_status(store, sid, {"done", "failed"}) == "failed"
    sys_texts = [
        m["content"].get("text", "") for m in store.get_messages(sid) if m["role"] == "system"
    ]
    assert any("越权" in t and "回滚" in t for t in sys_texts)


def test_brief_superseded_by_user_message(tmp_path, monkeypatch):
    """确认卡等待期间用户明确要求改方案 → 打回并重拟简报。"""
    engine, store = _make_engine(
        tmp_path, monkeypatch, [BRIEF_TEXT, "修订后的简报：先导入第二册再看图。", DONE_REPORT]
    )
    sid = store.create_session("import_advice", {})
    engine.start(sid)

    aid1 = _pending_approval(store, sid)
    # 明确 revise 口令（非澄清问句）
    msg = "拒绝，改成先导入 PDF 第二册再自己看图 review"
    store.add_message(sid, "user", {"text": msg})
    engine.post_user_message(sid, msg)

    # 原确认卡被作废（superseded），修订后出新卡
    deadline = time.monotonic() + 10
    aid2 = ""
    while time.monotonic() < deadline:
        pend = [
            m["content"]["approval_id"]
            for m in store.get_messages(sid)
            if m["role"] == "approval" and m["content"]["approval_id"] != aid1
        ]
        if pend:
            aid2 = pend[0]
            break
        time.sleep(0.05)
    assert aid2, "用户消息后未重拟简报"
    a1 = store.get_approval(aid1)
    assert a1["status"] == "rejected"
    resolved = [
        m["content"] for m in store.get_messages(sid) if m["role"] == "approval_resolved"
    ]
    assert any(r["approval_id"] == aid1 and r["status"] == "superseded" for r in resolved)

    _decide(engine, store, sid, aid2, True)
    assert _wait_status(store, sid, {"done", "failed"}) == "done"


def test_brief_clarify_keeps_approval_card(tmp_path, monkeypatch):
    """确认卡上追问「接下来做什么」→ 澄清回答，确认卡仍 pending（Cursor 式对话通道）。"""
    engine, store = _make_engine(
        tmp_path,
        monkeypatch,
        [BRIEF_TEXT, "接下来会：render_preview → 导入 50 页 → 页级分流。", DONE_REPORT],
    )
    sid = store.create_session("import_advice", {})
    engine.start(sid)

    aid = _pending_approval(store, sid)
    q = "请告诉我你接下来要执行哪些动作？"
    store.add_message(sid, "user", {"text": q})
    engine.post_user_message(sid, q)

    # 等澄清回答落库，确认卡仍 pending
    deadline = time.monotonic() + 10
    answered = False
    while time.monotonic() < deadline:
        texts = [
            m["content"].get("text", "")
            for m in store.get_messages(sid)
            if m["role"] == "assistant"
        ]
        if any("render_preview" in t or "导入" in t for t in texts[1:]):
            answered = True
            break
        time.sleep(0.05)
    assert answered, "澄清问题未得到回答"
    a = store.get_approval(aid)
    assert a["status"] == "pending", "澄清问答不应作废确认卡"
    assert store.get_session(sid)["status"] == "awaiting_approval"
    # 无 superseded
    resolved = [
        m["content"] for m in store.get_messages(sid) if m["role"] == "approval_resolved"
    ]
    assert not any(r["approval_id"] == aid for r in resolved)

    _decide(engine, store, sid, aid, True)
    assert _wait_status(store, sid, {"done", "failed"}) == "done"


def test_queued_message_during_execution_continues(tmp_path, monkeypatch):
    """执行期间排队的用户消息：Agent 报 done 后先消化排队消息再收尾。"""

    class SlowAgent(FakeAgent):
        def ask(self, prompt, on_text=None, *, force: bool = False):
            self.asked.append(prompt)
            if len(self.asked) == 2:
                time.sleep(1.0)  # 执行轮放慢，让用户消息有机会排队
            i = min(len(self.asked) - 1, len(self.script) - 1)
            text = self.script[i]
            if on_text:
                on_text(text)
            return text

    monkeypatch.setattr(loop_mod, "SessionAgent", SlowAgent)
    monkeypatch.setattr(
        SlowAgent, "script", [BRIEF_TEXT, DONE_REPORT, DONE_REPORT]
    )
    settings = Settings(copilot_db_path=tmp_path / "t.db")
    store = Store(settings.db_path)
    engine = LoopEngine(settings=settings, store=store, task_context_builder=lambda k, p: "CTX")
    sid = store.create_session("import_advice", {})
    engine.start(sid)
    aid = _pending_approval(store, sid)
    _decide(engine, store, sid, aid, True)
    time.sleep(0.3)  # 进入执行轮
    engine.post_user_message(sid, "顺便把第 5 页也看一下")
    assert _wait_status(store, sid, {"done", "failed"}) == "done"
    # 排队消息作为新一轮 instruction 带给 Agent（第 3 次 ask）
    results = [m["content"] for m in store.get_messages(sid) if m["role"] == "result"]
    assert any(r.get("interim") for r in results)  # 中间报告
    assert results[-1]["report"]["status"] == "done"


def test_followup_after_done_resumes(tmp_path, monkeypatch):
    """终态会话继续对话：resume_session 复用 agent_id 重启执行阶段。"""
    engine, store = _make_engine(tmp_path, monkeypatch, [BRIEF_TEXT, DONE_REPORT])
    sid = store.create_session("import_advice", {})
    engine.start(sid)
    aid = _pending_approval(store, sid)
    _decide(engine, store, sid, aid, True)
    assert _wait_status(store, sid, {"done"}) == "done"
    # status 落库后 finally 才弹出 runtime，稍等线程收尾
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and engine.is_running(sid):
        time.sleep(0.02)
    assert not engine.is_running(sid)

    monkeypatch.setattr(FakeAgent, "script", [DONE_REPORT])  # 续聊直接进执行轮
    engine.resume_session(sid, "再帮我检查一下目录页")
    # 先等续聊消息落库（区分于 resume 前的旧 done 状态），再等本轮跑完
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        texts = [
            m["content"].get("text", "") for m in store.get_messages(sid) if m["role"] == "user"
        ]
        if any("目录页" in t for t in texts):
            break
        time.sleep(0.05)
    assert any("目录页" in t for t in texts), "续聊用户消息未落库"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and engine.is_running(sid):
        time.sleep(0.05)
    assert store.get_session(sid)["status"] == "done"
    acts = [x["action"] for x in store.get_audit(sid)]
    assert "agent_resumed" in acts


def test_cancel_during_brief(tmp_path, monkeypatch):
    engine, store = _make_engine(tmp_path, monkeypatch, [BRIEF_TEXT, DONE_REPORT])
    sid = store.create_session("page_assist", {"job_id": "j", "page_no": 3})
    engine.start(sid)
    _pending_approval(store, sid)
    engine.cancel(sid)
    assert _wait_status(store, sid, {"cancelled"}) == "cancelled"


def test_hang_reconnects_then_done(tmp_path, monkeypatch):
    """SDK 流挂死自愈：首轮 ask 无响应 → 看门狗中止 → 自动重连同一 Agent 重试 → 正常完成。"""
    import dataclasses

    class HangThenOkAgent(FakeAgent):
        instances = 0

        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            type(self).instances += 1
            self._hang = type(self).instances == 1  # 第一个实例模拟挂死

        def ask(self, prompt, on_text=None, *, force: bool = False):
            if self._hang:
                time.sleep(60)  # 永不输出（守护线程，测试结束自动消亡）
                return ""
            return super().ask(prompt, on_text)

    engine, store = _make_engine(tmp_path, monkeypatch, [BRIEF_TEXT, DONE_REPORT])
    monkeypatch.setattr(loop_mod, "SessionAgent", HangThenOkAgent)
    real_load = loop_mod.load_permissions

    def small_budgets(env_dir):
        p = real_load(env_dir)
        return dataclasses.replace(
            p, budgets=dataclasses.replace(p.budgets, run_timeout_s=2, idle_timeout_s=2)
        )

    monkeypatch.setattr(loop_mod, "load_permissions", small_budgets)

    sid = store.create_session("page_assist", {"job_id": "j", "page_no": 3})
    engine.start(sid)

    # 挂死检测（join 切片 5s）+ 重连后出简报确认卡
    aid = _pending_approval(store, sid, timeout=30.0)
    _decide(engine, store, sid, aid, True)
    assert _wait_status(store, sid, {"done", "failed"}, timeout=30.0) == "done"
    # 重连审计与提示消息都应存在
    acts = [x["action"] for x in store.get_audit(sid)]
    assert "agent_reconnect" in acts
    texts = [m["content"].get("text", "") for m in store.get_messages(sid) if m["role"] == "system"]
    assert any("自动重连" in t for t in texts)
    assert HangThenOkAgent.instances >= 2


def test_hang_twice_fails_with_message(tmp_path, monkeypatch):
    """连续两轮无响应 → failed（不再吊死在 executing/planning）。"""
    import dataclasses

    class AlwaysHangAgent(FakeAgent):
        def ask(self, prompt, on_text=None, *, force: bool = False):
            time.sleep(60)
            return ""

    engine, store = _make_engine(tmp_path, monkeypatch, [BRIEF_TEXT])
    monkeypatch.setattr(loop_mod, "SessionAgent", AlwaysHangAgent)
    real_load = loop_mod.load_permissions

    def small_budgets(env_dir):
        p = real_load(env_dir)
        return dataclasses.replace(
            p, budgets=dataclasses.replace(p.budgets, run_timeout_s=2, idle_timeout_s=2)
        )

    monkeypatch.setattr(loop_mod, "load_permissions", small_budgets)

    sid = store.create_session("page_assist", {"job_id": "j", "page_no": 3})
    engine.start(sid)
    assert _wait_status(store, sid, {"failed"}, timeout=40.0) == "failed"
    texts = [m["content"].get("text", "") for m in store.get_messages(sid) if m["role"] == "system"]
    assert any("连续两轮中断" in t for t in texts)


def test_bridge_error_reconnects_then_done(tmp_path, monkeypatch):
    """桥接 RemoteProtocolError 应走自动重连，而不是立刻 failed。"""
    from app.agent_runner import AgentRunError

    class BridgeThenOkAgent(FakeAgent):
        instances = 0

        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            type(self).instances += 1
            self._boom = type(self).instances == 1

        def ask(self, prompt, on_text=None, *, force: bool = False):
            if self._boom:
                raise AgentRunError(
                    "Agent 运行失败: Bridge request failed: RemoteProtocolError: "
                    "peer closed connection without sending complete message body "
                    "(incomplete chunked read)"
                )
            return super().ask(prompt, on_text)

    engine, store = _make_engine(tmp_path, monkeypatch, [BRIEF_TEXT, DONE_REPORT])
    monkeypatch.setattr(loop_mod, "SessionAgent", BridgeThenOkAgent)

    sid = store.create_session("page_assist", {"job_id": "j", "page_no": 3})
    engine.start(sid)
    aid = _pending_approval(store, sid, timeout=15.0)
    _decide(engine, store, sid, aid, True)
    assert _wait_status(store, sid, {"done", "failed"}, timeout=15.0) == "done"
    acts = [x["action"] for x in store.get_audit(sid)]
    assert "agent_reconnect" in acts
    texts = [m["content"].get("text", "") for m in store.get_messages(sid) if m["role"] == "system"]
    assert any("自动重连" in t for t in texts)
    assert BridgeThenOkAgent.instances >= 2


def test_internal_error_force_new_agent_then_done(tmp_path, monkeypatch):
    """Cursor internal error：resume 旧 Agent 无效，须 force_new 再建一轮。"""
    from app.agent_runner import AgentRunError

    class InternalThenOkAgent(FakeAgent):
        instances = 0
        last_agent_id = None

        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            type(self).instances += 1
            type(self).last_agent_id = k.get("agent_id")
            self._boom = type(self).instances == 1

        def ask(self, prompt, on_text=None, *, force: bool = False):
            if self._boom:
                raise AgentRunError("Agent 运行失败: internal: internal error")
            return super().ask(prompt, on_text)

    engine, store = _make_engine(tmp_path, monkeypatch, [BRIEF_TEXT, DONE_REPORT])
    monkeypatch.setattr(loop_mod, "SessionAgent", InternalThenOkAgent)

    sid = store.create_session("page_assist", {"job_id": "j", "page_no": 3})
    # 模拟已有坏死 agent_id（Resume 会成功，但 Send 秒挂）
    store.update_session(sid, agent_id="agent-dead-internal")
    engine.start(sid)
    aid = _pending_approval(store, sid, timeout=15.0)
    _decide(engine, store, sid, aid, True)
    assert _wait_status(store, sid, {"done", "failed"}, timeout=15.0) == "done"
    audits = store.get_audit(sid)
    reconnect = [x for x in audits if x["action"] == "agent_reconnect"]
    assert reconnect and reconnect[0].get("detail", {}).get("force_new") is True
    texts = [m["content"].get("text", "") for m in store.get_messages(sid) if m["role"] == "system"]
    assert any("新建 Agent" in t for t in texts)
    assert InternalThenOkAgent.instances >= 2
