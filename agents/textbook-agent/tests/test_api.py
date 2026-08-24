from __future__ import annotations

import os

os.environ.setdefault("AGENT_HOME", "/tmp/textbook-agent-test")
os.environ.setdefault("TEXTBOOK_AGENT_TOKEN", "test-token")

from fastapi.testclient import TestClient

from app.main import app
from app.workspace import ensure_workspace


def _client() -> TestClient:
    ensure_workspace()
    return TestClient(app)


def test_health_no_auth() -> None:
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_jobs_reject_missing_token() -> None:
    r = _client().get("/v1/jobs")
    assert r.status_code == 401


def test_grade_rejects_missing_token() -> None:
    r = _client().post(
        "/v1/grade",
        json={"request_id": "t1", "problem_text": "x", "student_answer": ""},
    )
    assert r.status_code == 401


def test_grade_book_missing_without_sdk(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TEXTBOOK_AGENT_TOKEN", "test-token")
    from app.config import settings

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    ensure_workspace()
    client = TestClient(app)
    r = client.post(
        "/v1/grade",
        headers={"Authorization": "Bearer test-token"},
        json={
            "request_id": "lb-1",
            "problem_text": "用列举法表示所有不大于10的正整数组成的集合",
            "student_answer": "{0,1,2,3,4,5,6,7,8,9,10}",
        },
    )
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    for _ in range(20):
        j = client.get(f"/v1/jobs/{job_id}", headers={"Authorization": "Bearer test-token"})
        assert j.status_code == 200
        body = j.json()
        if body["status"] == "completed":
            assert body["result"]["book_missing"] is True
            assert body["result"]["correct"] is None
            kinds = [s["kind"] for s in body.get("trace") or []]
            assert "search" in kinds
            listed = client.get("/v1/jobs", headers={"Authorization": "Bearer test-token"})
            assert listed.status_code == 200
            assert any(j["job_id"] == job_id for j in listed.json()["jobs"])
            return
        if body["status"] == "failed":
            raise AssertionError(body["error"])
    raise AssertionError("job did not finish")


def test_grade_mocked_sdk_set_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    ensure_workspace()
    pdf = settings.textbooks_dir / "math" / "demo.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4")
    (pdf.parent / "001.md").write_text("1.1 集合初步 正整数 例3\n", encoding="utf-8")

    def fake_grade(payload: dict, job_id: str = "") -> dict:
        return {
            "request_id": payload["request_id"],
            "correct": False,
            "verdict": "错",
            "correct_solution": "不大于10的正整数是{1,2,3,4,5,6,7,8,9,10}，0不是正整数。",
            "textbook": {
                "title": "普通高中教科书 数学 必修第一册",
                "chapter": "第1章 集合与逻辑",
                "section": "1.1 集合初步",
                "examples": ["例3"],
            },
            "book_missing": False,
            "notes": "学生把0算进正整数。",
        }

    monkeypatch.setattr("app.sdk_runner.run_grade", fake_grade)
    client = TestClient(app)
    r = client.post(
        "/v1/grade",
        headers={"Authorization": "Bearer test-token"},
        json={"request_id": "lb-1", "problem_text": "正整数", "student_answer": "{0}"},
    )
    job_id = r.json()["job_id"]
    for _ in range(20):
        body = client.get(
            f"/v1/jobs/{job_id}",
            headers={"Authorization": "Bearer test-token"},
        ).json()
        if body["status"] == "completed":
            assert body["result"]["correct"] is False
            assert "1.1" in body["result"]["textbook"]["section"]
            assert body["result"]["book_missing"] is False
            return
    raise AssertionError("job did not finish")


