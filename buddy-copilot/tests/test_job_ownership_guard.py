"""导入任务归属校验 + 「停止」不被回写。

背景（真实事故）：导入「第二册」时会话 payload 带着旧的「第一册」job_id，
旧分段导入把第二册的页写进了第一册；随后点停止无效，
因为残余流程把 cancelled 又改回 awaiting_user。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.loop import LoopEngine, _pdf_identity  # noqa: E402
from app.store import Store  # noqa: E402

BOOK1 = "corpus/imports/cee9ee71/普通高中教科书·数学必修 第一册.pdf"
BOOK2 = "corpus/imports/staging/cfdb87c0/普通高中教科书·数学必修 第二册.pdf"


class FakeClient:
    """只实现 get_import_job；返回指定任务的 minio_key。"""

    def __init__(self, minio_key: str | None, raise_exc: bool = False) -> None:
        self.minio_key = minio_key
        self.raise_exc = raise_exc

    def get_import_job(self, job_id: str) -> dict:
        if self.raise_exc:
            raise RuntimeError("api down")
        return {"id": job_id, "minio_key": self.minio_key, "meta": {}}


def _engine(tmp_path) -> tuple[LoopEngine, Store]:
    settings = Settings(copilot_db_path=tmp_path / "t.db")
    store = Store(settings.db_path)
    engine = LoopEngine(
        settings=settings,
        store=store,
        task_context_builder=lambda kind, payload: "CTX",
    )
    return engine, store


def test_pdf_identity_ignores_bucket_prefix():
    assert _pdf_identity(BOOK1) == _pdf_identity(
        "corpus/imports/staging/other-hash/普通高中教科书·数学必修 第一册.pdf"
    )
    assert _pdf_identity(BOOK1) != _pdf_identity(BOOK2)
    assert _pdf_identity("", "第二册.pdf") == "第二册"
    assert _pdf_identity("", "") == ""


def test_foreign_job_binding_is_dropped(tmp_path):
    engine, store = _engine(tmp_path)
    sid = store.create_session(
        "import_advice",
        {"minio_key": BOOK2, "import_job_id": "job-book1", "segment_imported_through": 20},
    )
    payload = dict(store.get_session(sid)["payload"])
    engine._drop_foreign_job_binding(sid, payload, FakeClient(BOOK1))

    assert "import_job_id" not in payload
    assert "segment_imported_through" not in payload
    assert store.get_session(sid)["import_job_id"] == ""
    assert store.find_session_id_by_import_job("job-book1") is None
    texts = [m["content"].get("text", "") for m in store.get_messages(sid)]
    assert any("解除错误绑定" in t for t in texts)


def test_own_job_binding_is_kept(tmp_path):
    engine, store = _engine(tmp_path)
    sid = store.create_session(
        "import_advice",
        {"minio_key": BOOK2, "import_job_id": "job-book2", "segment_imported_through": 20},
    )
    payload = dict(store.get_session(sid)["payload"])
    # 任务键在正式目录、哈希不同，但同一本书 → 不解绑
    engine._drop_foreign_job_binding(
        sid, payload, FakeClient("corpus/imports/abc123/普通高中教科书·数学必修 第二册.pdf")
    )
    assert payload["import_job_id"] == "job-book2"
    assert payload["segment_imported_through"] == 20


def test_unreachable_api_keeps_binding(tmp_path):
    engine, store = _engine(tmp_path)
    sid = store.create_session(
        "import_advice", {"minio_key": BOOK2, "import_job_id": "job-x"}
    )
    payload = dict(store.get_session(sid)["payload"])
    engine._drop_foreign_job_binding(sid, payload, FakeClient(None, raise_exc=True))
    assert payload["import_job_id"] == "job-x"


def test_cancel_is_not_overwritten(tmp_path):
    engine, store = _engine(tmp_path)
    sid = store.create_session("import_advice", {"minio_key": BOOK2})
    engine.cancel(sid)
    assert store.get_session(sid)["status"] == "cancelled"

    # 残余流程试图改回停靠态 → 必须被忽略
    engine._status(sid, "awaiting_user")
    engine._say_next_steps(sid, preface="仍有待办页")
    assert store.get_session(sid)["status"] == "cancelled"
    assert not any(
        "请选择下一步" in m["content"].get("text", "") for m in store.get_messages(sid)
    )

    # 用户主动续聊后解除封锁
    engine._cancelled.discard(sid)
    engine._status(sid, "executing")
    assert store.get_session(sid)["status"] == "executing"
