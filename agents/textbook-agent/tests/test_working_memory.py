from __future__ import annotations

import time

from app import conversations, jobs, working_memory
from app.config import settings
from app.sdk_errors import STALL_ZH


def _home(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(settings, "agent_home", str(home))
    jobs.reset_for_tests()
    conversations.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    conversations._loaded_home = str(settings.home)


def _wait_job(job_id: str, timeout: float = 4.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = jobs.get_job(job_id)
        if row and row["status"] in ("completed", "failed", "cancelled"):
            return row
        time.sleep(0.05)
    return jobs.get_job(job_id) or {}


def test_write_from_job_chat_only(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    cid = "conv-1"
    chat = {
        "job_id": "j1",
        "kind": "chat",
        "status": "failed",
        "payload": {"message": "把三级目录补进导入", "conversation_id": cid},
        "result": {"conversation_id": cid},
        "error": STALL_ZH,
        "trace": [
            {"kind": "tool", "title": "connect", "detail": "正在连接 Cursor"},
            {"kind": "tool", "title": "read completed", "detail": '{"path": "tools/build_toc.py"}'},
        ],
    }
    path = working_memory.write_from_job(chat)
    assert path.endswith(f"memory/working/{cid}.md")
    text = (settings.memory_dir / "working" / f"{cid}.md").read_text(encoding="utf-8")
    assert "把三级目录补进导入" in text
    assert "桥接超时" in text
    assert "tools/build_toc.py" in text
    assert working_memory.write_from_job({"kind": "grade", "payload": {"conversation_id": cid}}) == ""
    assert working_memory.write_from_job({"kind": "chat", "payload": {}}) == ""


def test_write_from_job_includes_page_focus(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    cid = "conv-focus"
    chat = {
        "job_id": "j-focus",
        "kind": "chat",
        "status": "completed",
        "payload": {
            "message": "改这段",
            "conversation_id": cid,
            "book_id": "book-1",
            "book": "沪教",
            "page_no": 12,
            "block_id": "p-3",
            "block_label": "例 3",
        },
        "result": {"conversation_id": cid},
        "error": "",
        "trace": [],
    }
    working_memory.write_from_job(chat)
    text = (settings.memory_dir / "working" / f"{cid}.md").read_text(encoding="utf-8")
    assert "聚焦：" in text
    assert "书 沪教" in text
    assert "第 12 页" in text
    assert "块 p-3" in text
    assert "例 3" in text


def test_flush_before_auto_resume(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    cid = "conv-flush"
    conversations.ensure(cid, "把三级目录补进导入")
    row = jobs.create_job("chat", {"message": "把三级目录补进导入", "conversation_id": cid, "mode": "agent"})
    conversations.attach_job(cid, row["job_id"])
    order: list[str] = []
    kick_text = {}

    real_write = working_memory.write_from_job

    def wrap_write(job):
        order.append("write")
        return real_write(job)

    def wrap_kick(job_id: str) -> None:
        order.append("kick")
        dest = settings.memory_dir / "working" / f"{cid}.md"
        kick_text["exists"] = dest.is_file()
        kick_text["body"] = dest.read_text(encoding="utf-8") if dest.is_file() else ""

    monkeypatch.setattr(working_memory, "write_from_job", wrap_write)
    monkeypatch.setattr(jobs, "_kick_auto_resume", wrap_kick)

    def boom() -> dict:
        raise TimeoutError(STALL_ZH)

    jobs.start_background(row["job_id"], boom)
    got = _wait_job(row["job_id"])
    assert got["status"] == "failed"
    deadline = time.time() + 2
    while time.time() < deadline and order != ["write", "kick"]:
        time.sleep(0.05)
    assert order == ["write", "kick"]
    assert kick_text.get("exists") is True
    assert "把三级目录补进导入" in kick_text.get("body", "")
    assert "桥接超时" in kick_text.get("body", "")


def test_non_chat_and_bad_id_skip(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    assert working_memory.write_from_job({"kind": "page_refine", "payload": {"conversation_id": "x"}}) == ""
    assert working_memory.working_path("../etc") is None
    assert working_memory.prompt_block("") == ""
    assert working_memory.prompt_block("not a cid!") == ""
