"""挂载目录里的词插件：受限 Python，热加载，改完不重建镜像。

为什么要有这一层：绝大多数新「眼睛」（一个可被检出的现象）都能写成
``configs/checks/vocabulary.yaml`` 里的一行表达式。但少数原语——例如遍历表格
单元格做跨行比对——一条表达式真写不出来。这个目录就是给那少数几个用的：它和
检测器同一个 bind-mount 目录，所以加一个原语依然不需要重建镜像。

安全边界必须说清楚：这里的代码**在 buddy-worker 进程里执行**。沙箱挡得住越权
（禁 import、禁下划线属性、禁改传入对象、禁 ``while`` 死循环、单次调用超时即停），
挡不住「算得不对」。所以每个插件必须自带双极性正反例，加载期跑不过就不加载——
正反例是这一层唯一的正确性保证。

插件文件长这样（``configs/checks/vocab_ext/xxx.py``）::

    NAME = "table_row_has_mixed_units"
    SCOPE = "block"                      # block | page
    DOC = "表格同一行里混用了不同单位"
    REQUIRES = ["type", "text"]          # 只能看见这里声明的字段
    EXAMPLES = {
        "positive": [{"type": "table", "text": "3 cm / 4 米"}],
        "negative": [{"type": "table", "text": "3 cm / 4 cm"}],
    }

    def evaluate(fields):
        return fields["type"] == "table" and has_cjk(fields["text"])

``fields`` 是只读映射，键就是 ``REQUIRES`` 声明的那些——声明什么才看得见什么，
依赖契约在这里是有牙齿的，不是注释。
"""
from __future__ import annotations

import ast
import logging
import re
import time
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Sequence

from RestrictedPython import compile_restricted, safe_builtins
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import (
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
    safer_getattr,
)

logger = logging.getLogger(__name__)

PLUGINS_DIRNAME = "vocab_ext"
# 未过审的草稿放这里：加载器不看子目录，所以放进来的文件永远不会被执行。
PROPOSALS_DIRNAME = "proposals"

MAX_SOURCE_CHARS = 8000
# 单次调用的墙钟上限。超时不代表能中断（Python 里没法安全地打断一段计算），
# 但连续超时就把这个词停掉，避免一本书被一个慢插件拖死。
CALL_TIME_LIMIT_MS = 50.0
MAX_TIMEOUT_STRIKES = 3

_NAME_RE = re.compile(r"[a-z][a-z0-9_]{2,63}")
_SCOPES = ("block", "page")

# 静态禁令。``while`` 是唯一能写出无界循环的语法（没有 import，也就没有
# itertools.count 这类无穷迭代器），禁掉它等于把死循环挡在加载期。
_FORBIDDEN_NODES: tuple[tuple[type[ast.AST], str], ...] = (
    (ast.Import, "插件不准 import"),
    (ast.ImportFrom, "插件不准 import"),
    (ast.While, "插件不准用 while（无界循环）；遍历数据请用 for"),
    (ast.Global, "插件不准用 global"),
    (ast.Nonlocal, "插件不准用 nonlocal"),
    (ast.AsyncFunctionDef, "插件不准写 async"),
    (ast.Await, "插件不准写 await"),
    (ast.Yield, "插件不准写 yield"),
    (ast.YieldFrom, "插件不准写 yield from"),
)

# safe_builtins 少了几个纯函数式的常用名（any/all/sum/min/max…），补上；
# 同时摘掉 setattr / delattr / __build_class__ 这些没必要给插件的能力。
_EXTRA_BUILTINS: dict[str, Any] = {
    "all": all,
    "any": any,
    "dict": dict,
    "enumerate": enumerate,
    "filter": filter,
    "frozenset": frozenset,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "reversed": reversed,
    "set": set,
    "sum": sum,
}
_DROPPED_BUILTINS = ("setattr", "delattr", "__build_class__")


class PluginError(Exception):
    """插件在求值期出了问题（超时、越权、算崩）。"""


class PluginExamples(NamedTuple):
    positive: tuple[Any, ...]
    negative: tuple[Any, ...]


