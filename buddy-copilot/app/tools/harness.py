"""验收执行器：pytest 子集（golden / 页面对比走 buddy_api 客户端）。"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class HarnessResult:
    ok: bool
    summary: str
    output: str


def run_pytest(workspace: Path, targets: list[str], timeout: int = 600) -> HarnessResult:
    """在 learning-buddy-api 自带 .venv 里跑指定测试子集。

    targets 仅允许 tests/ 下的相对路径，防止被用来执行任意文件。
    """
    api_dir = workspace / "learning-buddy-api"
    safe: list[str] = []
    for t in targets:
        t = t.strip()
        base = t.split("::", 1)[0]
        if not base.startswith("tests/") or ".." in base:
            return HarnessResult(False, f"非法测试路径: {t}", "")
        safe.append(t)
    if not safe:
        safe = ["tests/"]
    py = api_dir / ".venv" / "bin" / "python"
    try:
        p = subprocess.run(
            [str(py), "-m", "pytest", "-q", *safe],
            cwd=api_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return HarnessResult(False, f"pytest 超时（>{timeout}s）", "")
    tail = (p.stdout + "\n" + p.stderr)[-6000:]
    last = [ln for ln in p.stdout.splitlines() if ln.strip()][-1:] or ["(no output)"]
    return HarnessResult(p.returncode == 0, last[0], tail)
