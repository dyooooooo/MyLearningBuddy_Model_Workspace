"""栏目框整名匹配：不把「习题3.1」当成「习题」。"""

from __future__ import annotations

import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1] / "workspace" / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import ir_lib  # noqa: E402


def test_match_box_kind_exact_only() -> None:
    prefs = ["习题", "思考与讨论", "拓展延伸"]
    assert ir_lib._match_box_kind("习题3.1", prefs) == ""
    assert ir_lib._match_box_kind("习题3.2", prefs) == ""
    assert ir_lib._match_box_kind("习题", prefs) == "习题"
    assert ir_lib._match_box_kind("思考与讨论：", prefs) == "思考与讨论"
    assert ir_lib._match_box_kind("拓展延伸", prefs) == "拓展延伸"
    assert ir_lib._match_box_kind("练习巩固题", ["练习巩固"]) == ""
