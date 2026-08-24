from __future__ import annotations

from app import jobs
from app.context import current_job_id
from app.tools_loader import load_tool_specs
from app.workspace import ensure_workspace


def test_load_seed_tools(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings

    settings.agent_home = str(tmp_path / "home")
    ensure_workspace()
    (settings.textbooks_dir / "math").mkdir(parents=True, exist_ok=True)
    md = settings.textbooks_dir / "math" / "001.md"
    md.write_text("1.1 集合初步 正整数 例3\n", encoding="utf-8")
    pdf = settings.textbooks_dir / "math" / "demo.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    specs = {s["id"]: s for s in load_tool_specs()}
    assert "search_md" in specs
    assert "open_pdf_page" in specs
    row = jobs.create_job("grade", {"request_id": "t"})
    token = current_job_id.set(row["job_id"])
    try:
        hits = specs["search_md"]["execute"]({"query": "正整数"})
    finally:
        current_job_id.reset(token)
    assert hits and "001.md" in hits[0]["path"]
    opened = specs["open_pdf_page"]["execute"]({"path": "demo.pdf", "page": 3})
    assert opened["exists"] is True
    assert opened["page"] == 3
    saved = jobs.get_job(row["job_id"])
    assert saved is not None
    assert any(s["kind"] == "search" for s in saved["trace"])


def test_write_tool_then_reload(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings

    settings.agent_home = str(tmp_path / "home")
    ensure_workspace()
    specs = {s["id"]: s for s in load_tool_specs()}
    assert "list_config" in specs
    assert "write_tool" in specs
    listed = specs["list_config"]["execute"]({})
    assert "search_md" in [t["id"] for t in listed["tools"]]
    specs["write_tool"]["execute"](
        {
            "tool_id": "count_ones",
            "description": "数 1 的个数",
            "path": "count_ones.py",
            "content": "def count_ones(text: str) -> dict:\n    return {\"n\": text.count(\"1\")}\n",
        }
    )
    specs["reload_config"]["execute"]({})
    again = {s["id"]: s for s in load_tool_specs()}
    assert "count_ones" in again
    assert again["count_ones"]["execute"]({"text": "a1b11"})["n"] == 3
