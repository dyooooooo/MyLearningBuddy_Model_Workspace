from __future__ import annotations

import json
from pathlib import Path

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


def _write_book(root: Path) -> str:
    book_id = "v2mdbook1"
    book = root / book_id
    mineru = book / "mineru"
    mineru.mkdir(parents=True)
    (book / "pages").mkdir()
    (book / "mid").mkdir()
    (book / "pages" / "0001.md").write_text("旧稿第1页\n", encoding="utf-8")
    (book / "mid" / "0001.md").write_text("旧中间\n", encoding="utf-8")
    pages = [
        [
            {
                "type": "title",
                "content": {
                    "title_content": [
                        {"type": "text", "content": "氓  "},
                        {"type": "equation_inline", "content": "^{①}"},
                    ],
                    "level": 2,
                },
                "bbox": [0, 0, 10, 10],
            },
            {
                "type": "paragraph",
                "content": {"paragraph_content": [{"type": "text", "content": "氓之蚩蚩"}]},
                "bbox": [0, 20, 10, 30],
            },
            {
                "type": "page_header",
                "content": {"page_header_content": [{"type": "text", "content": "普通高中教科书"}]},
                "bbox": [0, 0, 5, 5],
            },
            {
                "type": "page_footnote",
                "content": {
                    "page_footnote_content": [{"type": "text", "content": "① 选自《诗经注析》。"}]
                },
                "bbox": [0, 80, 10, 90],
            },
            {
                "type": "page_footer",
                "content": {"page_footer_content": [{"type": "text", "content": "语文下册"}]},
                "bbox": [0, 95, 10, 99],
            },
            {
                "type": "page_number",
                "content": {"page_number_content": [{"type": "text", "content": "2"}]},
                "bbox": [0, 96, 2, 99],
            },
        ],
        [
            {
                "type": "equation_interline",
                "content": {
                    "math_content": "\\mathrm{H}_{2}+\\mathrm{Cl}_{2}\\xlongequal{\\text{点燃或光照}}2\\mathrm{HCl}",
                    "math_type": "latex",
                    "image_source": {"path": "images/eq.jpg"},
                },
            }
        ],
    ]
    (mineru / "demo_content_list_v2.json").write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    (mineru / "demo_content_list.json").write_text(
        json.dumps(
            [
                {"type": "text", "text": "氓之蚩蚩，抱布贸丝", "page_idx": 0, "bbox": [0, 20, 10, 30]},
                {"type": "page_footnote", "text": "① 选自《诗经注析》。", "page_idx": 0, "bbox": [0, 80, 10, 90]},
                {
                    "type": "equation",
                    "text": "\\mathrm{H}_{2}+\\mathrm{Cl}_{2}\\xlongequal{\\text{点燃或光照}}2\\mathrm{HCl}",
                    "page_idx": 1,
                    "bbox": [0, 10, 10, 20],
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (mineru / "full.md").write_text(
        "## 氓 $^{①}$\n\n氓之蚩蚩，抱布贸丝\n\n$$\n\\mathrm{H}_{2}+\\mathrm{Cl}_{2}\\xlongequal{\\text{点燃或光照}}2\\mathrm{HCl}\n$$\n",
        encoding="utf-8",
    )
    (book / "meta.json").write_text(
        json.dumps({"id": book_id, "label": "语文试页", "subject": "chinese", "status": "ready", "page_count": 2}),
        encoding="utf-8",
    )
    return book_id


def test_v2md_generate_keeps_old_pages(tmp_path, monkeypatch) -> None:
    settings, _surfaces = _home(tmp_path, monkeypatch)
    book_id = _write_book(settings.textbooks_dir)
    book = settings.textbooks_dir / book_id
    old_page = (book / "pages" / "0001.md").stat().st_mtime_ns
    old_mid = (book / "mid" / "0001.md").stat().st_mtime_ns
    from app.workspace_module import load

    out = load("v2md_generate").v2md_generate(book_id, root=str(settings.textbooks_dir))
    assert out["ok"] is True
    assert out["written"] == 2
    page = (book / "v2md" / "pages" / "0001.md").read_text(encoding="utf-8")
    assert "诗经注析" in page
    assert "[!header]" in page
    assert "[!footer]" in page
    assert "[!page]" in page
    assert "::: kb-" not in page
    chem = (book / "v2md" / "pages" / "0002.md").read_text(encoding="utf-8")
    assert "xlongequal" in chem
    assert "![公式]" not in chem
    assert (book / "pages" / "0001.md").stat().st_mtime_ns == old_page
    assert (book / "mid" / "0001.md").stat().st_mtime_ns == old_mid


def test_official_slice_has_verse_not_footnote(tmp_path, monkeypatch) -> None:
    settings, _surfaces = _home(tmp_path, monkeypatch)
    book_id = _write_book(settings.textbooks_dir)
    from app.tools_loader import load_tool_specs
    from app.workspace_module import load

    specs = {s["id"]: s for s in load_tool_specs()}
    assert "v2md_slice" not in specs
    assert "official_slice" not in specs
    assert "v2md_generate" not in specs
    out = load("official_slice").official_slice(book_id, 1, root=str(settings.textbooks_dir))
    assert out["ok"] is True
    assert "氓" in out["markdown"]
    assert "诗经注析" not in out["markdown"]


def test_library_markdown_src_v2md_and_old(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    settings, surfaces = _home(tmp_path, monkeypatch)
    surfaces.reload_all()
    book_id = _write_book(settings.textbooks_dir)
    from app import library

    library.write_page(book_id, 1, "旧稿第1页", b"jpeg")
    library.patch_book(book_id, status="ready", page_count=2)
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    old = client.get(f"/v1/library/{book_id}/pages/1/markdown", headers=headers)
    assert old.status_code == 200
    assert "旧稿第1页" in old.json()["markdown"]
    new = client.get(f"/v1/library/{book_id}/pages/1/markdown?src=v2md", headers=headers)
    assert new.status_code == 410
    official = client.get(f"/v1/library/{book_id}/pages/1/markdown?src=official", headers=headers)
    assert official.status_code == 410
    missing = client.get(f"/v1/library/{book_id}/pages/9/markdown?src=v2md", headers=headers)
    assert missing.status_code == 410
    still_old = client.get(f"/v1/library/{book_id}/pages/1/markdown", headers=headers)
    assert still_old.status_code == 200
    assert "旧稿第1页" in still_old.json()["markdown"]


def test_page_v2_goldens(tmp_path, monkeypatch) -> None:
    _settings, surfaces = _home(tmp_path, monkeypatch)
    surfaces.reload_all()
    report = surfaces.test_surface("page-v2")
    assert report["ok"] is True
    official = surfaces.test_surface("page-official")
    assert official["ok"] is True
    ir_slot = surfaces.test_surface("page-ir")
    assert ir_slot["ok"] is True
    compare = surfaces.test_surface("compare")
    assert compare["ok"] is True
