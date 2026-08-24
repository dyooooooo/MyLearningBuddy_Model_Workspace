from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.workspace import ensure_workspace


def test_library_create_list_page(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    ensure_workspace()
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    r = client.post(
        "/v1/library",
        headers=headers,
        files={"file": ("book.pdf", b"%PDF-1.4 demo", "application/pdf")},
        data={"label": "必修第一册", "textbook_hint": "沪教数学", "subject": "math"},
    )
    assert r.status_code == 200
    book = r.json()
    assert book["label"] == "必修第一册"
    assert book["status"] == "uploaded"
    book_id = book["id"]
    listed = client.get("/v1/library", headers=headers)
    assert listed.status_code == 200
    assert any(row["id"] == book_id for row in listed.json())
    from app import library

    library.write_page(book_id, 1, "例3 集合", b"jpeg1")
    library.patch_book(book_id, status="ready", page_count=1)
    md = client.get(f"/v1/library/{book_id}/pages/1/markdown", headers=headers)
    assert md.status_code == 200
    assert "例3" in md.json()["markdown"]
    img = client.get(f"/v1/library/{book_id}/pages/1/image", headers=headers)
    assert img.status_code == 200
    assert img.content == b"jpeg1"
    gone = client.delete(f"/v1/library/{book_id}", headers=headers)
    assert gone.status_code == 200
    assert client.get(f"/v1/library/{book_id}", headers=headers).status_code == 404


def test_path_jail_rejects_outside(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app.paths import PathJailError, resolve_in_home
    from app.tools_loader import load_tool_specs

    settings.agent_home = str(tmp_path / "home")
    ensure_workspace()
    try:
        resolve_in_home("/etc/passwd")
        raise AssertionError("expected jail")
    except PathJailError:
        pass
    specs = {s["id"]: s for s in load_tool_specs()}
    try:
        specs["search_md"]["execute"]({"query": "x", "root": "/etc"})
        raise AssertionError("expected jail")
    except PathJailError:
        pass
