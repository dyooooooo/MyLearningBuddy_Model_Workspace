"""fs_guard 批准范围与爆炸半径。

回归的是一个具体漏洞：只要会话里有**任意一张**已批准的 fs.request_edit，
白名单内的所有文件就都能改。批准必须按路径求交。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.tools.fs_guard import (
    covered_by_approval,
    is_auto_allowed,
    is_whitelisted,
    WorktreeSnapshot,
)

_PLUGIN = "learning-buddy-api/configs/checks/vocab_ext/table_cells.py"
_DRAFT = "learning-buddy-api/configs/checks/vocab_ext/proposals/table_cells.py"
_YAML = "learning-buddy-api/configs/checks/vocabulary.yaml"


def test_file_approval_is_exact():
    approved = ["learning-buddy-api/data/prompts/mineru_page_refine_v2.md"]
    assert covered_by_approval(approved[0], approved)
    assert not covered_by_approval(
        "learning-buddy-api/data/prompts/other.md", approved
    )


def test_directory_approval_is_prefix():
    approved = ["learning-buddy-api/configs/editions/hujiao_math_hs_v1/"]
    assert covered_by_approval(
        "learning-buddy-api/configs/editions/hujiao_math_hs_v1/layout_rules.yaml",
        approved,
    )
    assert not covered_by_approval(
        "learning-buddy-api/configs/editions/other_book/config.json", approved
    )


def test_empty_approval_covers_nothing():
    assert not covered_by_approval("learning-buddy-api/tests/test_x.py", [])


# ── 专属闸：vocab_ext/ 里是会在 buddy-worker 进程里执行的受限 Python ────────────


def test_plugin_dir_does_not_inherit_the_directory_whitelist():
    """回归的是这条漏洞：configs/checks/ 一进白名单，vocab_ext/ 就自动继承了。"""
    assert is_whitelisted(_YAML)
    assert not is_whitelisted(_PLUGIN)
    # 专属闸自己的申请工具是唯一该开 allow_gated 的地方。
    assert is_whitelisted(_PLUGIN, allow_gated=True)


def test_parent_directory_approval_does_not_open_the_plugin_dir():
    """批了一份 YAML 的目录，不等于批了里面的可执行代码。"""
    approved = ["learning-buddy-api/configs/checks/"]
    assert covered_by_approval(_YAML, approved)
    assert not covered_by_approval(_PLUGIN, approved)


def test_plugin_needs_an_approval_under_its_own_prefix():
    assert covered_by_approval(
        _PLUGIN, ["learning-buddy-api/configs/checks/vocab_ext/"]
    )
    assert covered_by_approval(_PLUGIN, [_PLUGIN])


def test_unexecuted_drafts_go_through_the_ordinary_door():
    """加载器只扫 vocab_ext/ 顶层，proposals/ 里的草稿永远不会被执行。"""
    assert is_whitelisted(_DRAFT)
    assert covered_by_approval(_DRAFT, ["learning-buddy-api/configs/checks/"])


def test_plugin_dir_is_never_auto_allowed():
    """哪怕将来往 auto_allow_suffixes 里加了 .py，vocab_ext/ 也不放行。"""
    assert not is_auto_allowed(_PLUGIN, auto_allow_suffixes=(".py",))
    assert is_auto_allowed(_DRAFT, auto_allow_suffixes=(".py",))


def _git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_changed_line_count_counts_edits_and_new_files(tmp_path):
    repo = _git_repo(tmp_path)
    snap = WorktreeSnapshot(repo, whitelist=(), forbidden=(), guarded_roots=())
    (repo / "a.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    (repo / "b.py").write_text("p = 1\nq = 2\n", encoding="utf-8")
    total = snap.changed_line_count(["a.py", "b.py"])
    # a.py: 2 added; b.py: 2 lines in a brand-new file.
    assert total == 4


def test_changed_line_count_is_zero_without_changes(tmp_path):
    repo = _git_repo(tmp_path)
    snap = WorktreeSnapshot(repo, whitelist=(), forbidden=(), guarded_roots=())
    assert snap.changed_line_count([]) == 0


def test_diff_stat_scopes_to_given_files_not_whole_repo(tmp_path):
    """回归：导入会话的 diff 卡曾把整库未提交改动列进去。"""
    repo = _git_repo(tmp_path)
    (repo / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (repo / "noise.py").write_text("n = 1\n", encoding="utf-8")
    snap = WorktreeSnapshot(repo, whitelist=(), forbidden=(), guarded_roots=())
    stat = snap.diff_stat(["a.py"])
    assert "a.py" in stat
    assert "noise.py" not in stat
    assert snap.diff_stat([]) == ""
    assert snap.diff_stat(None) == ""
