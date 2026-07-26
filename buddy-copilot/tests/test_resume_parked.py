"""刷新后 awaiting_user 停靠会话可 resume（不要求 agent_id）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.continue_intent import is_continue_request  # noqa: E402


def _can_resume(status: str, agent_id: str | None, text: str) -> bool:
    """与 main.post_message 恢复判定保持一致（抽测用）。"""
    terminal = status in ("done", "failed", "cancelled")
    parked = status == "awaiting_user"
    want_continue = is_continue_request(text)
    if parked:
        return True
    if terminal:
        return bool(agent_id) or want_continue
    return want_continue


def test_awaiting_user_any_message_resumes() -> None:
    assert _can_resume("awaiting_user", None, "按建议改")
    assert _can_resume("awaiting_user", None, "全文继续")
    assert _can_resume("awaiting_user", "", "随便说一句")


def test_failed_without_agent_needs_continue() -> None:
    assert not _can_resume("failed", None, "看一下第 3 页")
    assert _can_resume("failed", None, "继续")
    assert _can_resume("failed", "aid-1", "看一下第 3 页")


def test_executing_zombie_needs_continue() -> None:
    assert not _can_resume("executing", None, "你好")
    assert _can_resume("executing", None, "继续")
