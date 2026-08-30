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


def test_fork_drops_files_added_after_the_checkpoint(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    page = home / "textbooks" / "book-1" / "pages" / "0005.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("good page\n", encoding="utf-8")
    base = checkpoints.snapshot("manual", "fork base")
    page.write_text("mangled\n", encoding="utf-8")
    stray = home / "memory" / "stray.md"
    stray.write_text("written after the base\n", encoding="utf-8")
    out = checkpoints.fork(base["id"], "clean-try")
    assert page.read_text(encoding="utf-8") == "good page\n"
    assert not stray.exists()
    assert "memory/stray.md" in out["removed_paths"]


def test_fork_keeps_uncommitted_work_on_the_old_branch(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    prompt = home / "prompts" / "chat.md"
    base = checkpoints.snapshot("manual", "fork base")
    prompt.write_text("work nobody committed\n", encoding="utf-8")
    out = checkpoints.fork(base["id"], "side-track")
    assert prompt.read_text(encoding="utf-8") != "work nobody committed\n"
    checkpoints.restore(out["safety_id"])
    assert prompt.read_text(encoding="utf-8") == "work nobody committed\n"


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


def test_restore_rewinds_conversation_and_lists_files(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    prompt = home / "prompts" / "chat.md"
    original = prompt.read_text(encoding="utf-8")
    conv = conversations.ensure("", message="先问一句")
    first = jobs.create_job("chat", {"message": "先问一句", "conversation_id": conv["id"]})
    sha = checkpoints.snapshot_turn(first["job_id"], conv["id"], "先问一句")
    conversations.attach_job(conv["id"], first["job_id"])
    prompt.write_text("changed-by-agent\n", encoding="utf-8")
    second = jobs.create_job("chat", {"message": "再问一句", "conversation_id": conv["id"]})
    conversations.attach_job(conv["id"], second["job_id"])
    out = checkpoints.restore(sha)
    assert prompt.read_text(encoding="utf-8") == original
    assert out["conversation_id"] == conv["id"]
    assert out["message"] == "先问一句"
    assert first["job_id"] in out["removed_job_ids"]
    assert second["job_id"] in out["removed_job_ids"]
    assert any(row["path"] == "prompts/chat.md" for row in out["changes"])
    thread = conversations.conversation_jobs(conv["id"])
    assert [row["job_id"] for row in thread] == []
    assert jobs.get_job(first["job_id"]) is None
    assert jobs.get_job(second["job_id"]) is None
    listed = conversations.list_conversations()
    assert all(row["id"] != first["job_id"] for row in listed)
    assert not any(row.get("preview") == "再问一句" for row in listed)


def test_restore_drops_files_added_after_the_checkpoint(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    (home / "memory" / "notes.md").write_text("kept\n", encoding="utf-8")
    sha = checkpoints.snapshot("manual", "before the agent wrote")["id"]
    extra = home / "memory" / "scratch.md"
    extra.write_text("written by the agent\n", encoding="utf-8")
    out = checkpoints.restore(sha)
    assert not extra.exists()
    assert "memory/scratch.md" in out["removed_paths"]
    assert any(row["path"] == "memory/scratch.md" and row["label"] == "撤掉" for row in out["changes"])


def test_restore_puts_a_textbook_page_back(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    page = home / "textbooks" / "book-1" / "pages" / "0005.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("good page\n", encoding="utf-8")
    scan = page.parent / "0005.jpg"
    scan.write_bytes(b"jpeg-bytes")
    sha = checkpoints.snapshot("manual", "page is fine")["id"]
    page.write_text("mangled by the agent\n", encoding="utf-8")
    new_page = page.parent / "0006.md"
    new_page.write_text("page the agent invented\n", encoding="utf-8")
    out = checkpoints.restore(sha)
    assert page.read_text(encoding="utf-8") == "good page\n"
    assert not new_page.exists()
    assert scan.is_file()
    paths = {row["path"] for row in out["changes"]}
    assert "textbooks/book-1/pages/0005.md" in paths
    assert "textbooks/book-1/pages/0005.jpg" not in paths


def test_restore_keeps_a_book_imported_after_the_checkpoint(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    sha = checkpoints.snapshot("manual", "before the import")["id"]
    page = home / "textbooks" / "book-2" / "pages" / "0001.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("imported page\n", encoding="utf-8")
    out = checkpoints.restore(sha)
    assert page.is_file()
    assert "textbooks/book-2/pages/0001.md" not in out["removed_paths"]
    kept = [row for row in out["changes"] if row["path"] == "textbooks/book-2/pages/0001.md"]
    assert kept and kept[0]["label"] == "新增，保留"


def test_restore_leaves_a_safety_checkpoint(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    prompt = home / "prompts" / "chat.md"
    sha = checkpoints.snapshot("manual", "base")["id"]
    prompt.write_text("uncommitted work\n", encoding="utf-8")
    out = checkpoints.restore(sha)
    assert prompt.read_text(encoding="utf-8") != "uncommitted work\n"
    checkpoints.restore(out["safety_id"])
    assert prompt.read_text(encoding="utf-8") == "uncommitted work\n"


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
