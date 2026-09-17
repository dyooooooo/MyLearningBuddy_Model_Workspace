"""Exercise kb/toc.py against the real shared lexicon, not the built-in fallback.

`tests/test_kb/test_toc.py` 全部跑在回退分支上 —— 本地没有 `/data/agent/tools/`，
`workspace_lexicon.lexicon()` 直接返回 None。结果是委托分支从来没被测过：章行的
`level` 被 `or 1` 从 0 顶成 1，两条分支行为不一致，测试却全绿。

这里把 `agent_textbooks_dir` 指到仓库里的 workspace，强制走委托分支，并逐条断言
它与回退分支给出同样的形状。agent 那边不在时跳过，不阻塞纯 buddy-api 的环境。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.kb import workspace_lexicon
from app.kb.toc import looks_like_toc_blob, parse_entry

WORKSPACE = (
    Path(__file__).resolve().parents[3] / "agents" / "textbook-agent" / "workspace"
)


@pytest.fixture
def shared_lexicon(monkeypatch):
    """Point the loader at the in-repo workspace so the delegated branch runs."""
    if not (WORKSPACE / "tools" / "toc_lexicon.py").is_file():
        pytest.skip("textbook-agent workspace not present")
    monkeypatch.setattr(
        workspace_lexicon.settings, "agent_textbooks_dir", str(WORKSPACE / "textbooks")
    )
    workspace_lexicon.reset_for_tests()
    if workspace_lexicon.lexicon() is None:
        pytest.fail("共享词法表就在仓库里却没加载成功，说明加载器坏了")
    yield
    workspace_lexicon.reset_for_tests()


def test_chapter_level_is_zero_like_the_fallback(shared_lexicon):
    """章是 level 0。走共享词法时曾被 `or 1` 顶成 1。"""
    out = parse_entry("第1章 集合与逻辑")
    assert out["is_chapter"] is True
    assert out["level"] == 0


def test_slash_page_number(shared_lexicon):
    """`/47` 斜杠后无空格：内置正则不认，label 还会残留尾部斜杠。"""
    out = parse_entry("第4章 怎样求合力与分力/47")
    assert out["label"] == "第4章 怎样求合力与分力"
    assert out["print_page"] == 47


def test_jie_section_is_recognised(shared_lexicon):
    """`第N节`：内置 `_SECTION = ^\\*?\\d+\\.\\d+` 完全不认。"""
    out = parse_entry("第一节 力的合成 48")
    assert out["label"] == "第一节 力的合成"
    assert out["print_page"] == 48
    assert out["is_chapter"] is False
    assert out["level"] == 1


def test_numeric_section_matches_fallback_shape(shared_lexicon):
    out = parse_entry("1.1 集合初步 2")
    assert out["label"] == "1.1 集合初步"
    assert out["print_page"] == 2
    assert out["level"] == 1


def test_image_lines_do_not_dilute_the_page_vote(shared_lexicon):
    """整页投票要排掉纯插图行。

    物理必修第 1 册第 7 页（第三、四章）就是栽在这里：11 条目录行 + 2 张装饰插图，
    算成 11/13 = 0.846 才过 0.55 的线，按整页算则是 7/13 = 0.538 —— 差一点点，
    于是这一页没被包成 `::: kb-page-toc`，前端不套目录格式。
    """
    blob = "\n".join(
        [
            "目录",
            "![](https://example.invalid/a.jpg)",
            "第3章 怎样描述力 32",
            "第一节 力的描述 33",
            "第二节 形变与弹力 38",
            "![](https://example.invalid/b.jpg)",
        ]
    )
    assert looks_like_toc_blob(blob) is True