class VocabPlugin(NamedTuple):
    """一个加载成功的词插件。"""

    name: str
    scope: str
    doc: str
    requires: tuple[str, ...]
    examples: PluginExamples
    source: str
    call: Callable[[Mapping[str, Any]], Any]


class PluginLoadError(NamedTuple):
    file: str
    errors: list[str]


# ── 受限执行环境 ────────────────────────────────────────────────────────────


def _deny_write(obj: Any) -> Any:
    """只允许改插件自己新建的容器，传进去的东西一律不准动。"""
    if isinstance(obj, (list, dict, set, bytearray)):
        return obj
    raise PluginError(f"插件不准修改 {type(obj).__name__} 对象")


def _inplacevar(op: str, left: Any, right: Any) -> Any:
    if op == "+=":
        return left + right
    if op == "-=":
        return left - right
    if op == "*=":
        return left * right
    if op == "/=":
        return left / right
    if op == "//=":
        return left // right
    if op == "%=":
        return left % right
    raise PluginError(f"插件不支持这个复合赋值运算符: {op}")


def _plugin_builtins() -> dict[str, Any]:
    builtins = {k: v for k, v in safe_builtins.items() if k not in _DROPPED_BUILTINS}
    builtins.update(_EXTRA_BUILTINS)
    return builtins


def _plugin_globals(helpers: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "__builtins__": _plugin_builtins(),
        "__metaclass__": type,
        "__name__": "vocab_plugin",
        "_getattr_": safer_getattr,
        "_getitem_": default_guarded_getitem,
        "_getiter_": default_guarded_getiter,
        "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
        "_unpack_sequence_": guarded_unpack_sequence,
        "_write_": _deny_write,
        "_inplacevar_": _inplacevar,
        **dict(helpers),
    }


# ── 加载 ────────────────────────────────────────────────────────────────────


def _static_errors(source: str, filename: str) -> list[str]:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [f"不是合法 Python: {exc.msg}（第 {exc.lineno} 行）"]
    errs: list[str] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        for kind, message in _FORBIDDEN_NODES:
            if isinstance(node, kind) and message not in seen:
                seen.add(message)
                errs.append(message)
    return errs


def _as_examples(raw: Any) -> tuple[PluginExamples | None, list[str]]:
    if not isinstance(raw, Mapping):
        return None, ["EXAMPLES 必须是 {'positive': [...], 'negative': [...]}"]
    pos = raw.get("positive") or ()
    neg = raw.get("negative") or ()
    errs: list[str] = []
    if not isinstance(pos, Sequence) or isinstance(pos, str) or not pos:
        errs.append("EXAMPLES['positive'] 必须是非空列表")
    if not isinstance(neg, Sequence) or isinstance(neg, str) or not neg:
        errs.append("EXAMPLES['negative'] 必须是非空列表（没有反例等于没测过误报）")
    if errs:
        return None, errs
    return PluginExamples(tuple(pos), tuple(neg)), []


def _metadata_errors(namespace: Mapping[str, Any]) -> list[str]:
    errs: list[str] = []
    name = str(namespace.get("NAME") or "").strip()
    if not _NAME_RE.fullmatch(name):
        errs.append("NAME 必须是小写下划线，例如 table_row_has_mixed_units")
    scope = str(namespace.get("SCOPE") or "block").strip()
    if scope not in _SCOPES:
        errs.append(f"SCOPE 只能是 {' 或 '.join(_SCOPES)}")
    if not str(namespace.get("DOC") or "").strip():
        errs.append("DOC 必填：没有中文释义，界面上那条判据没人读得懂")
    requires = namespace.get("REQUIRES")
    if not isinstance(requires, Sequence) or isinstance(requires, str) or not requires:
        errs.append("REQUIRES 必须是非空列表：声明什么字段才看得见什么字段")
    elif any(not isinstance(r, str) or not r.strip() for r in requires):
        errs.append("REQUIRES 里每一项都必须是字段名字符串")
    if not callable(namespace.get("evaluate")):
        errs.append("必须定义 def evaluate(fields)")
    return errs


