from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.workspace import ensure_workspace


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app import conversations

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    conversations.reset_for_tests()
    ensure_workspace()
    return settings, conversations


def test_title_from_first_message() -> None:
    from app.conversations import clean_agent_title, make_title

    assert make_title("帮我把目录标题修好") == "把目录标题修好"
    assert make_title("") == "新对话"
    assert clean_agent_title("「修目录显示」\n其他") == "修目录显示"
    assert clean_agent_title("") == ""


def test_chained_cursor_ids_merge_into_one_thread(tmp_path, monkeypatch) -> None:
    _settings, conversations = _home(tmp_path, monkeypatch)
    from app import jobs

    jobs._jobs.clear()
    jobs._loaded_home = str(_settings.home)
    chain = [
        ("j1", "", "A"),
        ("j2", "A", "B"),
        ("j3", "B", "C"),
        ("j4", "C", "D"),
    ]
    for i, (jid, pay, res) in enumerate(chain):
        jobs._jobs[jid] = {
            "job_id": jid,
            "kind": "chat",
            "status": "completed",
            "payload": {"message": f"第{i + 1}句", "conversation_id": pay},
            "result": {"conversation_id": res, "reply": "ok"},
            "error": "",
            "trace": [],
            "created_at": f"2026-08-20T08:0{i}:00+00:00",
            "updated_at": f"2026-08-20T08:0{i}:10+00:00",
        }
    monkeypatch.setattr("app.sdk_runner.summarize_title", lambda _blob: "修目录并清理轨迹")
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    rows = client.get("/v1/conversations", headers=headers).json()["conversations"]
    assert len(rows) == 1
    assert rows[0]["turns"] == 4
    detail = client.get(f"/v1/conversations/{rows[0]['id']}", headers=headers).json()
    assert [j["job_id"] for j in detail["jobs"]] == ["j1", "j2", "j3", "j4"]
    conversations.set_title(rows[0]["id"], "修目录并清理轨迹", "agent")
    assert conversations.get("D")["id"] == rows[0]["id"]
    assert conversations.get(rows[0]["id"])["title"] == "修目录并清理轨迹"


def test_same_conversation_stays_one_thread(tmp_path, monkeypatch) -> None:
    _settings, conversations = _home(tmp_path, monkeypatch)

    def fake_chat(message: str, conversation_id: str = "", job_id: str = "", **_kw) -> dict:
        return {"conversation_id": conversation_id, "reply": f"回：{message}", "book_missing": False}

    monkeypatch.setattr("app.sdk_runner.run_chat", fake_chat)
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    first = client.post("/v1/chat", headers=headers, json={"message": "帮我看看第6页目录"})
    assert first.status_code == 202
    cid = first.json()["conversation_id"]
    title = first.json()["title"]
    assert cid
    assert title == "看看第6页目录"
    second = client.post(
        "/v1/chat",
        headers=headers,
        json={"message": "再改一下标题标签", "conversation_id": cid},
    )
    assert second.json()["conversation_id"] == cid
    listed = client.get("/v1/conversations", headers=headers)
    assert listed.status_code == 200
    rows = listed.json()["conversations"]
    assert len(rows) == 1
    assert rows[0]["id"] == cid
    assert rows[0]["turns"] == 2
    assert rows[0]["title"] == title
    detail = client.get(f"/v1/conversations/{cid}", headers=headers)
    assert detail.status_code == 200
    jobs = detail.json()["jobs"]
    assert len(jobs) == 2
    assert jobs[0]["payload"]["message"] == "帮我看看第6页目录"


def test_new_chat_is_a_new_conversation(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)

    def fake_chat(message: str, conversation_id: str = "", **_kw) -> dict:
        return {"conversation_id": conversation_id, "reply": "ok", "book_missing": False}

    monkeypatch.setattr("app.sdk_runner.run_chat", fake_chat)
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    a = client.post("/v1/chat", headers=headers, json={"message": "第一场"}).json()["conversation_id"]
    b = client.post("/v1/chat", headers=headers, json={"message": "第二场"}).json()["conversation_id"]
    assert a != b
    rows = client.get("/v1/conversations", headers=headers).json()["conversations"]
    assert len(rows) == 2
