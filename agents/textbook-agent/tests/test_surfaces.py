from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.paths import PathJailError
from app.workspace import ensure_workspace


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app import surfaces

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    surfaces.reset_for_tests()
    ensure_workspace()
    return settings, surfaces


def test_page_body_goldens_and_hash_title(tmp_path, monkeypatch) -> None:
    _settings, surfaces = _home(tmp_path, monkeypatch)
    surfaces.reload_all()
    raw = "目录\n\n第1章 集合与逻辑\n1.1 集合初步 2\n"
    hashed = "# 目录\n\n第1章 集合与逻辑\n1.1 集合初步 2\n"
    for md in (raw, hashed):
        out = surfaces.prepare_page(md, print_to_pdf={"2": 8}, book_id="demo")
        assert 'class="kb-page-toc-heading">目录</div>' in out
        assert "目录 </p>" not in out
        assert "</p>" not in out or "kb-page-toc-heading" in out
    report = surfaces.test_surface("page-body")
    assert report["ok"] is True


def test_broken_prepare_keeps_last_good(tmp_path, monkeypatch) -> None:
    _settings, surfaces = _home(tmp_path, monkeypatch)
    surfaces.reload_all()
    before = surfaces.prepare_page("目录\n\n第1章 集合\n1.1 集合初步 2\n", print_to_pdf={"2": 8})
    assert "kb-page-toc-heading" in before
    surfaces.write_surface("page-body", "prepare.mjs", "export function prepare() { throw new Error('boom'); }\n")
    report = surfaces.test_surface("page-body")
    assert report["ok"] is False
    reloaded = surfaces.reload_surfaces()
    page = next(s for s in reloaded["slots"] if s["slot"] == "page-body")
    assert page["source"] in {"last-good", "seed"}
    after = surfaces.prepare_page("目录\n\n第1章 集合\n1.1 集合初步 2\n", print_to_pdf={"2": 8})
    assert "kb-page-toc-heading" in after


def test_write_surface_stays_in_slot(tmp_path, monkeypatch) -> None:
    _settings, surfaces = _home(tmp_path, monkeypatch)
    try:
        surfaces.write_surface("page-body", "../toc-tree/paint.mjs", "nope")
        raise AssertionError("expected jail")
    except PathJailError:
        pass
    try:
        surfaces.write_surface("page-body", "/etc/passwd", "nope")
        raise AssertionError("expected jail")
    except PathJailError:
        pass
    try:
        surfaces.write_surface("dock", "theme.css", "x")
        raise AssertionError("expected unknown slot")
    except ValueError:
        pass


def test_surface_http_and_page_prepared(tmp_path, monkeypatch) -> None:
    from app import library

    _settings, surfaces = _home(tmp_path, monkeypatch)
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    listed = client.get("/v1/surfaces", headers=headers)
    assert listed.status_code == 200
    names = {row["slot"] for row in listed.json()["slots"]}
    assert names == {"page-body", "toc-tree", "compare"}
    css = client.get("/v1/surfaces/page-body/theme.css", headers=headers)
    assert css.status_code == 200
    assert "kb-page-toc-heading" in css.text
    painted = client.post(
        "/v1/surfaces/toc-tree/paint",
        headers=headers,
        json={
            "payload": {
                "entries": [{"label": "第1章", "print_page": 1, "is_chapter": True, "level": 0, "pdf_page": 7}],
                "printToPdf": {"1": 7},
            }
        },
    )
    assert painted.status_code == 200
    assert "data-goto-page=\"7\"" in painted.json()["html"]
    r = client.post(
        "/v1/library",
        headers=headers,
        files={"file": ("book.pdf", b"%PDF-1.4 demo", "application/pdf")},
        data={"label": "册", "textbook_hint": "沪教", "subject": "math"},
    )
    book_id = r.json()["id"]
    library.write_page(book_id, 1, "目录\n\n第1章 集合\n1.1 集合初步 2\n", b"jpeg1")
    library.patch_book(book_id, status="ready", page_count=1)
    md = client.get(f"/v1/library/{book_id}/pages/1/markdown", headers=headers)
    assert md.status_code == 200
    body = md.json()
    assert "目录" in body["markdown"]
    assert "kb-page-toc-heading" in body["prepared"]


def test_sanitize_css_strips_import() -> None:
    from app.surfaces import sanitize_css

    dirty = "@import url('https://evil.test/x.css'); body{color:red} url(https://x) </style><script>"
    clean = sanitize_css(dirty)
    assert "@import" not in clean.lower()
    assert "https://evil" not in clean
    assert "</style" not in clean.lower() or "&lt;/style" in clean


def test_theme_css_falls_back_if_folder_missing_file(tmp_path, monkeypatch) -> None:
    _settings, surfaces = _home(tmp_path, monkeypatch)
    surfaces.ensure_surfaces()
    empty = tmp_path / "no-css"
    empty.mkdir()
    css = surfaces._read_css(empty, "page-body")
    assert "kb-page-toc" in css
    assert "kb-table-wrap" in css


def test_existing_mid_is_not_retagged(tmp_path, monkeypatch) -> None:
    _settings, surfaces = _home(tmp_path, monkeypatch)
    surfaces.reload_all()
    mid = "::: kb-page-toc\n目录\n\n第1章 集合\n1.1 集合初步 2\n:::\n"
    first = surfaces.prepare_page(mid, print_to_pdf={"2": 8})
    surfaces.write_surface(
        "page-body",
        "tag.mjs",
        "export function tag(md) { return '::: kb-center\\nCHANGED\\n:::\\n' + (md || ''); }\n",
    )
    again = surfaces.prepare_page(mid, print_to_pdf={"2": 8})
    assert first == again
    assert "kb-page-toc-heading" in again
    assert "CHANGED" not in again


def test_prepare_table_is_single_wrap(tmp_path, monkeypatch) -> None:
    _settings, surfaces = _home(tmp_path, monkeypatch)
    surfaces.reload_all()
    raw = "表 1-1 符号\n\n<table><tr><td>数集</td><td>符号</td></tr><tr><td>自然数集</td><td>N</td></tr></table>\n"
    already = (
        '<div class="kb-table-wrap"><table><caption>表 1-1</caption>'
        "<tr><th>数集</th><th>符号</th></tr><tr><td>自然数集</td><td>N</td></tr></table></div>\n"
    )
    for md in (raw, already):
        out = surfaces.prepare_page(md, book_id="demo")
        assert out.count("kb-table-wrap") == 1
        assert "$$" not in out
        assert "<table" in out
