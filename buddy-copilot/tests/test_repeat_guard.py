"""空转熔断：重复调用与「写盘不生效」必须被编排器拦下。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.mcp_server as m  # noqa: E402
from app.repeat_guard import RepeatGuard  # noqa: E402
from app.store import Store  # noqa: E402
from app.tools.registry import ToolSpec  # noqa: E402


def test_third_identical_call_blocked():
    guard = RepeatGuard()
    args = {"job_id": "j1", "page_no": 3}
    assert guard.check("corpus.page_html", args) is None
    assert guard.check("corpus.page_html", args) is None
    blocked = guard.check("corpus.page_html", args)
    assert blocked and "重复" in blocked


def test_different_args_not_blocked():
    guard = RepeatGuard()
    for page in (1, 2, 3, 4, 5):
        assert guard.check("corpus.page_html", {"job_id": "j1", "page_no": page}) is None


def test_two_no_effect_writes_block_the_third():
    guard = RepeatGuard()
    for attempt in ("a", "b"):
        args = {"job_id": "j1", "block_id": "b1", "patch_json": attempt}
        assert guard.check("corpus.patch_block", args) is None
        guard.observe("corpus.patch_block", args, {"ok": True, "html_changed": False})

    blocked = guard.check("corpus.patch_block", {"job_id": "j1", "block_id": "b1", "patch_json": "c"})
    assert blocked and "没有变化" in blocked


def test_effective_write_resets_no_effect_streak():
    guard = RepeatGuard()
    args = {"job_id": "j1", "block_id": "b1", "patch_json": "a"}
    guard.check("corpus.patch_block", args)
    guard.observe("corpus.patch_block", args, {"html_changed": False})
    args2 = {"job_id": "j1", "block_id": "b1", "patch_json": "b"}
    guard.check("corpus.patch_block", args2)
    guard.observe("corpus.patch_block", args2, {"html_changed": True})

    assert guard.check("corpus.patch_block", {"job_id": "j1", "block_id": "b1", "patch_json": "c"}) is None


def test_no_effect_streak_is_per_target():
    guard = RepeatGuard()
    for block in ("b1", "b2"):
        args = {"job_id": "j1", "block_id": block, "patch_json": "x"}
        guard.check("corpus.patch_block", args)
        guard.observe("corpus.patch_block", args, {"html_changed": False})

    # 每块各 1 次无效，都还没到上限
    assert guard.check("corpus.patch_block", {"job_id": "j1", "block_id": "b1", "patch_json": "y"}) is None


def test_read_tools_ignore_html_changed():
    guard = RepeatGuard()
    args = {"job_id": "j1", "page_no": 1}
    guard.observe("corpus.page_html", args, {"html": ""})
    assert guard.check("corpus.page_html", args) is None


def test_wrap_blocks_repeated_ineffective_patch(tmp_path, monkeypatch):
    """经中间件端到端：连续无效写盘后第三次被拦，并落一条 skipped step。"""
    store = Store(tmp_path / "guard.db")
    sid = store.create_session("import_advice", {})
    monkeypatch.setattr(m, "_store", store)
    monkeypatch.setattr(m, "_SESSION_ID", sid)
    monkeypatch.setattr(m, "_local_calls", {"n": 0})
    monkeypatch.setattr(m, "_guard", RepeatGuard())

    calls: list[str] = []

    def fn(job_id: str, block_id: str, patch_json: str = "{}") -> dict:
        calls.append(patch_json)
        return {"ok": True, "html_changed": False}

    wrapped = m._wrap(
        ToolSpec(name="corpus.patch_block", title="单块改 IR", fn=fn), "run", False, "low", ""
    )
    assert json.loads(wrapped(job_id="j1", block_id="b1", patch_json="{\"text\": \"a\"}"))["ok"]
    assert json.loads(wrapped(job_id="j1", block_id="b1", patch_json="{\"text\": \"b\"}"))["ok"]
    out = json.loads(wrapped(job_id="j1", block_id="b1", patch_json="{\"text\": \"c\"}"))

    assert "没有变化" in out["error"]
    assert calls == ['{"text": "a"}', '{"text": "b"}']  # 第三次没进 handler
    skipped = [
        x["content"]
        for x in store.get_messages(sid)
        if x["role"] == "step" and x["content"].get("state") == "skipped"
    ]
    assert skipped and "没有变化" in skipped[0]["note"]
