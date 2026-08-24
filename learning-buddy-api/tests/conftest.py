"""在容器外跑测试时，把数据目录指回仓库自己的 `data/`。

`settings.corpus_dir` 默认是容器里的 `/app/data`，宿主机上不存在，会让读语料
（题库、大纲）的测试凭空变红。这里只在默认路径不存在时改指向，容器里跑不受影响。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings

REPO_DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(autouse=True, scope="session")
def repo_data_dir() -> None:
    if not Path(settings.corpus_dir).is_dir() and REPO_DATA.is_dir():
        settings.corpus_dir = str(REPO_DATA)
