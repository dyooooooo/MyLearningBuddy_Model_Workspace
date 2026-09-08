from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Dockerfile 拷进镜像的就这几条路径。
IMAGE_PATHS = ("app", "workspace", "client.py", "requirements.txt")


def _git(*args: str) -> str | None:
    """跑一条 git 命令；不在工作树里（例如从 git archive 解出来跑）就返回 None。"""
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def test_no_untracked_files_under_image_paths() -> None:
    """镜像会拷进去的目录里不许有未跟踪文件。

    未跟踪文件照样会被 docker build 从上下文里捡走，于是本机跑得好好的、
    clone 下来却缺件，而且一路静默 —— surface 加载失败只会回落成裸 markdown。
    lib/fig-size.mjs、app/workspace_module.py、.cursor/hooks/guard-redline.py
    都是这么漏掉的，各自漏了很久。
    """
    if _git("rev-parse", "--is-inside-work-tree") is None:
        pytest.skip("not a git work tree")
    out = _git("ls-files", "--others", "--exclude-standard", "--", *IMAGE_PATHS)
    assert out is not None, "git ls-files failed"
    stray = [line for line in out.splitlines() if line.strip()]
    assert not stray, (
        "这些文件会被打进镜像，但不在 git 里，clone 下来就没有：\n  "
        + "\n  ".join(stray)
    )
