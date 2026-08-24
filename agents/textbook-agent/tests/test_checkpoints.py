from __future__ import annotations

from app.config import settings
from app import checkpoints, conversations, jobs
from app.workspace import ensure_workspace


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setattr(settings, "agent_home", str(home))
    conversations.reset_for_tests()
    jobs.reset_for_tests()
    ensure_workspace()
    return home


def test_snapshot_restore_keeps_traces(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    prompts = home / "prompts" / "chat.md"
    original = prompts.read_text(encoding="utf-8")
    first = checkpoints.snapshot("manual", "before edit")
    prompts.write_text("changed-by-test\n", encoding="utf-8")
    traces = home / "traces" / "keep-me.json"
    traces.parent.mkdir(parents=True, exist_ok=True)
    traces.write_text('{"ok": true}\n', encoding="utf-8")
    checkpoints.snapshot("manual", "after edit")
    checkpoints.restore(first["id"])
    assert prompts.read_text(encoding="utf-8") == original
    assert traces.is_file()
    assert traces.read_text(encoding="utf-8") == '{"ok": true}\n'


def test_fork_creates_conversation(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    (home / "prompts" / "chat.md").write_text("fork-base\n", encoding="utf-8")
    base = checkpoints.snapshot("manual", "fork base")
    out = checkpoints.fork(base["id"], "try-one")
    assert out["branch"] == "fork/try-one"
    assert out["conversation_id"]
    row = conversations.get(out["conversation_id"])
    assert row is not None
    assert "分叉" in str(row.get("title") or "")


def test_snapshot_turn_binds_job(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    row = jobs.create_job("chat", {"message": "hello"})
    sha = checkpoints.snapshot_turn(row["job_id"], "conv-1", "hello")
    assert sha
    got = jobs.get_job(row["job_id"])
    assert jobs.job_checkpoint(got) == sha


def test_exclude_ignores_venv_tmp(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    checkpoints.ensure_repo()
    text = (home / ".checkpoints.git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".venv_tmp/" in text
    assert "*.libs/" in text
    ignore = home / ".cursorignore"
    assert ignore.is_file()
    body = ignore.read_text(encoding="utf-8")
    assert ".venv_tmp/" in body
    assert ".checkpoints.git/" in body


def test_backfill_attaches_missing_jobs(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    row = jobs.create_job("chat", {"message": "old turn"})
    first = checkpoints.backfill_missing_jobs()
    assert first["attached"] == 1
    assert first["id"]
    got = jobs.get_job(row["job_id"])
    assert jobs.job_checkpoint(got) == first["id"]
    again = checkpoints.backfill_missing_jobs()
    assert again["attached"] == 0
