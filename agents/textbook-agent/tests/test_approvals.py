from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import approvals, jobs, manage_tools
from app.config import settings
from app.context import current_job_id

BRIEF = {
    "situation": "沪教高中数学第5页公式被拆成上下标，复现：打开 pages/0005.md。",
    "why": "这是全书公式渲染的共性形状，不改则每本新书都会再拆一次。",
    "changes": "改 tools/refine_page.py 的 strip_sub_sup，把连续上下标合并成一组。",
    "impact": "已导入的书页不会自动变；新导入和 rebuild 会用新逻辑。可用检查点回退。",
}

NORM_CASES = (
    ("/data/agent/tools/foo.py", "tools/foo.py"),
    ("tools/foo.py", "tools/foo.py"),
    ("foo.py", "tools/foo.py"),
    ("./tools/foo.py", "tools/foo.py"),
    ("/data/agent/.cursor/hooks/guard-redline.py", ".cursor/hooks/guard-redline.py"),
    (".cursor/hooks/guard-redline.py", ".cursor/hooks/guard-redline.py"),
    ("surfaces/page-body/prepare.mjs", "surfaces/page-body/prepare.mjs"),
    ("/data/agent/surfaces/toc-tree/theme.css", "surfaces/toc-tree/theme.css"),
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "agent_home", str(tmp_path))
    jobs.reset_for_tests()
    return tmp_path


def test_norm_path_vectors(home) -> None:
    import importlib.util
    from pathlib import Path

    hook = (
        Path(__file__).resolve().parents[1]
        / "workspace"
        / ".cursor"
        / "hooks"
        / "guard-redline.py"
    )
    spec = importlib.util.spec_from_file_location("guard_redline_norm", hook)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    for raw, want in NORM_CASES:
        assert approvals.norm_path(raw, home="/data/agent") == want
        assert mod.norm_path(raw, home="/data/agent") == want


def test_validate_rejects_short_or_missing() -> None:
    good = dict(BRIEF)
    assert approvals.validate(good) == []
    short = dict(BRIEF, situation="太短")
    assert approvals.validate(short) == ["situation"]
    empty = {k: "" for k in approvals.FOUR_FIELDS}
    assert set(approvals.validate(empty)) == set(approvals.FOUR_FIELDS)


def test_open_grant_consume_once(home) -> None:
    record = approvals.open_request("tools/refine_page.py", **BRIEF)
    req_id = record["req_id"]
    assert approvals.load_pending(req_id)
    ticket = approvals.grant(req_id, job_id="job-1", conversation_id="c-1")
    assert ticket["status"] == "granted"
    assert ticket["one_shot"] is True
    assert approvals.load_pending(req_id) is None
    granted = home / "memory" / "approvals" / "granted" / f"{req_id}.json"
    assert granted.is_file()


def test_refuse_and_revise_do_not_grant(home) -> None:
    a = approvals.open_request("tools/a.py", **BRIEF)
    b = approvals.open_request("tools/b.py", **BRIEF)
    refused = approvals.refuse(a["req_id"], "不要改")
    assert refused["status"] == "refused"
    assert not (home / "memory" / "approvals" / "granted" / f"{a['req_id']}.json").exists()
    with pytest.raises(ValueError):
        approvals.revise(b["req_id"], "   ")
    revised = approvals.revise(b["req_id"], "先只改目录页，不要动解析器。")
    assert revised["status"] == "revised"
    assert approvals.load_pending(b["req_id"]) is None
    assert not (home / "memory" / "approvals" / "granted" / f"{b['req_id']}.json").exists()


def test_grant_missing_is_not_found(home) -> None:
    with pytest.raises(FileNotFoundError):
        approvals.grant("ab" * 16)


def test_request_code_edit_rejects_short(home) -> None:
    out = manage_tools.request_code_edit("tools/foo.py", "短", BRIEF["why"], BRIEF["changes"], BRIEF["impact"])
    assert out["ok"] is False
    assert "situation" in out["missing"]


def test_request_code_edit_rejects_unmanaged(home) -> None:
    out = manage_tools.request_code_edit("textbooks/x/pages/0001.md", **BRIEF)
    assert out["ok"] is False
    assert "path" in out["missing"]


def test_request_code_edit_writes_trace(home) -> None:
    row = jobs.create_job("chat", {"conversation_id": "c1", "message": "改工具"})
    token = current_job_id.set(row["job_id"])
    try:
        out = manage_tools.request_code_edit("tools/refine_page.py", **BRIEF)
    finally:
        current_job_id.reset(token)
    assert out["ok"] is True
    job = jobs.get_job(row["job_id"])
    kinds = [s.get("kind") for s in (job or {}).get("trace") or []]
    assert "approval" in kinds
    pending = approvals.list_pending()
    assert pending and pending[0]["path"] == "tools/refine_page.py"


def test_decide_api_grant_and_revise(home) -> None:
    os_token = "test-token"
    settings.textbook_agent_token = os_token
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {os_token}"}
    record = approvals.open_request("tools/foo.py", **BRIEF)
    r = client.post(
        f"/v1/approvals/{record['req_id']}/decide",
        headers=headers,
        json={"choice": "grant", "job_id": ""},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "granted"

    other = approvals.open_request("tools/bar.py", **BRIEF)
    bad = client.post(
        f"/v1/approvals/{other['req_id']}/decide",
        headers=headers,
        json={"choice": "revise", "note": ""},
    )
    assert bad.status_code == 400
    ok = client.post(
        f"/v1/approvals/{other['req_id']}/decide",
        headers=headers,
        json={"choice": "revise", "note": "换一种改法，只动目录页。"},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "revised"


def test_expired_ticket_not_listed_as_pending(home) -> None:
    record = approvals.open_request("tools/foo.py", **BRIEF)
    ticket = approvals.grant(record["req_id"])
    expires = datetime.now(timezone.utc) - timedelta(minutes=1)
    path = home / "memory" / "approvals" / "granted" / f"{ticket['req_id']}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["expires_at"] = expires.isoformat()
    path.write_text(json.dumps(data), encoding="utf-8")
    assert approvals.list_pending() == []