def test_run_chat_not_blocked_without_books(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app import sdk_runner

    settings.agent_home = str(tmp_path / "home")
    ensure_workspace()

    def fake_sdk(prompt: str, conversation_id: str = "", job_id: str = "", **_kw) -> tuple[str, str]:
        assert "通用 Agent" in prompt
        return "可以改配置", "agent-1"

    monkeypatch.setattr(sdk_runner, "_prompt_sdk", fake_sdk)
    out = sdk_runner.run_chat("加一个工具")
    assert out["book_missing"] is False
    assert out["reply"] == "可以改配置"
    assert out["conversation_id"] == "agent-1"
    assert out["mode"] == "agent"


def test_run_chat_ask_mode_is_read_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app import sdk_runner

    settings.agent_home = str(tmp_path / "home")
    ensure_workspace()

    def fake_sdk(prompt: str, conversation_id: str = "", job_id: str = "", **kw) -> tuple[str, str]:
        assert "问答模式" in prompt
        assert kw.get("mode") == "ask"
        assert "inbox/a.png" in prompt
        return "只读回答", "agent-ask"

    monkeypatch.setattr(sdk_runner, "_prompt_sdk", fake_sdk)
    out = sdk_runner.run_chat(
        "这页写什么",
        mode="ask",
        attachments=[{"kind": "image", "name": "a.png", "path": "inbox/a.png"}],
    )
    assert out["mode"] == "ask"
    assert out["reply"] == "只读回答"


def test_chat_without_books_calls_sdk(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    ensure_workspace()

    def fake_chat(message: str, conversation_id: str = "", job_id: str = "", **_kw) -> dict:
        return {"conversation_id": "c1", "reply": f"收到：{message}", "book_missing": False}

    monkeypatch.setattr("app.sdk_runner.run_chat", fake_chat)
    client = TestClient(app)
    r = client.post(
        "/v1/chat",
        headers={"Authorization": "Bearer test-token"},
        json={"message": "给自己加一个数数工具"},
    )
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    for _ in range(20):
        body = client.get(
            f"/v1/jobs/{job_id}",
            headers={"Authorization": "Bearer test-token"},
        ).json()
        if body["status"] == "completed":
            assert body["result"]["book_missing"] is False
            assert "数数" in body["result"]["reply"]
            return
        if body["status"] == "failed":
            raise AssertionError(body["error"])
    raise AssertionError("job did not finish")


def test_models_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app import models_catalog

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    monkeypatch.setattr(models_catalog, "_sdk_models", lambda: [])
    client = TestClient(app)
    r = client.get("/v1/models", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["models"]]
    assert "composer-2.5" in ids
    grok = next(m for m in r.json()["models"] if m["id"] == "grok-4.6")
    assert grok["group"] == "cursor"
    assert any(p["id"] == "effort" for p in grok["parameters"])
    assert any(p["id"] == "fast" for p in grok["parameters"])


def test_chat_stores_attachments_without_bytes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    ensure_workspace()

    def fake_chat(message: str, conversation_id: str = "", job_id: str = "", **kw) -> dict:
        assert kw.get("mode") == "plan"
        assert kw.get("images")
        return {"conversation_id": "c1", "reply": "看到图了", "book_missing": False, "mode": "plan"}

    monkeypatch.setattr("app.sdk_runner.run_chat", fake_chat)
    client = TestClient(app)
    r = client.post(
        "/v1/chat",
        headers={"Authorization": "Bearer test-token"},
        json={
            "message": "看这张图",
            "mode": "plan",
            "model": "composer-2.5",
            "images": [{"data": "aGVsbG8=", "mime_type": "image/png", "name": "shot.png"}],
        },
    )
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    for _ in range(20):
        body = client.get(
            f"/v1/jobs/{job_id}",
            headers={"Authorization": "Bearer test-token"},
        ).json()
        if body["status"] == "completed":
            payload = body["payload"]
            assert payload["mode"] == "plan"
            assert "aGVsbG8=" not in str(payload)
            assert payload["attachments"][0]["name"].endswith("shot.png")
            name = payload["attachments"][0]["name"]
            img = client.get(f"/v1/inbox/{name}", headers={"Authorization": "Bearer test-token"})
            assert img.status_code == 200
            assert img.content == b"hello"
            return
        if body["status"] == "failed":
            raise AssertionError(body["error"])
    raise AssertionError("job did not finish")


def test_chat_rejects_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    r = TestClient(app).post(
        "/v1/chat",
        headers={"Authorization": "Bearer test-token"},
        json={"message": ""},
    )
    assert r.status_code == 400
