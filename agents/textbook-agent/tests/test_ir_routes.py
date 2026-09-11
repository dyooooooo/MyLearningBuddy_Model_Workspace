from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.workspace import ensure_workspace

_HEADERS = {"Authorization": "Bearer test-token"}


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app import surfaces

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    surfaces.reset_for_tests()
    ensure_workspace()
    return settings


def _mk_book(home: Path, book_id: str = "irroute") -> None:
    book = home / "textbooks" / book_id
    mineru = book / "mineru"
    mineru.mkdir(parents=True)
    page = {
        "page_idx": 0,
        "page_size": [600, 800],
        "para_blocks": [
            {"type": "text", "index": 0, "bbox": [60, 100, 540, 140],
             "lines": [{"spans": [{"type": "text", "content": "注解前的原文", "score": 0.9}]}]},
        ],
        "discarded_blocks": [
            {"type": "page_footnote", "index": 9,
             "lines": [{"spans": [{"type": "text", "content": "① 页注"}]}]},
        ],
    }
    (mineru / "layout.json").write_text(
        json.dumps({"pdf_info": [page], "_version_name": "9.9.9-test"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (mineru / "demo_model.json").write_text("[]", encoding="utf-8")
    (mineru / "demo_content_list.json").write_text("[]", encoding="utf-8")
    (book / "meta.json").write_text(
        json.dumps({"id": book_id, "status": "ready", "page_count": 1}), encoding="utf-8"
    )


def test_ir_routes_end_to_end(tmp_path, monkeypatch) -> None:
    settings = _home(tmp_path, monkeypatch)
    _mk_book(Path(settings.agent_home))
    client = TestClient(app)

    toc = client.get("/v1/ir/irroute/toc", headers=_HEADERS)
    assert toc.status_code == 200
    assert toc.json()["entries"] == []

    blocks = client.get("/v1/ir/irroute/pages/1/blocks", headers=_HEADERS)
    assert blocks.status_code == 200
    body = blocks.json()
    assert body["page"]["blocks"][0]["text"] == "注解前的原文"
    assert body["page"]["chrome"]["footnotes"][0]["text"] == "① 页注"
    blk_id = body["page"]["blocks"][0]["id"]

    md = client.get("/v1/ir/irroute/pages/1/markdown", headers=_HEADERS)
    assert md.status_code == 200
    assert "注解前的原文" in md.json()["markdown"]
    assert "① 页注" in md.json()["markdown"]

    notes_dir = Path(settings.agent_home) / "textbooks" / "irroute" / "ir" / "notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "0001.yaml").write_text(
        f"- id: n1\n  block: {blk_id}\n  op: replace\n  old: 原文\n  new: 订正\n",
        encoding="utf-8",
    )
    md2 = client.get("/v1/ir/irroute/pages/1/markdown", headers=_HEADERS)
    assert "注解前的订正" in md2.json()["markdown"]
    assert md2.json()["notes"] == 1

    missing = client.get("/v1/ir/nope/toc", headers=_HEADERS)
    assert missing.status_code == 404
