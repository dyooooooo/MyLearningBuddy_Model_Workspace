"""导入任务 ↔ AI 会话 1:1 绑定与级联删除。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.store import Store  # noqa: E402


def test_bind_and_delete_by_import_job():
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "t.db")
        s1 = store.create_session(
            "import_advice",
            {"minio_key": "k", "import_job_id": "job-a"},
        )
        assert store.find_session_id_by_import_job("job-a") == s1
        s2 = store.create_session("import_advice", {"minio_key": "k2"})
        removed = store.bind_import_job(s2, "job-a")
        assert s1 in removed
        assert store.get_session(s1) is None
        assert store.find_session_id_by_import_job("job-a") == s2
        pl = store.get_session(s2)["payload"]
        assert pl["import_job_id"] == "job-a"

        gone = store.delete_sessions_by_import_job("job-a")
        assert s2 in gone
        assert store.get_session(s2) is None
        assert store.find_session_id_by_import_job("job-a") is None


def test_list_filter_by_job():
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "t.db")
        store.create_session("import_advice", {"import_job_id": "j1"})
        store.create_session("import_advice", {"import_job_id": "j2"})
        store.create_session("import_advice", {"job_id": "j1"})
        only = store.list_sessions(import_job_id="j1")
        assert len(only) == 2
        assert all(r["import_job_id"] == "j1" for r in only)


def test_payload_update_syncs_column():
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "t.db")
        sid = store.create_session("import_advice", {"minio_key": "x"})
        assert not store.find_session_id_by_import_job("later")
        store.update_session(sid, payload={"minio_key": "x", "import_job_id": "later"})
        assert store.find_session_id_by_import_job("later") == sid
