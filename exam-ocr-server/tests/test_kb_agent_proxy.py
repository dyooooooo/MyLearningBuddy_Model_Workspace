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


class _DeleteReq:
    method = "DELETE"
    headers = {"accept": "application/json"}
    query_params = {}

    async def body(self):
        return b""


def test_proxy_kb_delete_purges_buddy(monkeypatch):
    from app.config import settings
    from app.kb.routes import proxy_kb

    monkeypatch.setattr(settings, "textbook_agent_url", "http://textbook-agent:8200")
    monkeypatch.setattr(settings, "textbook_agent_token", "agent-tok")
    buddy = AsyncMock(return_value=MagicMock(status_code=200, content=b"{}", headers={}))
    agent = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            content=b'{"deleted":"b1"}',
            headers={"content-type": "application/json"},
            body=b'{"deleted":"b1"}',
        )
    )
    # proxy_agent returns a Response; reuse a real one via the existing helper path
    with (
        patch("app.kb.routes._buddy", buddy),
        patch("app.kb.routes.proxy_agent", agent),
    ):
        out = asyncio.run(proxy_kb("books/b1", _DeleteReq()))
    assert out.status_code == 200
    buddy.assert_awaited()
    assert buddy.await_args.args[0] == "DELETE"
    assert buddy.await_args.args[1] == "/api/v1/admin/kb/books/b1"


def test_import_pdf_reuses_draft_book(monkeypatch):
    from app.kb.routes import import_pdf

    monkeypatch.setattr("app.kb.routes.settings.textbook_agent_url", "http://textbook-agent:8200")
    monkeypatch.setattr("app.kb.routes.settings.textbook_agent_token", "agent-tok")
    patched = {"id": "draft1", "status": "uploaded", "label": "必修", "conversation_id": "c1"}
    convert = AsyncMock(return_value=MagicMock(status_code=200, content=b'{"queued":true}', headers={}))
    with (
        patch("app.kb.routes._agent_patch_book", AsyncMock(return_value=patched)) as patch_book,
        patch("app.kb.routes._buddy", convert),
        patch("app.kb.routes._agent_upload_pdf", AsyncMock()) as upload,
    ):
        out = asyncio.run(
            import_pdf(
                file=None,
                subject="math",
                grade_band="hs",
                category="syllabus",
                label="必修",
                textbook_hint="数学必修",
                book_id="draft1",
                conversation_id="c1",
            )
        )
    assert out["id"] == "draft1"
    assert out["conversation_id"] == "c1"
    upload.assert_not_awaited()
    patch_book.assert_awaited()
    convert.assert_awaited()
    assert convert.await_args.args[0] == "POST"
    assert convert.await_args.args[1] == "/api/v1/admin/kb/convert/draft1"


def test_identify_preview_posts_buddy(monkeypatch):
    from app.kb.routes import identify_preview

    monkeypatch.setattr("app.kb.routes.settings.buddy_api_url", "http://buddy-api:8100")
    monkeypatch.setattr("app.buddy_client.settings.buddy_admin_token", "adm")
    resp = MagicMock()
    resp.status_code = 200
    resp.text = ""
    resp.json.return_value = {"id": "d1", "subject": "math", "grade_band": "hs", "label": "必修"}
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("app.kb.routes.httpx.AsyncClient", return_value=client):
        out = asyncio.run(identify_preview("d1"))
    assert out["subject"] == "math"
    client.post.assert_awaited()
    assert client.post.await_args.args[0].endswith("/api/v1/admin/kb/identify/d1")
    assert client.post.await_args.kwargs["params"]["pages"] == 3
