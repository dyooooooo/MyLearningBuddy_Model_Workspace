"""corpus.start_import 应向会话推送 import_event（离线 mock）。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.tools.registry import ToolContext, build_specs  # noqa: E402


def test_start_import_emits_started_and_done(tmp_path, monkeypatch):
    from app import store as store_mod

    db = tmp_path / "t.db"
    st = store_mod.Store(db)
    sid = st.create_session("import_advice", {"minio_key": "corpus/imports/x.pdf"})

    client = MagicMock()
    client.start_pdf_import.return_value = {"id": "job-abc", "status": "uploaded"}
    client.get_import_job.return_value = {
        "id": "job-abc",
        "status": "parsed",
        "meta": {"pages_ready": 6, "pipeline_phase": "parse"},
        "error": None,
    }

    settings = Settings(copilot_db_path=db)
    ctx = ToolContext(settings=settings, store=st, client=client, session_id=sid, kind="import_advice")
    specs = build_specs(ctx)
    fn = specs["corpus.start_import"].fn
    out = fn(
        minio_key="corpus/imports/x.pdf",
        edition_id="e1",
        page_limit=12,
        wait=True,
        wait_timeout=30,
    )
    assert out["job_id"] == "job-abc"
    assert out["status"] == "parsed"

    msgs = st.get_messages(sid)
    roles = [m["role"] for m in msgs]
    assert "import_event" in roles
    events = [m["content"].get("event") for m in msgs if m["role"] == "import_event"]
    assert "import_started" in events
    assert "import_done" in events
    systems = [
        m["content"].get("text", "")
        for m in msgs
        if m["role"] == "system" and isinstance(m.get("content"), dict)
    ]
    assert any("已导入完成" in t or "已发起预览版导入" in t for t in systems)
    sess = st.get_session(sid)
    assert sess["payload"].get("import_job_id") == "job-abc"
