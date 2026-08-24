"""会话级文件检查点：保留 / 放弃（Cursor 式 Undo）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.loop import LoopEngine  # noqa: E402
from app.store import Store  # noqa: E402
from app.tools.fs_guard import WorktreeSnapshot  # noqa: E402


def test_keep_and_discard_checkpoint(tmp_path: Path):
    workspace = tmp_path / "ws"
    editions = workspace / "learning-buddy-api" / "configs" / "editions"
    editions.mkdir(parents=True)
    theme = editions / "theme.css"
    theme.write_text("body{color:red}\n")

    settings = Settings(
        workspace_root=workspace,
        copilot_db_path=tmp_path / "copilot.db",
    )
    store = Store(settings.db_path)
    engine = LoopEngine(settings, store, task_context_builder=lambda *_: "")
    sid = store.create_session("import_advice", {})

    snap = WorktreeSnapshot(
        workspace,
        ("learning-buddy-api/configs/editions/",),
        (".env",),
        (),
        (".css",),
    )
    snap.backup_guarded()
    theme.write_text("body{color:blue}\n")
    engine._arm_checkpoint(sid, snap)  # noqa: SLF001
    assert engine.has_pending_checkpoint(sid)

    r = engine.keep_files(sid)
    assert r["ok"] is True
    assert r["action"] == "keep"
    assert theme.read_text() == "body{color:blue}\n"
    assert not engine.has_pending_checkpoint(sid)

    # 再挂一轮后放弃
    snap2 = WorktreeSnapshot(
        workspace,
        ("learning-buddy-api/configs/editions/",),
        (".env",),
        (),
        (".css",),
    )
    snap2.backup_guarded()
    theme.write_text("body{color:green}\n")
    engine._arm_checkpoint(sid, snap2)  # noqa: SLF001
    r2 = engine.discard_files(sid, reason="测试放弃")
    assert r2["ok"] is True
    assert r2["action"] == "discard"
    assert theme.read_text() == "body{color:blue}\n"
    assert not engine.has_pending_checkpoint(sid)


def test_discard_without_checkpoint_is_noop(tmp_path: Path):
    settings = Settings(copilot_db_path=tmp_path / "copilot.db")
    store = Store(settings.db_path)
    engine = LoopEngine(settings, store, task_context_builder=lambda *_: "")
    sid = store.create_session("import_advice", {})
    r = engine.discard_files(sid)
    assert r["ok"] is False
    assert "没有待确认" in (r.get("error") or "")
