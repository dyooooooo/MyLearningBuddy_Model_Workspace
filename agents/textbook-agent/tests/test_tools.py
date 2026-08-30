from __future__ import annotations

from app import jobs
from app.context import bind_live_sdk_job, current_job_id
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
    assert "build_toc" in specs
    assert "toc_extract" in specs
    assert "clear_traces" in specs
    assert "refine_page" in specs
    assert "start_layout_refine" not in specs
    assert "layout_refine_status" not in specs
    assert "create_task" in specs
    assert "update_task" in specs
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


def test_execute_inherits_live_job(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings

    settings.agent_home = str(tmp_path / "home")
    jobs.reset_for_tests()
    ensure_workspace()
    (settings.textbooks_dir / "math").mkdir(parents=True, exist_ok=True)
    (settings.textbooks_dir / "math" / "001.md").write_text("正整数\n", encoding="utf-8")
    specs = {s["id"]: s for s in load_tool_specs()}
    row = jobs.create_job("chat", {"message": "搜"})
    bind_live_sdk_job(row["job_id"])
    try:
        assert current_job_id.get() == ""
        hits = specs["search_md"]["execute"]({"query": "正整数"})
    finally:
        bind_live_sdk_job("")
    assert hits
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


def test_refine_page_sibling_import(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app.config_store import write_tools

    settings.agent_home = str(tmp_path / "home")
    ensure_workspace()
    tools = settings.tools_dir
    (tools / "dep_helper.py").write_text(
        "def mark(text: str) -> str:\n    return text.replace('X', 'Y')\n",
        encoding="utf-8",
    )
    (tools / "needs_dep.py").write_text(
        "from dep_helper import mark\n"
        "def needs_dep(text: str) -> dict:\n"
        "    return {'out': mark(text)}\n",
        encoding="utf-8",
    )
    write_tools(
        {
            "tools": [
                {"id": "needs_dep", "path": "needs_dep.py", "description": "sibling import"},
            ]
        }
    )
    specs = {s["id"]: s for s in load_tool_specs()}
    assert "needs_dep" in specs
    assert specs["needs_dep"]["execute"]({"text": "X1"})["out"] == "Y1"


def test_broken_tool_does_not_kill_others(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app.config_store import write_tools

    settings.agent_home = str(tmp_path / "home")
    ensure_workspace()
    (settings.tools_dir / "broken.py").write_text(
        "from definitely_missing_mod import nope\n\ndef broken() -> dict:\n    return {}\n",
        encoding="utf-8",
    )
    write_tools(
        {
            "tools": [
                {"id": "broken", "path": "broken.py", "description": "bad import"},
                {"id": "search_md", "path": "search_md.py", "description": "search"},
            ]
        }
    )
    specs = {s["id"]: s for s in load_tool_specs()}
    assert "broken" not in specs
    assert "search_md" in specs


def test_propose_import_meta_publishes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app import bus, library
    from app.config import settings

    settings.agent_home = str(tmp_path / "home")
    ensure_workspace()
    book = library.create_book(
        pdf_bytes=b"%PDF-1.4",
        filename="book.pdf",
        book_id="bmeta",
        label="旧名",
        status="draft",
    )
    events: list[dict] = []
    q = bus.subscribe()
    specs = {s["id"]: s for s in load_tool_specs()}
    assert "propose_import_meta" in specs
    from app import jobs
    from app.context import current_job_id

    job = jobs.create_job("chat", {"trigger": "kb.import.dropped"})
    token = current_job_id.set(job["job_id"])
    try:
        out = specs["propose_import_meta"]["execute"](
            {
                "book_id": book["id"],
                "label": "普通高中教科书·数学必修第一册",
                "textbook_hint": "数学必修第一册",
                "subject": "数学",
                "grade_band": "高中",
                "category": "考纲",
            }
        )
    finally:
        current_job_id.reset(token)
    assert out["subject"] == "math"
    assert out["grade_band"] == "hs"
    assert out["category"] == "syllabus"
    patched = library.get_book("bmeta")
    assert patched is not None
    assert patched["label"] == "普通高中教科书·数学必修第1册"
    assert patched["textbook_hint"] == "数学必修第1册"
    assert patched["category"] == "syllabus"
    while True:
        try:
            events.append(q.get_nowait())
        except Exception:
            break
    bus.unsubscribe(q)
    assert any(ev.get("type") == "import_meta" for ev in events)
    traced = jobs.get_job(job["job_id"]) or {}
    titles = [str(step.get("title") or "") for step in traced.get("trace") or []]
    assert "propose_import_meta" in titles
