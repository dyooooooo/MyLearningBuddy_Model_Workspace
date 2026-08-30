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


def test_library_category_create_patch_list(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app import library

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    ensure_workspace()
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    created = client.post(
        "/v1/library",
        headers=headers,
        files={"file": ("outline.pdf", b"%PDF-1.4 demo", "application/pdf")},
        data={"label": "课程标准", "subject": "math", "grade_band": "hs", "category": "考纲"},
    )
    assert created.status_code == 200
    book = created.json()
    assert book["category"] == "syllabus"
    book_id = book["id"]
    listed = client.get("/v1/library", headers=headers)
    hit = next(row for row in listed.json() if row["id"] == book_id)
    assert hit["category"] == "syllabus"
    patched = client.patch(
        f"/v1/library/{book_id}",
        headers=headers,
        json={"category": "作业"},
    )
    assert patched.status_code == 200
    assert patched.json()["category"] == "homework"
    raw = library.read_meta(book_id) or {}
    raw["category"] = "考纲"
    library.write_meta(book_id, raw)
    again = library.get_book(book_id)
    assert again is not None
    assert again["category"] == "syllabus"
    again = client.delete(f"/v1/library/{book_id}", headers=headers)
    assert again.status_code == 200
    from pathlib import Path

    assert not (Path(settings.agent_home) / "textbooks" / book_id).exists()


def test_leftover_folder_without_meta_is_not_listed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app import library

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    ensure_workspace()
    orphan = library.book_dir("orphan1")
    (orphan / "pages").mkdir(parents=True)
    (orphan / "pages" / "0001.md").write_text("旧页", encoding="utf-8")
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    listed = client.get("/v1/library", headers=headers)
    assert listed.status_code == 200
    assert all(row["id"] != "orphan1" for row in listed.json())


def test_draft_book_hidden_from_list(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app import library

    settings.agent_home = str(tmp_path / "home")
    ensure_workspace()
    book = library.create_book(
        pdf_bytes=b"%PDF-1.4 draft",
        filename="draft.pdf",
        book_id="draft1",
        label="待确认",
        status="draft",
    )
    assert book["status"] == "draft"
    assert library.get_book("draft1") is not None
    assert all(row["id"] != "draft1" for row in library.list_books())
    library.patch_book("draft1", status="uploaded", conversation_id="c1")
    listed = library.list_books()
    hit = next(row for row in listed if row["id"] == "draft1")
    assert hit["status"] == "uploaded"
    assert hit["conversation_id"] == "c1"


def test_library_create_draft_via_http(tmp_path, monkeypatch) -> None:
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
        data={"label": "待确认", "status": "draft"},
    )
    assert r.status_code == 200
    book = r.json()
    assert book["status"] == "draft"
    listed = client.get("/v1/library", headers=headers)
    assert all(row["id"] != book["id"] for row in listed.json())
    got = client.get(f"/v1/library/{book['id']}", headers=headers)
    assert got.status_code == 200
    assert got.json()["status"] == "draft"


def test_reimport_clears_pages_and_toc(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app import library

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    ensure_workspace()
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    created = client.post(
        "/v1/library",
        headers=headers,
        files={"file": ("book.pdf", b"%PDF-1.4 demo", "application/pdf")},
        data={"label": "必修第一册"},
    )
    book_id = created.json()["id"]
    library.write_page(book_id, 1, "旧页", b"jpeg")
    library.write_toc(
        book_id,
        {
            "entries": [{"label": "旧目录", "level": 1, "print_page": 1, "pdf_page": 1, "is_chapter": False}],
            "print_to_pdf": {},
            "toc_pages": [],
        },
    )
    redone = client.post(f"/v1/library/{book_id}/reimport", headers=headers)
    assert redone.status_code == 200
    assert library.read_toc(book_id)["entries"] == []
    assert library.get_page_markdown(book_id, 1) is None


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
