from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.kb.agent_notify import notify_import_completed, notify_import_converting


def test_notify_skipped_without_url(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "textbook_agent_url", "")
    with patch("app.kb.agent_notify.httpx.Client") as client_cls:
        notify_import_completed("b1")
    client_cls.assert_not_called()


def test_notify_posts_import_event(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "textbook_agent_url", "http://textbook-agent:8200")
    monkeypatch.setattr(settings, "textbook_agent_token", "tok")
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    inst = MagicMock()
    inst.post.return_value = resp
    inst.__enter__.return_value = inst
    inst.__exit__.return_value = False
    with patch("app.kb.agent_notify.httpx.Client", return_value=inst):
        notify_import_completed("b9", conversation_id="c-9")
    args, kwargs = inst.post.call_args
    assert args[0].endswith("/v1/tasks/emit")
    assert kwargs["json"]["event"] == "kb.import.completed"
    assert kwargs["json"]["book_id"] == "b9"
    assert kwargs["json"]["payload"]["conversation_id"] == "c-9"
    assert kwargs["headers"]["Authorization"] == "Bearer tok"


def test_notify_posts_converting_event(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "textbook_agent_url", "http://textbook-agent:8200")
    monkeypatch.setattr(settings, "textbook_agent_token", "tok")
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    inst = MagicMock()
    inst.post.return_value = resp
    inst.__enter__.return_value = inst
    inst.__exit__.return_value = False
    with patch("app.kb.agent_notify.httpx.Client", return_value=inst):
        notify_import_converting("b9", conversation_id="c-9")
    args, kwargs = inst.post.call_args
    assert args[0].endswith("/v1/tasks/emit")
    assert kwargs["json"]["event"] == "kb.import.converting"
    assert kwargs["json"]["payload"]["conversation_id"] == "c-9"
