import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.kb.routes import proxy_agent


class _Req:
    method = "GET"
    headers = {"accept": "application/json"}
    query_params = {}

    async def body(self):
        return b""


def test_proxy_agent_requires_url(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "textbook_agent_url", "")
    monkeypatch.setattr(settings, "textbook_agent_token", "tok")
    try:
        asyncio.run(proxy_agent("config", _Req()))
        raise AssertionError("expected 503")
    except HTTPException as exc:
        assert exc.status_code == 503


def test_proxy_agent_forwards_bearer(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "textbook_agent_url", "http://textbook-agent:8200")
    monkeypatch.setattr(settings, "textbook_agent_token", "agent-tok")
    resp = MagicMock()
    resp.content = b'{"ok":true}'
    resp.status_code = 200
    resp.headers = {"content-type": "application/json"}
    client = MagicMock()
    client.request = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("app.kb.routes.httpx.AsyncClient", return_value=client):
        out = asyncio.run(proxy_agent("config", _Req()))
    assert out.status_code == 200
    client.request.assert_awaited()
    kwargs = client.request.await_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer agent-tok"
