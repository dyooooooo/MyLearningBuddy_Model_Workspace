"""已有导入任务：可建空绑定会话（auto_start=false），不触发编排。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas import CreateSessionRequest  # noqa: E402


def test_create_session_request_auto_start_default():
    req = CreateSessionRequest(kind="import_advice", payload={"import_job_id": "j1"})
    assert req.auto_start is True
    req2 = CreateSessionRequest(
        kind="import_advice",
        payload={"import_job_id": "j1"},
        auto_start=False,
    )
    assert req2.auto_start is False


def test_idle_bind_skips_engine_start(tmp_path, monkeypatch):
    from app import main as main_mod
    from app.store import Store

    st = Store(tmp_path / "idle.db")
    eng = MagicMock()
    monkeypatch.setattr(main_mod, "store", st)
    monkeypatch.setattr(main_mod, "engine", eng)
    monkeypatch.setattr(main_mod.settings, "cursor_api_key", "test-key")

    req = CreateSessionRequest(
        kind="import_advice",
        payload={
            "import_job_id": "job-idle-1",
            "job_id": "job-idle-1",
            "attach_existing_job": True,
            "fullbook_autofix_done": True,
            "edition_id": "hujiao_math_hs_v1",
            "minio_key": "corpus/imports/x.pdf",
            "page_count": 10,
        },
        auto_start=False,
    )
    out = main_mod.create_session(req)
    assert out["session_id"]
    assert out.get("auto_start") is False
    eng.start.assert_not_called()
    s = st.get_session(out["session_id"])
    assert s is not None
    assert s["status"] == "awaiting_user"
    assert s["import_job_id"] == "job-idle-1"
    msgs = st.get_messages(out["session_id"], after_seq=0)
    assert any(m["role"] == "system" for m in msgs)


def test_should_not_fullbook_when_attach_existing(tmp_path):
    from app.config import get_settings
    from app.loop import LoopEngine
    from app.store import Store

    st = Store(tmp_path / "seg.db")
    sid = st.create_session(
        "import_advice",
        {
            "minio_key": "k",
            "edition_id": "e",
            "attach_existing_job": True,
            "fullbook_autofix_done": True,
        },
    )
    eng = LoopEngine(
        settings=get_settings(),
        store=st,
        task_context_builder=lambda *_a, **_k: "",
    )
    assert eng._should_mineru_segment(sid) is False