def _make_call(
    name: str, fn: Callable[..., Any], source: str
) -> Callable[[Mapping[str, Any]], Any]:
    """包一层：只读入参、计时、连续超时就停用。"""
    strikes = {"n": 0}

    def call(fields: Mapping[str, Any]) -> Any:
        if strikes["n"] >= MAX_TIMEOUT_STRIKES:
            raise PluginError(f"词插件 {name} 连续超时 {strikes['n']} 次，已停用")
        started = time.perf_counter()
        try:
            value = fn(MappingProxyType(dict(fields)))
        except PluginError:
            raise
        except Exception as exc:  # noqa: BLE001 — 插件崩了只废这个词
            raise PluginError(f"词插件 {name} 求值失败: {exc}") from exc
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if elapsed_ms > CALL_TIME_LIMIT_MS:
            strikes["n"] += 1
            logger.warning(
                "vocab plugin %s (%s) took %.1fms (limit %.0fms), strike %d/%d",
                name, source, elapsed_ms, CALL_TIME_LIMIT_MS,
                strikes["n"], MAX_TIMEOUT_STRIKES,
            )
            raise PluginError(f"词插件 {name} 单次求值超过 {CALL_TIME_LIMIT_MS:.0f}ms")
        return value

    return call


def load_plugin_file(
    path: Path, helpers: Mapping[str, Any]
) -> tuple[VocabPlugin | None, list[str]]:
    """读一个插件文件。返回 (插件, 错误清单)，有错就不给插件。"""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [f"读不出文件: {exc}"]
    if len(source) > MAX_SOURCE_CHARS:
        return None, [f"插件源码超过 {MAX_SOURCE_CHARS} 字符，请拆小"]

    errs = _static_errors(source, path.name)
    if errs:
        return None, errs

    try:
        byte_code = compile_restricted(source, filename=path.name, mode="exec")
    except SyntaxError as exc:
        return None, [f"受限编译不通过: {exc.msg}（第 {exc.lineno} 行）"]

    namespace = _plugin_globals(helpers)
    try:
        exec(byte_code, namespace)  # noqa: S102 — 受限字节码 + 受限 globals
    except Exception as exc:  # noqa: BLE001
        return None, [f"插件加载时抛异常: {exc}"]

    errs = _metadata_errors(namespace)
    examples, example_errs = _as_examples(namespace.get("EXAMPLES"))
    errs.extend(example_errs)
    if errs or examples is None:
        return None, errs or ["EXAMPLES 解析失败"]

    name = str(namespace["NAME"]).strip()
    return (
        VocabPlugin(
            name=name,
            scope=str(namespace.get("SCOPE") or "block").strip(),
            doc=str(namespace["DOC"]).strip(),
            requires=tuple(str(r).strip() for r in namespace["REQUIRES"]),
            examples=examples,
            source=str(path),
            call=_make_call(name, namespace["evaluate"], path.name),
        ),
        [],
    )


def load_plugins(
    directory: Path, filenames: Iterable[str], helpers: Mapping[str, Any]
) -> tuple[tuple[VocabPlugin, ...], list[PluginLoadError]]:
    """加载 ``vocab_ext/`` 下的插件。

    只看目录顶层的 ``*.py``——``vocab_ext/proposals/`` 里的草稿因此永远不会被执行。
    一个插件坏掉只让它自己不加载，其余照跑；错误会经 ``load_errors`` 出现在界面上。
    """
    plugins: list[VocabPlugin] = []
    errors: list[PluginLoadError] = []
    seen: dict[str, str] = {}
    for filename in sorted(filenames):
        rel = f"{PLUGINS_DIRNAME}/{filename}"
        plugin, errs = load_plugin_file(directory / filename, helpers)
        if plugin is None:
            errors.append(PluginLoadError(file=rel, errors=errs))
            logger.warning("vocab plugin %s not loaded: %s", rel, "; ".join(errs))
            continue
        if plugin.name in seen:
            errors.append(
                PluginLoadError(
                    file=rel, errors=[f"词名重复: {plugin.name} 已由 {seen[plugin.name]} 定义"]
                )
            )
            continue
        seen[plugin.name] = rel
        plugins.append(plugin._replace(source=rel))
    return tuple(plugins), errors
