"""v4 净化面：模型边界防腐层（唯一信任边界）。

35B 在 guided JSON decoding 下输出单反斜杠 LaTeX 时，命令首字母为
``b/f/n/r/t`` 的会形成 **合法** JSON 转义并被 ``json.loads`` 静默破坏成
控制字符（``\\beta``→``\\x08eta``、``\\frac``→``\\x0crac``、
``\\notin``→换行+``otin``、``\\text``→制表符+``ext``）；其余首字母是非法
转义，guided 语法逼模型写 ``\\\\`` 反而全部存活。

本模块在 ``PageBlueprint.model_validate`` 之前做两类**确定性**修复：

1. 控制字符白名单还原 —— 仅当控制字符后的字母残词与
   ``repair_rules.json`` 中某命令的去首字母形式**精确相等**时才还原，
   天然安全（``\\nequation`` 里的换行+``equation`` 不会被误改）。
2. LaTeX 环境自动闭合 —— ``\\begin{X}`` 缺配对 ``\\end{X}`` 时在
   math 区域末尾补齐（p14 残缺 cases 类）。

规则库是数据文件：判例晋级只改 ``repair_rules.json``，不改代码。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_RULES_PATH = Path(__file__).resolve().parent / "repair_rules.json"

# JSON 合法转义字母 → json.loads 产出的控制字符
_ESCAPE_CHAR: dict[str, str] = {
    "b": "\x08",
    "f": "\x0c",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_CTRL_TO_LETTER = {v: k for k, v in _ESCAPE_CHAR.items()}

_DAMAGE_RE = re.compile(r"[\x08\x0c\n\r\t]([a-zA-Z]+)")
_HARD_CTRL_RE = re.compile(r"[\x08\x0c\x0b]")


@lru_cache(maxsize=1)
def _remainder_map() -> dict[str, dict[str, str]]:
    """letter → {命令去首字母残词: 完整命令}."""
    data = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    out: dict[str, dict[str, str]] = {k: {} for k in _ESCAPE_CHAR}
    for cmd in data.get("commands", []):
        first = cmd[0]
        if first in out:
            out[first][cmd[1:]] = cmd
    return out


# 只有首字母属于 JSON 合法转义集（b/f/n/r/t）的命令才会被静默破坏，也才有资格
# 进 repair_rules.json 白名单——其余首字母是非法转义，guided 语法逼模型写
# ``\\`` 反而全部存活，无需修复。
ESCAPE_PRONE_FIRST_LETTERS = frozenset(_ESCAPE_CHAR.keys())


def whitelisted_commands() -> set[str]:
    """当前 repair_rules.json 白名单里的命令名集合（供晋级门判重）。"""
    data = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    return set(data.get("commands", []))


def repair_escape_damage(text: str) -> tuple[str, list[dict[str, Any]]]:
    """还原 JSON 转义静默破坏的 LaTeX 命令；返回 (修复后文本, 修复记录)。"""
    if not text or not _DAMAGE_RE.search(text):
        return text, []
    repairs: list[dict[str, Any]] = []
    rules = _remainder_map()

    def _sub(m: re.Match[str]) -> str:
        ctrl = m.group(0)[0]
        word = m.group(1)
        letter = _CTRL_TO_LETTER[ctrl]
        cmd = rules[letter].get(word)
        if cmd is None:
            return m.group(0)
        repairs.append({"rule": f"ctrl_{letter}", "command": cmd, "damaged": repr(m.group(0))})
        return "\\" + cmd

    return _DAMAGE_RE.sub(_sub, text), repairs


_BEGIN_RE = re.compile(r"\\begin\{([a-zA-Z*]+)\}")
_END_RE = re.compile(r"\\end\{([a-zA-Z*]+)\}")


def close_environments(latex: str) -> tuple[str, list[dict[str, Any]]]:
    """为不配对的 ``\\begin{X}`` 补齐 ``\\end{X}``（栈式，后开先闭）。"""
    if "\\begin{" not in (latex or ""):
        return latex, []
    stack: list[str] = []
    for m in re.finditer(r"\\(begin|end)\{([a-zA-Z*]+)\}", latex):
        kind, env = m.group(1), m.group(2)
        if kind == "begin":
            stack.append(env)
        elif stack and stack[-1] == env:
            stack.pop()
    if not stack:
        return latex, []
    repairs = [{"rule": "env_autoclose", "env": env} for env in reversed(stack)]
    suffix = "".join(f" \\end{{{env}}}" for env in reversed(stack))
    return latex.rstrip() + suffix, repairs


_DOLLAR_REGION_RE = re.compile(r"\$([^$]+)\$")


def _close_environments_in_prose(text: str) -> tuple[str, list[dict[str, Any]]]:
    """对文本中每个 ``$...$`` 区域独立补闭合，``\\end`` 落在 ``$`` 之内。"""
    if "\\begin{" not in (text or ""):
        return text, []
    repairs: list[dict[str, Any]] = []

    def _sub(m: re.Match[str]) -> str:
        fixed, r = close_environments(m.group(1))
        repairs.extend(r)
        return f"${fixed}$"

    return _DOLLAR_REGION_RE.sub(_sub, text), repairs


def repair_string(text: str, *, math_only: bool = False) -> tuple[str, list[dict[str, Any]]]:
    fixed, repairs = repair_escape_damage(text)
    if math_only:
        fixed, r2 = close_environments(fixed)
    else:
        fixed, r2 = _close_environments_in_prose(fixed)
    return fixed, repairs + r2


def has_hard_control_chars(text: str) -> bool:
    """还原后仍残留 ``\\x08/\\x0c/\\x0b`` ⇒ 无法确定性还原的损伤。"""
    return bool(text and _HARD_CTRL_RE.search(text))


def _walk(obj: Any, repairs: list[dict[str, Any]], path: str, *, math_only: bool) -> Any:
    if isinstance(obj, str):
        fixed, r = repair_string(obj, math_only=math_only)
        for item in r:
            item["path"] = path
        repairs.extend(r)
        return fixed
    if isinstance(obj, list):
        return [
            _walk(v, repairs, f"{path}[{i}]", math_only=math_only) for i, v in enumerate(obj)
        ]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            # equation.latex 是纯 LaTeX（无 $ 定界符），环境闭合作用于整串
            child_math_only = math_only or (k == "latex")
            out[k] = _walk(v, repairs, f"{path}.{k}", math_only=child_math_only)
        return out
    return obj


def repair_blueprint_dict(raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """对 raw blueprint dict 的所有字符串做确定性修复；返回 (新 dict, 修复记录)。"""
    repairs: list[dict[str, Any]] = []
    fixed = _walk(raw, repairs, "$", math_only=False)
    return fixed, repairs
