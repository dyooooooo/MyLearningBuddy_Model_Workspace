"""跨书症状检测器：声明加载、词表求值、模式映射。

分工是这个模块存在的理由：**骨架归人，内容归 Agent**。

Python 这一侧只做三件事——把 ``configs/checks/`` 读进来、给表达式准备一份词表、
把命中结果映射回模式库。症状本身（判据、证据、处置）**以及词本身**都写在挂载目录
里，由抽象轨的 Agent 产出。``configs/checks/`` 是 bind-mount 且在 Agent 写白名单内，
所以加一个症状、乃至加一个新词，都不需要改 Python、不需要重建镜像。

词表分四层，只有前两层在镜像里：

* **L0 事实字段**——``PageVocabulary`` 给出的一块一页原始事实（``zone``、``char_count``…）。
* **L1 算子**——``matches`` / ``any`` / ``lexicon`` 这类小而稳定的函数。
* **L2 配置词**——``vocabulary.yaml``，一条 = 名字 + 表达式 + 中文释义 + 双极性正反例。
* **L2b 词插件**——``vocab_ext/*.py``，受限 Python，给「一条表达式真写不出来」的原语。

L2 与 L2b 的正反例在**加载期**就跑一遍：跑不过的词直接不加载并进 ``load_errors``。
一个词声称自己在判什么、和它实际判什么，必须对得上，否则引用它的检测器就是在
拿一个谎言当前提。

检测器跑在**版式判定之后**：拿到的是最终 DocumentBlock（``meta.layout_zone`` 等
判定结果已经落在 meta 上）和渲染后的 HTML。所以词表能同时看见几何、判定来源和
渲染结果，这是 ``LayoutRule.when`` 做不到的——那一层只看得见单块的几何。

与 35B 线的 ``app.import_35b.caselaw`` 是同一套设计思路（症状 → 历史处置），但按
双线隔离原则**不共用代码**：那边是 PageBlueprint + Milvus 向量检索，这边是
DocumentBlock + 指纹精确匹配。
"""
from __future__ import annotations

import ast
import difflib
import logging
import re
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Literal, NamedTuple, Sequence

from omegaconf import OmegaConf
from pydantic import BaseModel, Field
from simpleeval import EvalWithCompoundTypes
from sqlalchemy.orm import Session

from app.db.models import (
    CorpusImprovementPattern,
    CorpusImprovementPoint,
    CorpusSymptomFalsePositive,
)
from app.services.document_ir import DocumentBlock
from app.services.improvement_points import LANDING_TARGETS
from app.services.ir.layout.rules import rule_expr_functions
from app.services.vocab_plugins import (
    PLUGINS_DIRNAME,
    PluginError,
    VocabPlugin,
    load_plugins,
)
from app.util.json_util import loads

logger = logging.getLogger(__name__)

_API_ROOT = Path(__file__).resolve().parents[2]
CHECKS_DIR = _API_ROOT / "configs" / "checks"
# 这三份和检测器同目录同挂载，但都不是检测器，加载时要单独拿出来。
FEATURES_FILE = "features.yaml"
VOCABULARY_FILE = "vocabulary.yaml"
LEXICONS_FILE = "lexicons.yaml"
_RESERVED_FILES = (FEATURES_FILE, VOCABULARY_FILE, LEXICONS_FILE)

Severity = Literal["high", "medium", "low"]
_SEVERITY_ORDER: tuple[str, ...] = ("high", "medium", "low")

_TEXT_PREVIEW_LIMIT = 60

_MATH_TEXT_RE = re.compile(r"\\text(?:rm|bf|it|sf|tt)?\s*\{([^{}]*)\}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 声明 schema ─────────────────────────────────────────────────────────────


class DetectSpec(BaseModel):
    scope: Literal["block", "page"] = "block"
    when: str
    min_hits: int = 1


class RemediationSpec(BaseModel):
    kind: Literal["rule", "playbook"] = "playbook"
    landing_target: str = ""
    rule_ref: str = ""
    playbook: str = ""
    # 修这条症状必须先看哪几项证据才能定位到「改哪里」。
    # 必须是 evidence 的子集：evidence 是「这条命中时顺手记下的字段」，
    # locate_by 是其中「不给就没法动手」的那几个——改进器拿到的就是这个子集。
    # 缺省等于整个 evidence；kind=playbook（手册，人/Agent 照着做）时不得为空。
    locate_by: list[str] = Field(default_factory=list)

    def locate_fields(self, evidence: Sequence[str]) -> list[str]:
        """实际用于定位的字段（缺省回落到整个 evidence，保序去重）。"""
        picked = list(self.locate_by or evidence)
        return list(dict.fromkeys(picked))


class ExamplesSpec(BaseModel):
    positive: list[str] = Field(default_factory=list)
    negative: list[str] = Field(default_factory=list)


class SymptomCheck(BaseModel):
    """一个症状检测器：一份 YAML 一个。"""

    suspect_type: str
    title: str = ""
    severity: Severity = "medium"
    enabled: bool = True
    detect: DetectSpec
    evidence: list[str] = Field(default_factory=list)
    remediation: RemediationSpec = Field(default_factory=RemediationSpec)
    examples: ExamplesSpec = Field(default_factory=ExamplesSpec)
    source: str = ""

    def validation_errors(self, derived: Iterable[str] = ()) -> list[str]:
        """声明本身的硬约束：跨书可用性 + 名字都在词表里。

        名字校验必须在**加载期**做。放到求值期就成了静默失效：``simpleeval``
        对词表外的名字抛 NameNotDefined，被逐页的兜底 except 记成一行 warning，
        这条检测器于是永远不命中，而清单里它还显示 enabled。
        """
        errs: list[str] = []
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", self.suspect_type):
            errs.append("suspect_type 必须是全大写下划线，例如 HEADER_IN_MAIN")
        target = self.remediation.landing_target
        if target and target not in LANDING_TARGETS:
            errs.append(f"landing_target 不在闭集里: {target}")
        if self.remediation.kind == "playbook" and not self.remediation.playbook.strip():
            errs.append("remediation.kind=playbook 时必须写 playbook 步骤")
        errs.extend(self._locate_by_errors())
        if self.detect.min_hits < 1:
            errs.append("min_hits 至少为 1")
        errs.extend(_cross_book_errors(self.detect.when))
        scope = self.detect.scope
        errs.extend(_name_errors(self.detect.when, scope=scope, derived=derived))
        # 证据取的是字段值，函数名放这里没有意义，所以这一处不放行函数。
        allowed = vocabulary_names(scope, derived=derived, functions=False)
        for field_name in self.evidence:
            if field_name in allowed:
                continue
            errs.append(f"evidence 里有词表外的名字 {field_name}{_did_you_mean(field_name, allowed)}")
        return errs

    def _locate_by_errors(self) -> list[str]:
        """locate_by 必须是 evidence 的子集；手册型修复必须给出定位依据。

        为什么要卡子集：locate_by 是给改进器（buddy-copilot）看的那一小撮字段，
        它只能从这条命中真的记下来的证据里挑。写了 evidence 里没有的名字，改进器
        拿到的就是空值——症状报了，但没人知道该改哪一块。
        """
        errs: list[str] = []
        evidence = set(self.evidence)
        outside = [f for f in self.remediation.locate_by if f not in evidence]
        if outside:
            errs.append(
                "remediation.locate_by 必须是 evidence 的子集，这些不在里面: "
                + ", ".join(outside)
            )
        if self.remediation.kind == "playbook" and not self.remediation.locate_fields(
            self.evidence
        ):
            errs.append(
                "remediation.kind=playbook 时 locate_by 不得为空"
                "（evidence 也空着的话请先补 evidence）：手册照着做的人得知道改哪一块"
            )
        return errs


def _cross_book_errors(expr: str, label: str = "when") -> list[str]:
    """挡住会让检测器只对一本书有效的写法。

    页码、块 ID、具体正文——写了这三样，换一本教材就失效，这条检测器也就没有
    存在的意义了。宁可在加载时报错，也不要让它安静地永远不命中。

    这条守卫按层分工，三种文件不是一个待遇：

    * **检测器的 ``when``、``vocabulary.yaml`` 的 ``expr``、``features.yaml``
      的表达式**——都过这道守卫，正文文字一律不准写死在表达式里。
    * **``lexicons.yaml``**——不过这道守卫。它就是为「一批中文词」准备的地方：
      排版惯例词、单位名、标点变体这些**不属于某一本书**的数据，本来就该集中
      成一份具名词表，由表达式用 ``lexicon('名字')`` 引用。代价是它必须有名字和
      中文释义（见 ``_parse_lexicons``），否则谁也说不清那份表收的是什么。
    """
    errs: list[str] = []
    comparison = r"(?:==|!=|<=|>=|<|>|\bin\b)"
    if re.search(rf"\bpage_no\s*{comparison}", expr):
        errs.append(f"{label} 里不准出现绝对页码（page_no 比较）")
    if re.search(rf"\b(?:key|block_id)\s*{comparison}", expr):
        errs.append(f"{label} 里不准出现块 ID")
    for m in re.finditer(r"""(["'])(.*?)\1""", expr):
        literal = m.group(2)
        if len(literal) >= 8 and re.search(r"[\u4e00-\u9fff]", literal):
            errs.append(f"{label} 里不准写死正文文字: {literal[:20]}")
    return errs


# ── 名字校验：词表是闭集，写错当场报错 ──────────────────────────────────────


def vocabulary_names(
    scope: str = "block", *, derived: Iterable[str] = (), functions: bool = True
) -> frozenset[str]:
    """某个 scope 下允许出现的全部名字。

    页级检测器看不到块级字段，所以两个 scope 的闭集不一样——``scope: page`` 的
    表达式里写 ``rel_y0`` 是错的，这里要能报出来。
    """
    names = set(_atomic_names(scope if scope == "page" else "block"))
    if functions:
        names |= _function_names()
    names |= {str(name) for name in derived}
    return frozenset(names)


@lru_cache(maxsize=4)
def _atomic_names(scope: str) -> frozenset[str]:
    """代码里写死的那部分词表（页级 + 块级字段），从同一份样本推导。

    单独抄一份名单迟早和 ``PageVocabulary`` 漂移，漂移的结果就是校验放行了一个
    其实不存在的名字，或者拦下一个明明可用的名字。
    """
    vocab = _sample_vocabulary()
    names = set(vocab.page_names)
    if scope != "page":
        names |= set(vocab.block_names(0))
    return frozenset(names)


@lru_cache(maxsize=1)
def _function_names() -> frozenset[str]:
    return frozenset(_sample_vocabulary().functions())


def _comprehension_targets(tree: ast.AST) -> set[str]:
    """列表推导式的循环变量。

    ``any([contains(text, m) for m in lexicon('math_markers')])`` 里的 ``m`` 是局部
    绑定，不是词表里的名字。不摘掉它，闭集校验会把它当拼写错误拦下来。
    """
    bound: set[str] = set()
    comprehensions = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
    for node in ast.walk(tree):
        if not isinstance(node, comprehensions):
            continue
        for generator in node.generators:
            for target in ast.walk(generator.target):
                if isinstance(target, ast.Name):
                    bound.add(target.id)
    return bound


def _referenced_names(tree: ast.AST) -> set[str]:
    """表达式引用到的词表名字（已摘掉推导式的局部变量）。"""
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    return names - _comprehension_targets(tree)


def _did_you_mean(name: str, allowed: Iterable[str]) -> str:
    # 给两个候选：``rel_yo`` 到 ``rel_y0`` 和 ``rel_y1`` 的距离一样，只给一个容易给错那个。
    near = difflib.get_close_matches(name, sorted(allowed), n=2, cutoff=0.7)
    return f"，是不是想写 {' 或 '.join(near)}？" if near else ""


def _name_errors(
    expr: str, *, scope: str, derived: Iterable[str] = (), label: str = "when"
) -> list[str]:
    """表达式里出现的名字必须都在词表内。拼错一个字母就拦在这里。"""
    text = (expr or "").strip()
    if not text:
        return [f"{label} 不能为空"]
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        return [f"{label} 不是合法表达式: {exc.msg}"]
    allowed = vocabulary_names(scope, derived=derived)
    errs: list[str] = []
    for name in sorted(_referenced_names(tree)):
        if name in allowed:
            continue
        hint = _did_you_mean(name, allowed)
        if not hint and name in _atomic_names("block"):
            hint = "，这是块级字段，scope: page 用不了"
        errs.append(f"{label} 里有词表外的名字 {name}{hint}")
    return errs


# ── 派生特征：用原子词组合出的命名特征，写在 YAML 里 ────────────────────────


class DerivedFeature(NamedTuple):
    """``features.yaml`` 里的一条：一个名字 + 一段用原子词写的表达式。

    ``node`` 是 ``simpleeval`` 认的那种已解析节点，加载时解析一次，之后每个块复用，
    省掉逐块重新 parse。
    """

    name: str
    expr: str
    node: ast.AST
    page_safe: bool


class WordExamples(NamedTuple):
    """一个词的双极性样例。正例必须命中、反例必须不命中，加载期就验。"""

    positive: tuple[Any, ...]
    negative: tuple[Any, ...]


class Lexicon(NamedTuple):
    """``lexicons.yaml`` 里的一份命名词表（排版惯例、单位名、标点变体…）。

    这些数据本来写死在 Python 里。搬出来的理由不只是免重建：跨书守卫禁止在表达式
    里硬写长串中文，所以一批例外词**只能**以命名词表的形式存在，检测器与配置词通过
    ``lexicon('math_label_words')`` 引用它。
    """

    name: str
    doc: str
    words: tuple[str, ...]


class ConfigWord(NamedTuple):
    """一个挂载目录里定义的词：``vocabulary.yaml`` 的一条，或一个 ``vocab_ext`` 插件。

    两种来源共用一个类型，是为了让求值侧只有一条分支——``_LazyNames`` 不必关心
    这个名字是表达式算出来的还是插件算出来的。
    """

    name: str
    scope: str  # block | page
    doc: str
    expr: str  # 配置词：表达式原文；插件词：空串
    node: ast.AST | None
    requires: tuple[str, ...]
    source: str
    examples: WordExamples
    plugin: VocabPlugin | None = None

    @property
    def kind(self) -> str:
        return "plugin" if self.plugin is not None else "config"

    @property
    def page_safe(self) -> bool:
        return self.scope == "page"


class LoadError(BaseModel):
    """一份没能生效的声明，以及为什么。"""

    file: str
    errors: list[str] = Field(default_factory=list)


class LoadedChecks(NamedTuple):
    checks: tuple[SymptomCheck, ...]
    features: tuple[DerivedFeature, ...]
    errors: tuple[LoadError, ...]
    words: tuple[ConfigWord, ...] = ()
    lexicons: Mapping[str, Lexicon] = MappingProxyType({})


def _feature_refs(node: ast.AST) -> set[str]:
    return _referenced_names(node)


def _parse_features(
    raw: dict[str, Any], *, words: Sequence[ConfigWord] = ()
) -> tuple[tuple[DerivedFeature, ...], list[str]]:
    """把 ``features:`` 映射解析成可求值的派生特征，并挡掉三类错误写法。

    名字撞原子词、引用不存在的名字、互相引用成环——这三样都必须在加载期拦下：
    环会让求值递归到栈溢出，另外两样会让引用它的检测器静默失效。
    """
    errs: list[str] = []
    parsed: dict[str, tuple[str, ast.AST]] = {}
    atomic = _atomic_names("block") | _function_names() | {w.name for w in words}

    for name, expr in raw.items():
        label = f"features.{name}"
        key = str(name)
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", key):
            errs.append(f"{label} 名字必须是小写下划线，例如 is_narrow_right_figure")
            continue
        if key in atomic:
            errs.append(f"{label} 和原子词重名，换一个名字")
            continue
        if not isinstance(expr, str) or not expr.strip():
            errs.append(f"{label} 必须是一段非空表达式")
            continue
        try:
            # mode="eval" 顺带挡掉赋值、多语句这类不是表达式的写法。
            ast.parse(expr.strip(), mode="eval")
            node = EvalWithCompoundTypes.parse(expr.strip())
        except SyntaxError as exc:
            errs.append(f"{label} 不是合法表达式: {exc.msg}")
            continue
        errs.extend(_cross_book_errors(expr, label))
        parsed[key] = (expr.strip(), node)

    # 引用的名字必须存在：原子词、函数，或另一条派生特征。
    known = atomic | set(parsed)
    for key in list(parsed):
        _expr, node = parsed[key]
        unknown = sorted(_feature_refs(node) - known)
        if unknown:
            for bad in unknown:
                errs.append(
                    f"features.{key} 引用了不存在的名字 {bad}{_did_you_mean(bad, known)}"
                )
            del parsed[key]

    for cycle in _feature_cycles(parsed):
        errs.append("features 里存在循环引用: " + " → ".join(cycle))
        for key in cycle[:-1]:
            parsed.pop(key, None)

    page_only = (
        _atomic_names("page")
        | _function_names()
        | {w.name for w in words if w.page_safe}
    )
    features = [
        DerivedFeature(
            name=key,
            expr=expr,
            node=node,
            page_safe=_page_safe(key, parsed, page_only, set()),
        )
        for key, (expr, node) in sorted(parsed.items())
    ]
    return tuple(features), errs


def _page_safe(
    key: str,
    parsed: dict[str, tuple[str, ast.AST]],
    page_only: frozenset[str],
    seen: set[str],
) -> bool:
    """这条派生特征能不能在 ``scope: page`` 的检测器里用。

    引用了块级字段（自己引，或者顺着别的派生特征引到）就不能——页级求值时根本
    没有块，用了就是必然失效。
    """
    if key in seen:
        return False
    seen.add(key)
    _expr, node = parsed[key]
    for ref in _feature_refs(node):
        if ref in page_only:
            continue
        if ref in parsed:
            if not _page_safe(ref, parsed, page_only, seen):
                return False
            continue
        return False
    return True


def _feature_cycles(parsed: dict[str, tuple[str, ast.AST]]) -> list[list[str]]:
    """返回每个环的一条路径，例如 ``[a, b, a]``。"""
    graph = {k: sorted(_feature_refs(n) & set(parsed)) for k, (_e, n) in parsed.items()}
    cycles: list[list[str]] = []
    state: dict[str, int] = {}

    def walk(node: str, path: list[str]) -> None:
        state[node] = 1
        for nxt in graph.get(node, ()):
            if state.get(nxt) == 1:
                cycles.append(path[path.index(nxt) :] + [nxt])
            elif state.get(nxt, 0) == 0:
                walk(nxt, path + [nxt])
        state[node] = 2

    for node in sorted(graph):
        if state.get(node, 0) == 0:
            walk(node, [node])
    return cycles


def _derived_for_scope(features: Sequence[DerivedFeature], scope: str) -> tuple[str, ...]:
    if scope == "page":
        return tuple(f.name for f in features if f.page_safe)
    return tuple(f.name for f in features)


def _extra_names(
    words: Sequence[ConfigWord], features: Sequence[DerivedFeature], scope: str
) -> tuple[str, ...]:
    """L0 之外、这个 scope 下还能用的名字：配置词、词插件、派生特征。

    检测器校验只认一个「闭集」，所以这三类必须合成一份名单交给
    ``vocabulary_names``——漏掉哪一类，那一类就会被当成拼写错误拦下来。
    """
    names = [w.name for w in words if scope != "page" or w.page_safe]
    names += list(_derived_for_scope(features, scope))
    return tuple(names)


# ── 词表数据与配置词：词也住在挂载目录里 ─────────────────────────────────────


def _lexicon_refs(node: ast.AST) -> tuple[set[str], list[str]]:
    """表达式里 ``lexicon('x')`` 引用到的词表名，外加不合法的写法。

    只认字面量：``lexicon(变量)`` 没法在加载期校验，也就没法保证「引用了一份不存在
    的词表」当场报错——而那正是最难查的一种坏（词永远不命中，界面上却显示正常）。
    """
    names: set[str] = set()
    errs: list[str] = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        if not isinstance(call.func, ast.Name) or call.func.id != "lexicon":
            continue
        arg = call.args[0] if len(call.args) == 1 else None
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
            errs.append("lexicon() 的参数必须是字面量，例如 lexicon('math_label_words')")
            continue
        names.add(arg.value)
    return names, errs


def _parse_lexicons(raw: Any) -> tuple[dict[str, Lexicon], list[str]]:
    if not isinstance(raw, dict):
        return {}, ["lexicons: 必须是「名字: 声明」映射"]
    out: dict[str, Lexicon] = {}
    errs: list[str] = []
    for name, spec in raw.items():
        key = str(name)
        label = f"lexicons.{key}"
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", key):
            errs.append(f"{label} 名字必须是小写下划线，例如 math_label_words")
            continue
        if not isinstance(spec, dict):
            errs.append(f"{label} 必须是映射，至少要有 doc 和 words")
            continue
        doc = str(spec.get("doc") or "").strip()
        if not doc:
            errs.append(f"{label} 缺 doc：一份词表不说清收的是什么，没人敢往里加词")
            continue
        words = spec.get("words")
        if not isinstance(words, list) or not words:
            errs.append(f"{label} 的 words 必须是非空列表")
            continue
        if any(not isinstance(w, str) or not w for w in words):
            errs.append(f"{label} 的 words 里每一项都必须是非空字符串")
            continue
        out[key] = Lexicon(name=key, doc=doc, words=tuple(str(w) for w in words))
    return out, errs


def _parse_word_examples(raw: Any, label: str) -> tuple[WordExamples | None, list[str]]:
    if not isinstance(raw, dict):
        return None, [f"{label} 缺 examples：没有正反例的词等于没验证过"]
    errs: list[str] = []
    pos = raw.get("positive")
    neg = raw.get("negative")
    if not isinstance(pos, list) or not pos:
        errs.append(f"{label} 的 examples.positive 必须是非空列表")
    if not isinstance(neg, list) or not neg:
        errs.append(f"{label} 的 examples.negative 必须是非空列表（没有反例等于没测过误报）")
    if errs:
        return None, errs
    return WordExamples(tuple(pos or ()), tuple(neg or ())), []


def _parse_words(
    raw: Any,
    *,
    lexicons: Mapping[str, Lexicon],
    plugins: Sequence[VocabPlugin] = (),
) -> tuple[tuple[ConfigWord, ...], list[str]]:
    """把 ``vocabulary.yaml`` 的 ``words:`` 解析成可求值的配置词。

    比派生特征严一档：这里出任何一条错，这个词直接不加载。词是检测器的前提，
    一个半对的前提比没有前提更难查。
    """
    if not isinstance(raw, dict):
        return (), ["words: 必须是「名字: 声明」映射"]

    errs: list[str] = []
    reserved = _atomic_names("block") | _function_names()
    plugin_names = {p.name for p in plugins}
    staged: dict[str, dict[str, Any]] = {}

    for name, spec in raw.items():
        key = str(name)
        label = f"words.{key}"
        local: list[str] = []
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", key):
            errs.append(f"{label} 名字必须是小写下划线，例如 cjk_text_in_math")
            continue
        if key in reserved:
            errs.append(f"{label} 和 L0 字段或算子重名，换一个名字")
            continue
        if key in plugin_names:
            errs.append(f"{label} 和词插件同名，换一个名字")
            continue
        if not isinstance(spec, dict):
            errs.append(f"{label} 必须是映射，至少要有 doc、expr、examples")
            continue

        scope = str(spec.get("scope") or "block")
        if scope not in ("block", "page"):
            local.append(f"{label} 的 scope 只能是 block 或 page")
        doc = str(spec.get("doc") or "").strip()
        if not doc:
            local.append(f"{label} 缺 doc（中文释义）：界面上那条判据要给人读")
        expr = spec.get("expr")
        node: ast.AST | None = None
        if not isinstance(expr, str) or not expr.strip():
            local.append(f"{label} 的 expr 必须是一段非空表达式")
        else:
            try:
                # mode="eval" 顺带挡掉赋值、多语句这类不是表达式的写法。
                ast.parse(expr.strip(), mode="eval")
                node = EvalWithCompoundTypes.parse(expr.strip())
            except SyntaxError as exc:
                local.append(f"{label} 不是合法表达式: {exc.msg}")
        if node is not None:
            local.extend(_cross_book_errors(str(expr), label))
            lex_names, lex_errs = _lexicon_refs(node)
            local.extend(f"{label}: {m}" for m in lex_errs)
            missing = sorted(lex_names - set(lexicons))
            if missing:
                local.append(f"{label} 引用了不存在的词表: {', '.join(missing)}")
        examples, ex_errs = _parse_word_examples(spec.get("examples"), label)
        local.extend(ex_errs)

        if local or node is None or examples is None:
            errs.extend(local)
            continue
        staged[key] = {
            "scope": scope,
            "doc": doc,
            "expr": str(expr).strip(),
            "node": node,
            "examples": examples,
        }

    # 引用的名字必须存在：L0 字段、算子、词插件，或另一个配置词。
    known = reserved | plugin_names | set(staged)
    for key in list(staged):
        unknown = sorted(_feature_refs(staged[key]["node"]) - known)
        if unknown:
            for bad in unknown:
                errs.append(
                    f"words.{key} 引用了不存在的名字 {bad}{_did_you_mean(bad, known)}"
                )
            del staged[key]

    for cycle in _feature_cycles({k: (v["expr"], v["node"]) for k, v in staged.items()}):
        errs.append("words 里存在循环引用: " + " → ".join(cycle))
        for key in cycle[:-1]:
            staged.pop(key, None)

    page_ok = (
        _atomic_names("page")
        | _function_names()
        | {k for k, v in staged.items() if v["scope"] == "page"}
        | {p.name for p in plugins if p.scope == "page"}
    )
    for key in list(staged):
        if staged[key]["scope"] != "page":
            continue
        bad = sorted(_feature_refs(staged[key]["node"]) - page_ok)
        if bad:
            errs.append(
                f"words.{key} 声明 scope: page，却引用了块级名字 {', '.join(bad)}"
            )
            del staged[key]

    words = tuple(
        ConfigWord(
            name=key,
            scope=str(spec["scope"]),
            doc=str(spec["doc"]),
            expr=str(spec["expr"]),
            node=spec["node"],
            requires=tuple(sorted(_feature_refs(spec["node"]) - _function_names())),
            source=VOCABULARY_FILE,
            examples=spec["examples"],
        )
        for key, spec in sorted(staged.items())
    )
    return words, errs


def _words_from_plugins(plugins: Sequence[VocabPlugin]) -> tuple[ConfigWord, ...]:
    return tuple(
        ConfigWord(
            name=p.name,
            scope=p.scope,
            doc=p.doc,
            expr="",
            node=None,
            requires=tuple(p.requires),
            source=p.source,
            examples=WordExamples(p.examples.positive, p.examples.negative),
            plugin=p,
        )
        for p in plugins
    )


# ── 词的自证：正反例在加载期跑一遍，跑不过就不上线 ───────────────────────────

_EXAMPLE_BLOCK_FIELDS = ("type", "text", "caption", "level", "bbox", "meta", "table_html")
_EXAMPLE_HTML = '<article><section class="lb-main"></section></article>'


def _example_block(index: int, raw: Any) -> DocumentBlock:
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, Mapping):
        raise ValueError("样例块必须是字符串或字段映射")
    unknown = sorted(set(raw) - set(_EXAMPLE_BLOCK_FIELDS))
    if unknown:
        raise ValueError(f"样例块里有不认识的字段: {', '.join(unknown)}")
    fields = {k: v for k, v in raw.items() if k in _EXAMPLE_BLOCK_FIELDS}
    fields.setdefault("type", "paragraph")
    fields.setdefault("bbox", [0.0, 0.0, 500.0, 40.0])
    return DocumentBlock(id=f"ex{index + 1}", page_no=1, order=index + 1, **fields)


def _example_page(spec: Any) -> tuple[list[DocumentBlock], str]:
    """把一条样例摊成一页。

    最常见的写法就是一个字符串——那是块的正文。要造多块或页级样例，写
    ``{blocks: [...], html: "..."}``。
    """
    if isinstance(spec, str):
        raw_blocks: list[Any] = [{"text": spec}]
        html = ""
    elif isinstance(spec, Mapping) and "blocks" in spec:
        raw_blocks = list(spec.get("blocks") or [])
        html = str(spec.get("html") or "")
    elif isinstance(spec, Mapping):
        raw_blocks = [dict(spec)]
        html = ""
    else:
        raise ValueError("样例必须是字符串、块字段映射，或 {blocks: [...]}")
    blocks = [_example_block(i, raw) for i, raw in enumerate(raw_blocks)]
    return blocks, html or _EXAMPLE_HTML


def _eval_word(
    word: ConfigWord,
    spec: Any,
    *,
    words: Mapping[str, ConfigWord],
    lexicons: Mapping[str, Lexicon],
) -> Any:
    blocks, html = _example_page(spec)
    vocab = PageVocabulary(blocks, html, 1)
    base = dict(vocab.page_names)
    if word.scope != "page":
        if not blocks:
            raise ValueError("块级词的样例至少要有一个块")
        base.update(vocab.block_names(0))
    names = _LazyNames(base, {}, vocab.functions(lexicons), words=words)
    return names[word.name]


def _word_example_errors(
    words: Sequence[ConfigWord],
    lexicons: Mapping[str, Lexicon],
    *,
    only: Iterable[str] | None = None,
) -> dict[str, list[str]]:
    """跑每个词的正反例，返回 {词名: 错误清单}。

    这是这一层唯一的正确性保证：词的释义说它在判什么，正反例证明它真的在判那个。
    对不上就不加载——引用它的检测器于是当场报错，而不是拿一个谎言当前提。

    ``only`` 只筛**要验哪些词**，``words`` 仍需给全：一个词可能引用另一个词，
    求值时缺了被引用的那个就成了「依赖没配齐」而不是「这个词算错了」。
    """
    word_map = {w.name: w for w in words}
    targets = set(only) if only is not None else None
    out: dict[str, list[str]] = {}
    for word in words:
        if targets is not None and word.name not in targets:
            continue
        errs: list[str] = []
        cases = (
            ("正例", word.examples.positive, True),
            ("反例", word.examples.negative, False),
        )
        for polarity, specs, expected in cases:
            for i, spec in enumerate(specs, 1):
                try:
                    value = bool(
                        _eval_word(word, spec, words=word_map, lexicons=lexicons)
                    )
                except Exception as exc:  # noqa: BLE001 — 算不出来也是不合格
                    errs.append(f"{polarity} 第 {i} 条求值失败: {exc}")
                    continue
                if value is not expected:
                    errs.append(
                        f"{polarity} 第 {i} 条应当"
                        f"{'命中' if expected else '不命中'}，实际相反"
                    )
        if errs:
            out[word.name] = errs
    return out


# ── 加载（mtime 缓存，改完即时生效）────────────────────────────────────────


def _dir_stamp(directory: Path) -> tuple[tuple[str, float], ...]:
    """目录指纹：文件名 + mtime。指纹一变，下面那层 ``lru_cache`` 自然失效。

    词插件按 ``vocab_ext/xxx.py`` 记名，且**只看该目录顶层**——所以
    ``vocab_ext/proposals/`` 里没过审的草稿既不进指纹，也不会被执行。
    """
    paths: list[tuple[str, Path]] = []
    try:
        for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
            paths.append((path.name, path))
        for path in sorted((directory / PLUGINS_DIRNAME).glob("*.py")):
            paths.append((f"{PLUGINS_DIRNAME}/{path.name}", path))
    except OSError:
        return ()
    out: list[tuple[str, float]] = []
    for name, path in paths:
        try:
            out.append((name, path.stat().st_mtime))
        except OSError:
            continue
    return tuple(out)


@lru_cache(maxsize=8)
def _load_all_cached(
    directory: str, _stamp: tuple[tuple[str, float], ...]
) -> LoadedChecks:
    root = Path(directory)
    errors: list[LoadError] = []
    names = {name for name, _m in _stamp}

    # 顺序是有讲究的：词表数据 → 词插件 → 配置词 → 派生特征 → 检测器。
    # 每一层都可能引用前一层的名字，反过来不行。
    lexicons: dict[str, Lexicon] = {}
    if LEXICONS_FILE in names:
        lexicons, lexicon_errs = _load_lexicons_file(root / LEXICONS_FILE)
        if lexicon_errs:
            errors.append(LoadError(file=LEXICONS_FILE, errors=lexicon_errs))

    plugin_files = [
        name.split("/", 1)[1]
        for name in sorted(names)
        if name.startswith(f"{PLUGINS_DIRNAME}/")
    ]
    plugins, plugin_errs = load_plugins(
        root / PLUGINS_DIRNAME, plugin_files, symptom_expr_functions(lexicons)
    )
    errors.extend(LoadError(file=e.file, errors=list(e.errors)) for e in plugin_errs)

    words = _words_from_plugins(plugins)
    vocabulary_errs: list[str] = []
    if VOCABULARY_FILE in names:
        config_words, vocabulary_errs = _load_vocabulary_file(
            root / VOCABULARY_FILE, lexicons=lexicons, plugins=plugins
        )
        words = words + config_words

    words, example_errs = _certify_words(words, lexicons)
    for source, messages in example_errs:
        if source == VOCABULARY_FILE:
            vocabulary_errs.extend(messages)
        else:
            errors.append(LoadError(file=source, errors=messages))
    if vocabulary_errs:
        errors.append(LoadError(file=VOCABULARY_FILE, errors=vocabulary_errs))

    features: tuple[DerivedFeature, ...] = ()
    if FEATURES_FILE in names:
        features, feature_errs = _load_features_file(root / FEATURES_FILE, words=words)
        if feature_errs:
            errors.append(LoadError(file=FEATURES_FILE, errors=feature_errs))

    checks: list[SymptomCheck] = []
    seen: set[str] = set()
    for name, _mtime in _stamp:
        # 保留文件不是检测器；带 "/" 的是词插件，已经在上面加载过了。
        if name in _RESERVED_FILES or "/" in name:
            continue
        path = root / name
        try:
            data = OmegaConf.to_container(OmegaConf.load(path), resolve=False)
        except Exception as exc:  # noqa: BLE001 — 一份坏声明不能拖垮整次导入
            errors.append(LoadError(file=name, errors=[f"YAML 解析失败: {exc}"]))
            logger.warning("symptom check: cannot parse %s: %s", path, exc)
            continue
        if not isinstance(data, dict):
            errors.append(LoadError(file=name, errors=["检测器声明必须是一个 YAML 映射"]))
            continue
        try:
            check = SymptomCheck.model_validate({**data, "source": str(path)})
        except Exception as exc:  # noqa: BLE001
            errors.append(LoadError(file=name, errors=[f"声明不合 schema: {exc}"]))
            logger.warning("symptom check: invalid declaration in %s: %s", path, exc)
            continue
        errs = check.validation_errors(
            _extra_names(words, features, check.detect.scope)
        )
        if errs:
            errors.append(LoadError(file=name, errors=errs))
            logger.warning("symptom check %s disabled: %s", path.name, "; ".join(errs))
            continue
        if check.suspect_type in seen:
            errors.append(
                LoadError(file=name, errors=[f"症状名重复: {check.suspect_type}"])
            )
            logger.warning(
                "symptom check: duplicate suspect_type %s in %s, skipped",
                check.suspect_type,
                path.name,
            )
            continue
        seen.add(check.suspect_type)
        checks.append(check)
    return LoadedChecks(
        tuple(checks),
        features,
        tuple(errors),
        words,
        MappingProxyType(dict(lexicons)),
    )


def _certify_words(
    words: Sequence[ConfigWord], lexicons: Mapping[str, Lexicon]
) -> tuple[tuple[ConfigWord, ...], list[tuple[str, list[str]]]]:
    """把正反例跑不过的词剔掉，并按来源文件归拢错误原因。"""
    bad = _word_example_errors(words, lexicons)
    if not bad:
        return tuple(words), []
    by_source: dict[str, list[str]] = {}
    for word in words:
        messages = bad.get(word.name)
        if not messages:
            continue
        by_source.setdefault(word.source, []).extend(
            f"{word.name}: {m}" for m in messages
        )
        logger.warning(
            "vocabulary word %s (%s) failed its own examples: %s",
            word.name, word.source, "; ".join(messages),
        )
    kept = tuple(w for w in words if w.name not in bad)
    return kept, sorted(by_source.items())


def _load_lexicons_file(path: Path) -> tuple[dict[str, Lexicon], list[str]]:
    data, err = _load_mapping(path, LEXICONS_FILE)
    if err:
        return {}, [err]
    raw = (data or {}).get("lexicons")
    if raw is None:
        return {}, [f"{LEXICONS_FILE} 缺少顶层 lexicons: 映射"]
    lexicons, errs = _parse_lexicons(raw)
    if errs:
        logger.warning("lexicons in %s: %s", path.name, "; ".join(errs))
    return lexicons, errs


def _load_vocabulary_file(
    path: Path,
    *,
    lexicons: Mapping[str, Lexicon],
    plugins: Sequence[VocabPlugin],
) -> tuple[tuple[ConfigWord, ...], list[str]]:
    data, err = _load_mapping(path, VOCABULARY_FILE)
    if err:
        return (), [err]
    raw = (data or {}).get("words")
    if raw is None:
        return (), [f"{VOCABULARY_FILE} 缺少顶层 words: 映射"]
    words, errs = _parse_words(raw, lexicons=lexicons, plugins=plugins)
    if errs:
        logger.warning("vocabulary in %s: %s", path.name, "; ".join(errs))
    return words, errs


def _load_mapping(path: Path, label: str) -> tuple[dict[str, Any] | None, str]:
    try:
        data = OmegaConf.to_container(OmegaConf.load(path), resolve=False)
    except Exception as exc:  # noqa: BLE001 — 一份坏声明不能拖垮整次导入
        logger.warning("%s: cannot parse %s: %s", label, path, exc)
        return None, f"YAML 解析失败: {exc}"
    if not isinstance(data, dict):
        return None, f"{label} 必须是一个 YAML 映射"
    return data, ""


def _load_features_file(
    path: Path, *, words: Sequence[ConfigWord] = ()
) -> tuple[tuple[DerivedFeature, ...], list[str]]:
    data, err = _load_mapping(path, FEATURES_FILE)
    if err:
        return (), [err]
    raw = (data or {}).get("features")
    if raw is None:
        return (), ["features.yaml 缺少顶层 features: 映射"]
    if not isinstance(raw, dict):
        return (), ["features: 必须是「名字: 表达式」映射"]
    features, errs = _parse_features(raw, words=words)
    if errs:
        logger.warning("derived features in %s: %s", path.name, "; ".join(errs))
    return features, errs


def _loaded(directory: Path | None = None) -> LoadedChecks:
    root = directory or CHECKS_DIR
    return _load_all_cached(str(root), _dir_stamp(root))


def load_checks(directory: Path | None = None) -> tuple[SymptomCheck, ...]:
    """当前生效的全部检测器。目录是挂载的，保存后下一页就生效。"""
    return _loaded(directory).checks


def load_features(directory: Path | None = None) -> tuple[DerivedFeature, ...]:
    """当前生效的派生特征。"""
    return _loaded(directory).features


def load_words(directory: Path | None = None) -> tuple[ConfigWord, ...]:
    """当前生效的配置词与词插件。"""
    return _loaded(directory).words


def load_lexicons(directory: Path | None = None) -> Mapping[str, Lexicon]:
    """当前生效的命名词表。"""
    return _loaded(directory).lexicons


def load_errors(directory: Path | None = None) -> tuple[LoadError, ...]:
    """没能生效的声明和原因。

    只写日志等于没人看得见：一条检测器悄悄从清单里消失，界面上和没写过一模一样。
    这份清单会经 ``checks_overview`` 出现在 ``GET /checks`` 里。
    """
    return _loaded(directory).errors


def clear_checks_cache() -> None:
    _load_all_cached.cache_clear()
    _atomic_names.cache_clear()
    _function_names.cache_clear()


def check_for_suspect(
    suspect_type: str, directory: Path | None = None
) -> SymptomCheck | None:
    suspect = (suspect_type or "").strip().upper()
    if not suspect:
        return None
    for check in load_checks(directory):
        if check.suspect_type == suspect:
            return check
    return None


def resolve_locate_by(suspect_type: str, directory: Path | None = None) -> list[str]:
    """这个症状当前声明的定位字段。

    和 ``resolve_playbook`` 一个道理：解析放在读取侧。老任务入库时还没有
    ``locate_by``，改了 YAML 也不必回写数据库，下一次读就按新声明给。
    """
    check = check_for_suspect(suspect_type, directory)
    if check is None:
        return []
    return check.remediation.locate_fields(check.evidence)


def resolve_playbook(
    ref: str = "", *, suspect_type: str = "", directory: Path | None = None
) -> str:
    """把 ``playbook_ref`` 指针解析成 YAML 里的手册步骤全文。

    模式库里存的是 ``configs/checks/x.yaml#remediation.playbook`` 这样的**指针**，
    步骤真身在 YAML 里。解析放在读取侧，改 YAML 下一次读就生效，不必回写数据库，
    也就不存在两处步骤不一致的问题。

    没绑检测器的模式，``ref`` 里存的往往就是步骤原文（抽象轨早期写法），这种原样
    带回。指针指向的检测器不存在时返回空串——宁可显示「没有手册」，也不要把一个
    路径当步骤发给 Agent。
    """
    ref = (ref or "").strip()
    check = check_for_suspect(suspect_type, directory)
    if check is None and ref:
        name = Path(ref.split("#", 1)[0]).name
        if name.endswith((".yaml", ".yml")):
            for candidate in load_checks(directory):
                if Path(candidate.source).name == name:
                    check = candidate
                    break
    if check is not None:
        text = check.remediation.playbook.strip()
        if text:
            return text
    if ref and not _looks_like_pointer(ref):  # 老数据：ref 里存的就是步骤本身
        return ref
    builtin = _BUILTIN_PLAYBOOKS.get((suspect_type or "").strip().upper())
    if builtin:
        return builtin
    return ""


# 硬编码四条基础检查没有 YAML 检测器，但自动改必须有步骤，否则会进「未知」被跳过。
_BUILTIN_PLAYBOOKS: dict[str, str] = {
    "MATH_FALLBACK": (
        "1. `corpus.page_html` 确认还有 `class=\"math math--fallback\"`。\n"
        "2. `corpus.get_blocks` 取该块 text / latex。\n"
        "3. 修公式：中文拆出、补环境、去掉损坏宏。\n"
        "4. `corpus.patch_block` 写回，再 `corpus.rerender_page`。\n"
        "5. 再看 HTML：没有 fallback 才算修掉。修不了或其实不是公式 → "
        "`checks.report_false_positive`。"
    ),
    "MAIN_EMPTY": (
        "1. `corpus.get_blocks` 看本页 zone / skip_render / blank。\n"
        "2. 主栏块被标成侧栏或 skip → 写 page override 或 layout_rules，再 "
        "`corpus.rebuild_ir`。\n"
        "3. 块文本本身空 → 对照 PDF；MinerU 漏检记 issue，不要空改。"
    ),
    "PAGE_EMPTY": (
        "1. 对照 PDF 是否真是空白页。\n"
        "2. 真空白：`checks.report_false_positive`。\n"
        "3. 有字却空：查页范围截断 / MinerU 漏检，记 issue，不要空改。"
    ),
    "CONTROL_CHAR_RESIDUE": (
        "1. `corpus.get_blocks` 看 preview 里的控制字符。\n"
        "2. `corpus.patch_block` 删掉后 `corpus.rerender_page`。\n"
        "3. 删不干净 → 记 issue。"
    ),
}


def finding_has_fix_path(finding: dict[str, Any]) -> bool:
    """这条命中有没有现成处置办法（手册或落点）。

    旧逻辑用 ``pattern_id`` 当「已知」：第一本教材上，YAML 里已经有手册的
    症状也会被当成未知，Agent 看不到步骤。手册在检测器里，不在模式库里。
    """
    if finding.get("playbook") or finding.get("landing_target"):
        return True
    if str(finding.get("remediation_kind") or "") in {"rule", "playbook"}:
        return True
    suspect = str(finding.get("suspect_type") or "")
    return bool(
        resolve_playbook(str(finding.get("playbook_ref") or ""), suspect_type=suspect)
    )


def attach_read_side_playbook(finding: dict[str, Any]) -> dict[str, Any]:
    """读取侧补手册和定位字段。无 pattern 的声明式症状也能拿到步骤。"""
    suspect = str(finding.get("suspect_type") or "")
    if not finding.get("locate_by"):
        fields = resolve_locate_by(suspect)
        if fields:
            finding["locate_by"] = fields
    if not finding.get("playbook"):
        steps = resolve_playbook(
            str(finding.get("playbook_ref") or ""),
            suspect_type=suspect,
        )
        if steps:
            finding["playbook"] = steps
            if not finding.get("remediation_kind"):
                finding["remediation_kind"] = "playbook"
    return finding


def partition_findings(
    findings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按「有没有处置办法」切开。读取侧会补手册。"""
    known: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for raw in findings:
        if not isinstance(raw, dict):
            continue
        finding = attach_read_side_playbook(dict(raw))
        if finding_has_fix_path(finding):
            known.append(finding)
        else:
            unknown.append(finding)
    return known, unknown


def _looks_like_pointer(ref: str) -> bool:
    return bool(re.search(r"\.ya?ml(?:$|#|\s)", ref))


def load_check_source(
    text: str, *, source: str = "<inline>", directory: Path | None = None
) -> SymptomCheck:
    """从 YAML 文本解析一条检测器（试跑用，不落盘）。

    校验和正式加载走同一条路径，所以 Agent 在试跑时就会看到「词表里没有这个
    名字」，而不是发布之后才发现它永远不命中。
    """
    data = OmegaConf.to_container(OmegaConf.create(text), resolve=False)
    if not isinstance(data, dict):
        raise ValueError("检测器声明必须是一个 YAML 映射")
    check = SymptomCheck.model_validate({**data, "source": source})
    loaded = _loaded(directory)
    errs = check.validation_errors(
        _extra_names(loaded.words, loaded.features, check.detect.scope)
    )
    if errs:
        raise ValueError("; ".join(errs))
    return check


# ── 词表 ────────────────────────────────────────────────────────────────────


def _bbox(block: DocumentBlock) -> tuple[float, float, float, float] | None:
    box = block.bbox
    if not box or len(box) < 4:
        return None
    x0, y0, x1, y1 = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return x0, y0, x1, y1


def _norm_text(block: DocumentBlock) -> str:
    return re.sub(r"\s+", "", block.text or "")


def _block_text(block: DocumentBlock) -> str:
    return block.text or ""


def _zone(block: DocumentBlock) -> str:
    return str((block.meta or {}).get("layout_zone") or "main")


def _y_overlap(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _x_overlap(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0]))


# ── L1 算子：小而稳定的一层，配置词和词插件都站在它上面 ──────────────────────


@lru_cache(maxsize=512)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _findall(pattern: Any, text: Any) -> tuple[Any, ...]:
    return tuple(_compiled(str(pattern)).findall(str(text)))


def _latex_texts(text: Any) -> tuple[str, ...]:
    r"""公式里每个 ``\text{…}``/``\textrm{…}`` 的内容，已去掉首尾空白。"""
    return tuple(m.group(1).strip() for m in _MATH_TEXT_RE.finditer(str(text)))


def _has_cjk(text: Any) -> bool:
    return bool(_CJK_RE.search(str(text)))


def _lexicon_lookup(lexicons: Mapping[str, "Lexicon"]) -> Any:
    def lexicon(name: Any) -> tuple[str, ...]:
        entry = lexicons.get(str(name))
        if entry is None:
            # 返回空集合等于让引用它的词永远不命中，那是最难查的一种坏；宁可当场炸。
            raise ValueError(f"{LEXICONS_FILE} 里没有名为 {name} 的词表")
        return entry.words

    return lexicon


def symptom_expr_functions(
    lexicons: Mapping[str, "Lexicon"] | None = None
) -> dict[str, Any]:
    """表达式里可用的全部算子。

    与版式规则共用 ``rule_expr_functions()`` 那一批，再加上聚合与文本原语——
    作者不该还得记住「这两种文件的方言差在哪」。
    """
    base = rule_expr_functions()
    base.update(
        {
            "any": lambda values: any(values),
            "all": lambda values: all(values),
            "findall": _findall,
            "latex_texts": _latex_texts,
            "has_cjk": _has_cjk,
            "lexicon": _lexicon_lookup(lexicons or {}),
        }
    )
    return base


class PageVocabulary:
    """一页的求值词表：页级聚合 + 每块的名字表。"""

    def __init__(self, blocks: Sequence[DocumentBlock], html: str, page_no: int) -> None:
        self.blocks = list(blocks)
        self.html = html or ""
        self.page_no = int(page_no)

        boxes = [b for b in (_bbox(blk) for blk in self.blocks) if b is not None]
        self.page_w = max((b[2] for b in boxes), default=1000.0) or 1000.0
        self.page_h = max((b[3] for b in boxes), default=1000.0) or 1000.0

        self._zones = [_zone(b) for b in self.blocks]
        self._types = [str(b.type or "") for b in self.blocks]
        self._roles = [
            str((b.meta or {}).get("pedagogy_role") or "body") for b in self.blocks
        ]
        self._norm_texts = [_norm_text(b) for b in self.blocks]

        main_match = re.search(
            r'<section class="lb-main"[^>]*>(.*?)</section>', self.html, re.S
        )
        main_inner = main_match.group(1) if main_match else ""
        self.main_text = re.sub(r"<[^>]+>", "", main_inner).strip()

        self.page_names: dict[str, Any] = {
            "page_no": self.page_no,
            "page_w": self.page_w,
            "page_h": self.page_h,
            "page_block_count": len(self.blocks),
            "page_text_len": sum(b.content_length() for b in self.blocks),
            "page_figure_count": self._types.count("figure"),
            "page_table_count": self._types.count("table"),
            "page_heading_count": sum(1 for t in self._types if t == "heading"),
            "page_main_count": self._zones.count("main"),
            "page_aside_count": sum(
                1 for z in self._zones if z in ("aside_left", "aside_right")
            ),
            "page_low_conf_count": sum(
                1
                for b in self.blocks
                if str((b.meta or {}).get("layout_confidence") or "") == "low"
            ),
            "main_text_len": len(self.main_text),
            "html_len": len(self.html),
        }

    def functions(
        self, lexicons: Mapping[str, "Lexicon"] | None = None
    ) -> dict[str, Any]:
        base = symptom_expr_functions(lexicons)
        base.update(
            {
                "count_type": lambda t: self._types.count(str(t)),
                "count_zone": lambda z: self._zones.count(str(z)),
                "count_role": lambda r: self._roles.count(str(r)),
                "html_contains": lambda s: str(s) in self.html,
                "html_count": lambda s: self.html.count(str(s)),
            }
        )
        return base

    def block_names(self, index: int) -> dict[str, Any]:
        block = self.blocks[index]
        meta = block.meta or {}
        box = _bbox(block)
        text = _block_text(block)
        norm = self._norm_texts[index]
        zone = self._zones[index]

        overlap_count = 0
        y_overlap_max = 0.0
        if box is not None:
            for j, other in enumerate(self.blocks):
                if j == index:
                    continue
                other_box = _bbox(other)
                if other_box is None:
                    continue
                ov = _y_overlap(box, other_box)
                y_overlap_max = max(y_overlap_max, ov)
                if ov > 0 and _x_overlap(box, other_box) > 0:
                    overlap_count += 1

        same_zone = [j for j, z in enumerate(self._zones) if z == zone]
        names: dict[str, Any] = {
            "key": block.id,
            "block_id": block.id,
            "order": block.order,
            "type": str(block.type or ""),
            "mineru_type": str(meta.get("mineru_type") or ""),
            "role": self._roles[index],
            "zone": zone,
            "text": text,
            "text_preview": text[:_TEXT_PREVIEW_LIMIT],
            "char_count": len(norm),
            "has_text": bool(norm),
            "caption": block.caption or "",
            "has_caption": bool(block.caption),
            "level": int(block.level or 0),
            "is_heading": str(block.type or "") == "heading",
            "is_figure": str(block.type or "") == "figure",
            "is_table": str(block.type or "") == "table",
            "confidence": float(meta.get("layout_confidence_score") or 0.0),
            "confidence_label": str(meta.get("layout_confidence") or ""),
            "rule_id": str(meta.get("layout_rule_id") or ""),
            "rule_layer": str(meta.get("layout_rule_layer") or ""),
            "column_count": int(meta.get("column_count") or 1),
            "column_method": str(meta.get("column_method") or ""),
            "prev_type": self._types[index - 1] if index > 0 else "",
            "next_type": self._types[index + 1] if index + 1 < len(self.blocks) else "",
            "prev_zone": self._zones[index - 1] if index > 0 else "",
            "next_zone": self._zones[index + 1] if index + 1 < len(self.blocks) else "",
            "same_zone_count": len(same_zone),
            "same_type_count": self._types.count(self._types[index]),
            "dup_text_count": (self._norm_texts.count(norm) - 1) if norm else 0,
            "overlap_count": overlap_count,
            "y_overlap_max": y_overlap_max,
            "is_first_in_zone": bool(same_zone) and same_zone[0] == index,
            "is_last_in_zone": bool(same_zone) and same_zone[-1] == index,
            "has_bbox": box is not None,
        }
        if box is None:
            names.update(
                {
                    "x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0,
                    "width": 0.0, "height": 0.0, "cx": 0.0, "cy": 0.0,
                    "rel_x0": 0.0, "rel_y0": 0.0, "rel_x1": 0.0, "rel_y1": 0.0,
                    "width_ratio": 0.0, "height_ratio": 0.0,
                    "is_wide": False, "is_full_width": False,
                }
            )
        else:
            x0, y0, x1, y1 = box
            width, height = x1 - x0, y1 - y0
            names.update(
                {
                    "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "width": width, "height": height,
                    "cx": (x0 + x1) / 2.0, "cy": (y0 + y1) / 2.0,
                    "rel_x0": x0 / self.page_w, "rel_y0": y0 / self.page_h,
                    "rel_x1": x1 / self.page_w, "rel_y1": y1 / self.page_h,
                    "width_ratio": width / self.page_w,
                    "height_ratio": height / self.page_h,
                    "is_wide": width >= self.page_w * 0.38,
                    "is_full_width": width >= self.page_w * 0.72,
                }
            )
        return names


def _sample_vocabulary() -> PageVocabulary:
    """一份最小样本词表。名字清单和加载期校验都从这里推导，避免两处漂移。"""
    sample = DocumentBlock(
        id="b1", type="paragraph", page_no=1, order=1, text="示例", bbox=[0, 0, 500, 40]
    )
    return PageVocabulary([sample], "<article></article>", 1)


# 每个词的中文释义。判据是给人复核的，光有名字和样例值读不出「这在判什么」；
# 测试会锁住这张表与真实词表一一对应，加了名字忘了写说明会直接红。
VOCAB_DOC: dict[str, str] = {
    # 页级
    "page_no": "页码（书内第几页）",
    "page_w": "页面逻辑宽度（坐标系单位，通常约 1000）",
    "page_h": "页面逻辑高度",
    "page_block_count": "本页块总数",
    "page_text_len": "本页所有块的正文字符数合计",
    "page_figure_count": "本页图块数量",
    "page_table_count": "本页表格块数量",
    "page_heading_count": "本页标题块数量",
    "page_main_count": "本页落在主栏的块数量",
    "page_aside_count": "本页落在左右侧栏的块数量",
    "page_low_conf_count": "本页版式判定为低置信的块数量",
    "main_text_len": "渲染后主栏区的纯文本字符数",
    "html_len": "渲染后整页 HTML 的字符数",
    # 块级 · 身份
    "key": "块主键（同 block_id）",
    "block_id": "块编号",
    "order": "块在本页的排序序号",
    "type": "块类型：paragraph / heading / figure / table / formula 等",
    "mineru_type": "MinerU 原始给的类型名（未经归一）",
    "role": "教学角色：body / example / exercise / note 等",
    "zone": "分区：main 主栏、aside_left 左侧栏、aside_right 右侧栏、header 页眉等",
    # 块级 · 文本
    "text": "块的完整正文",
    "text_preview": "正文前若干字（做证据展示用，别拿来做判据）",
    "char_count": "归一化后的正文字符数（去掉空白与标记）",
    "has_text": "是否有正文（归一化后非空）",
    "caption": "图/表的题注文字",
    "has_caption": "是否带题注",
    "level": "标题层级（1=章，2=节…；非标题为 0）",
    "is_heading": "是不是标题块",
    "is_figure": "是不是图块",
    "is_table": "是不是表格块",
    # 块级 · 版式判定来源
    "confidence": "版式判定置信度分值（0–1）",
    "confidence_label": "版式判定置信度档位：high / medium / low",
    "rule_id": "把这块判成当前分区的那条版式规则的标识",
    "rule_layer": "那条规则来自哪一层：base_rules / 教材层 / 页面覆写",
    "column_count": "本页分栏检测出的栏数",
    "column_method": "分栏用的方法名（几何切分 / 投影等）",
    # 块级 · 邻居与重复
    "prev_type": "上一块的类型（本页第一块为空串）",
    "next_type": "下一块的类型（本页最后一块为空串）",
    "prev_zone": "上一块的分区",
    "next_zone": "下一块的分区",
    "same_zone_count": "与本块同分区的块数量",
    "same_type_count": "与本块同类型的块数量",
    "dup_text_count": "本页与本块正文完全相同的其他块数量（查重复内容）",
    "overlap_count": "与本块在横纵方向都有交叠的块数量（查压字/错位）",
    "y_overlap_max": "与其他块的最大纵向交叠高度",
    "is_first_in_zone": "是不是本分区的第一块",
    "is_last_in_zone": "是不是本分区的最后一块",
    # 块级 · 几何
    "has_bbox": "有没有坐标框（没有坐标时下面所有几何量都为 0）",
    "x0": "左边界绝对坐标",
    "y0": "上边界绝对坐标",
    "x1": "右边界绝对坐标",
    "y1": "下边界绝对坐标",
    "width": "块宽（绝对值）",
    "height": "块高（绝对值）",
    "cx": "中心点横坐标",
    "cy": "中心点纵坐标",
    "rel_x0": "左边界 ÷ 页宽（0=最左，1=最右）",
    "rel_y0": "上边界 ÷ 页高（0=页顶，1=页底）",
    "rel_x1": "右边界 ÷ 页宽",
    "rel_y1": "下边界 ÷ 页高",
    "width_ratio": "块宽 ÷ 页宽（判窄块/通栏最常用）",
    "height_ratio": "块高 ÷ 页高",
    "is_wide": "宽度 ≥ 页宽 38%（宽块）",
    "is_full_width": "宽度 ≥ 页宽 72%（通栏）",
    # 函数
    "startswith": "startswith(文本, 前缀) → 是否以该前缀开头",
    "endswith": "endswith(文本, 后缀) → 是否以该后缀结尾",
    "contains": "contains(文本, 子串) → 是否包含该子串",
    "matches": "matches(文本, 正则) → 正则是否匹配得上",
    "findall": "findall(正则, 文本) → 所有匹配片段（有捕获组时每项是组的元组）",
    "latex_texts": "latex_texts(文本) → 公式里每个 \\text{…} 的内容（已去首尾空白）",
    "has_cjk": "has_cjk(文本) → 是否含汉字",
    "any": "any(列表) → 列表里有没有为真的项，配列表推导式用",
    "all": "all(列表) → 列表里是否全为真",
    "lexicon": f"lexicon('名字') → 取 {LEXICONS_FILE} 里那份词表（名字必须写字面量）",
    "len": "len(值) → 长度",
    "abs": "abs(数) → 绝对值",
    "min": "min(a, b, …) → 最小值",
    "max": "max(a, b, …) → 最大值",
    "round": "round(数[, 位数]) → 四舍五入",
    "count_type": "count_type('figure') → 本页该类型的块数",
    "count_zone": "count_zone('main') → 本页该分区的块数",
    "count_role": "count_role('example') → 本页该教学角色的块数",
    "html_contains": "html_contains('字符串') → 整页 HTML 里是否出现",
    "html_count": "html_count('字符串') → 整页 HTML 里出现几次",
}


LAYER_L0 = "L0"       # 事实字段，镜像内，几乎不变
LAYER_L1 = "L1"       # 算子，镜像内，小而稳定
LAYER_L2 = "L2"       # 配置词，挂载目录，热
LAYER_L2B = "L2b"     # 词插件，挂载目录，热，受限 Python
LAYER_DERIVED = "derived"  # 派生特征，挂载目录，热

LAYER_DOC: dict[str, str] = {
    LAYER_L0: "事实字段（写在镜像里，一块一页的原始事实，几乎不变）",
    LAYER_L1: "算子（写在镜像里，函数入口，小而稳定）",
    LAYER_L2: f"配置词（{VOCABULARY_FILE}，挂载目录，保存即生效，不重建镜像）",
    LAYER_L2B: f"词插件（{PLUGINS_DIRNAME}/*.py，挂载目录，受限 Python，保存即生效）",
    LAYER_DERIVED: f"派生特征（{FEATURES_FILE}，挂载目录，已有词的组合）",
}


def _word_users(
    checks: Sequence[SymptomCheck],
    words: Sequence[ConfigWord],
    features: Sequence[DerivedFeature],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """谁在用这个词。

    返回 (检测器直接用法, 词间用法)。分两张表的理由：一个词往往不被检测器直接
    引用，而是被另一个词引用（``lexicon`` 只出现在 ``has_math`` 的表达式里）。
    只看直接引用会把这类词误报成孤词，所以词间用法要单独留着做传递闭包。
    """
    by_check: dict[str, list[str]] = {}
    for check in checks:
        label = Path(check.source).name or check.suspect_type
        used = set(_expr_refs(check.detect.when))
        used.update(check.evidence)
        used.update(check.remediation.locate_by)
        for name in used:
            by_check.setdefault(name, []).append(label)
    by_word: dict[str, list[str]] = {}
    for word in words:
        for name in _word_refs(word):
            by_word.setdefault(name, []).append(word.name)
    for feature in features:
        for name in _referenced_names(feature.node):
            by_word.setdefault(name, []).append(feature.name)
    return by_check, by_word


def _expr_refs(expr: str) -> set[str]:
    """表达式原文引用到的名字；解析不了就当没引用（加载期已经报过错了）。"""
    try:
        return _referenced_names(ast.parse(expr or "", mode="eval"))
    except SyntaxError:
        return set()


def _word_refs(word: ConfigWord) -> set[str]:
    """一个词的依赖：配置词看表达式，插件词看它声明的 ``REQUIRES``。"""
    if word.node is not None:
        return _referenced_names(word.node)
    return set(word.requires)


def _word_entries(
    vocab: PageVocabulary,
    words: Sequence[ConfigWord],
    features: Sequence[DerivedFeature],
    by_check: Mapping[str, list[str]],
    by_word: Mapping[str, list[str]],
) -> list[dict[str, Any]]:
    """逐词档案：来源层级、中文释义、依赖、谁在用。"""
    entries: list[dict[str, Any]] = []
    resolved: dict[str, frozenset[str]] = {}

    def detectors_of(name: str, seen: frozenset[str] = frozenset()) -> frozenset[str]:
        """哪些检测器最终会用到这个词（含经由别的词的间接引用）。

        ``seen`` 挡环：加载期已经拒了成环的声明，这里只是不让诊断自己递归死。
        """
        if name in resolved:
            return resolved[name]
        if name in seen:
            return frozenset()
        out = set(by_check.get(name, ()))
        for consumer in by_word.get(name, ()):
            out |= detectors_of(consumer, seen | {name})
        result = frozenset(out)
        if not (seen - {name}):  # 只缓存完整求出来的那些
            resolved[name] = result
        return result

    def add(
        name: str,
        layer: str,
        scope: str,
        *,
        doc: str = "",
        expr: str = "",
        depends_on: Sequence[str] = (),
        source: str = "",
    ) -> None:
        used_by = sorted(detectors_of(name))
        entries.append(
            {
                "name": name,
                "layer": layer,
                "scope": scope,
                "doc": doc or VOCAB_DOC.get(name, ""),
                "expr": expr,
                "depends_on": list(depends_on),
                # 最终会用到它的检测器（含经由别的词的间接引用）
                "used_by": used_by,
                # 直接引用它的词与派生特征（看清是「谁经手的」）
                "used_by_words": sorted(set(by_word.get(name, ()))),
                # 孤词：没有任何检测器会因为它而命中，这只眼睛等于白开
                "unused": not used_by,
                "source": source,
            }
        )

    for name in vocab.page_names:
        add(name, LAYER_L0, "page")
    block_only = set(vocab.block_names(0)) - set(vocab.page_names)
    for name in sorted(block_only):
        add(name, LAYER_L0, "block")
    for name in sorted(vocab.functions().keys()):
        add(name, LAYER_L1, "both")
    for word in words:
        layer = LAYER_L2B if word.plugin is not None else LAYER_L2
        add(
            word.name,
            layer,
            word.scope,
            doc=word.doc,
            expr=word.expr,
            depends_on=sorted(_word_refs(word)),
            source=word.source,
        )
    for feature in features:
        add(
            feature.name,
            LAYER_DERIVED,
            "page" if feature.page_safe else "block",
            expr=feature.expr,
            depends_on=sorted(_referenced_names(feature.node)),
            source=FEATURES_FILE,
        )
    return entries


def _word_diagnostics(
    entries: Sequence[Mapping[str, Any]],
    hits_by_suspect: Mapping[str, Mapping[str, int]] | None = None,
    checks: Sequence[SymptomCheck] = (),
) -> dict[str, Any]:
    """词表体检：孤词、重叠、命中量。

    为什么要有这一段：检测器加得越多，词表越容易变成「有的词没人用，有的词两条
    判据算的是同一件事」。孤词说明这只眼睛白开了，重叠说明该合并——这两件事不看
    诊断是发现不了的，界面上只会显示一长串名字。

    命中量按「用到这个词的检测器一共命中过多少处、其中多少被判为误报」聚合。
    这是每个词**实际起了多少作用**的唯一可测量口径。
    """
    hot_layers = (LAYER_L2, LAYER_L2B, LAYER_DERIVED)
    hot = [e for e in entries if e["layer"] in hot_layers]
    unused = sorted(e["name"] for e in entries if e["unused"])
    unused_hot = sorted(e["name"] for e in hot if e["unused"])

    # 表达式一字不差重复 = 同一件事写了两遍
    by_expr: dict[str, list[str]] = {}
    for entry in hot:
        expr = re.sub(r"\s+", " ", str(entry["expr"] or "")).strip()
        if expr:
            by_expr.setdefault(expr, []).append(entry["name"])
    duplicate_expr = [
        {"expr": expr, "names": sorted(names)}
        for expr, names in sorted(by_expr.items())
        if len(names) > 1
    ]

    # 使用者完全相同 = 这几个词永远一起出现，可能该合成一个
    by_users: dict[tuple[str, ...], list[str]] = {}
    for entry in hot:
        users = tuple(entry["used_by"])
        if users:
            by_users.setdefault(users, []).append(entry["name"])
    same_users = [
        {"used_by": list(users), "names": sorted(names)}
        for users, names in sorted(by_users.items())
        if len(names) > 1
    ]

    out: dict[str, Any] = {
        "total": len(entries),
        "unused": unused,
        "unused_hot": unused_hot,
        "coverage": {
            layer: {
                "total": sum(1 for e in entries if e["layer"] == layer),
                "used": sum(
                    1 for e in entries if e["layer"] == layer and not e["unused"]
                ),
            }
            for layer in (LAYER_L0, LAYER_L1, LAYER_L2, LAYER_L2B, LAYER_DERIVED)
        },
        "duplicate_expr": duplicate_expr,
        "same_users": same_users,
    }
    if hits_by_suspect is None:
        return out

    detector_suspect = {
        (Path(c.source).name or c.suspect_type): c.suspect_type for c in checks
    }
    per_word: list[dict[str, Any]] = []
    for entry in entries:
        hits = 0
        false_positive = 0
        for label in entry["used_by"]:
            stats = hits_by_suspect.get(detector_suspect.get(label, ""), {})
            hits += int(stats.get("hits") or 0)
            false_positive += int(stats.get("false_positive") or 0)
        if hits or false_positive:
            per_word.append(
                {
                    "name": entry["name"],
                    "hits": hits,
                    "false_positive": false_positive,
                    "false_positive_rate": (
                        round(false_positive / hits, 4) if hits else None
                    ),
                }
            )
    out["hits"] = sorted(per_word, key=lambda x: -x["hits"])
    return out


def vocabulary_reference(
    directory: Path | None = None,
    *,
    hits_by_suspect: Mapping[str, Mapping[str, int]] | None = None,
) -> dict[str, Any]:
    """给 Agent、界面和文档用的词表清单。

    除了名字 + 示例值 + 中文释义，还给每个词标出**来源层级**（哪一层、改它要不要
    重建镜像）、依赖、谁在用，以及整张表的体检结果。层级必须露出来：同一个名字，
    写在镜像里和写在挂载目录里，维护代价差一次重建加一次服务中断。
    """
    vocab = _sample_vocabulary()
    features = load_features(directory)
    words = load_words(directory)
    lexicons = load_lexicons(directory)
    checks = load_checks(directory)
    by_check, by_word = _word_users(checks, words, features)
    entries = _word_entries(vocab, words, features, by_check, by_word)
    return {
        "page": vocab.page_names,
        "block": vocab.block_names(0),
        "functions": sorted(vocab.functions(lexicons).keys()),
        "derived": [
            {"name": f.name, "expr": f.expr, "page_safe": f.page_safe} for f in features
        ],
        "docs": dict(VOCAB_DOC),
        "layers": dict(LAYER_DOC),
        "words": entries,
        "lexicons": [
            {"name": lex.name, "doc": lex.doc, "size": len(lex.words)}
            for lex in sorted(lexicons.values(), key=lambda x: x.name)
        ],
        "diagnostics": _word_diagnostics(entries, hits_by_suspect, checks),
    }


class _LazyNames(Mapping):
    """求值词表：L0 字段直接给，配置词/词插件/派生特征按需算一次并缓存。

    惰性有三个理由——一页几十个块乘十几个名字，全量预计算大部分是白算；名字之间
    允许互相引用，惰性天然按依赖顺序求值，不需要拓扑排序；而配置词搬出镜像之后，
    只有被检测器真正引用到的词才会被求值，比过去逐块预算还省。
    """

    def __init__(
        self,
        base: dict[str, Any],
        features: Mapping[str, DerivedFeature],
        functions: dict[str, Any],
        *,
        words: Mapping[str, ConfigWord] | None = None,
    ) -> None:
        self._base = base
        self._features = features
        self._words: Mapping[str, ConfigWord] = words or {}
        self._cache: dict[str, Any] = {}
        self._resolving: set[str] = set()
        self._eval = EvalWithCompoundTypes(functions=functions)
        self._eval.names = self

    def __getitem__(self, key: str) -> Any:
        if key in self._base:
            return self._base[key]
        if key in self._cache:
            return self._cache[key]
        word = self._words.get(key)
        feature = self._features.get(key) if word is None else None
        if word is None and feature is None:
            raise KeyError(key)
        if key in self._resolving:
            # 加载期已经挡过环了，这里是最后一道保险，防止递归到栈溢出。
            raise ValueError(f"{key} 循环引用")
        self._resolving.add(key)
        try:
            if word is not None:
                value = self._resolve_word(word)
            else:
                assert feature is not None
                value = self._eval.eval(feature.expr, previously_parsed=feature.node)
        finally:
            self._resolving.discard(key)
        self._cache[key] = value
        return value

    def _resolve_word(self, word: ConfigWord) -> Any:
        if word.plugin is None:
            return self._eval.eval(word.expr, previously_parsed=word.node)
        # 依赖契约在这里是有牙齿的：插件只看得见 REQUIRES 声明过的字段。
        fields: dict[str, Any] = {}
        for name in word.requires:
            try:
                fields[name] = self[name]
            except KeyError as exc:
                raise ValueError(
                    f"词插件 {word.name} 声明依赖 {name}，但当前 scope 里没有这个名字"
                ) from exc
        return word.plugin.call(fields)

    def __iter__(self) -> Iterator[str]:
        yield from self._base
        yield from self._words
        yield from self._features

    def __len__(self) -> int:
        return len(self._base) + len(self._words) + len(self._features)


# ── 求值 ────────────────────────────────────────────────────────────────────


class CheckHit(BaseModel):
    suspect_type: str
    severity: Severity
    page_no: int
    block_id: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    # evidence 里「不给就没法动手」的那几个字段名（来自 remediation.locate_by）。
    # 改进器只拿这个子集，所以它得跟着命中一起传下去。
    locate_by: list[str] = Field(default_factory=list)
    detector: str = ""


def _evidence_of(check: SymptomCheck, names: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field_name in check.evidence:
        try:
            value = names[field_name]
        except (KeyError, ValueError):
            continue
        if isinstance(value, float):
            value = round(value, 4)
        elif isinstance(value, str):
            value = value[:120]
        out[field_name] = value
    return out


def run_checks_on_page(
    blocks: Sequence[DocumentBlock],
    html: str,
    *,
    page_no: int,
    checks: Iterable[SymptomCheck] | None = None,
    features: Sequence[DerivedFeature] | None = None,
    words: Sequence[ConfigWord] | None = None,
    lexicons: Mapping[str, Lexicon] | None = None,
) -> list[CheckHit]:
    """在一页上跑全部检测器。纯函数，不碰数据库。"""
    page_blocks = [b for b in blocks if b.page_no == page_no]
    if not page_blocks:
        return []
    if any(bool((b.meta or {}).get("blank")) for b in page_blocks):
        return []

    active = [c for c in (checks if checks is not None else load_checks()) if c.enabled]
    if not active:
        return []

    feature_map = {
        f.name: f for f in (features if features is not None else load_features())
    }
    word_map = {w.name: w for w in (words if words is not None else load_words())}
    vocab = PageVocabulary(page_blocks, html, page_no)
    functions = vocab.functions(lexicons if lexicons is not None else load_lexicons())
    evaluator = EvalWithCompoundTypes(functions=functions)
    hits: list[CheckHit] = []

    for check in active:
        try:
            if check.detect.scope == "page":
                page_names = _LazyNames(
                    dict(vocab.page_names), feature_map, functions, words=word_map
                )
                evaluator.names = page_names
                if bool(evaluator.eval(check.detect.when)):
                    hits.append(
                        CheckHit(
                            suspect_type=check.suspect_type,
                            severity=check.severity,
                            page_no=page_no,
                            evidence=_evidence_of(check, page_names),
                            locate_by=check.remediation.locate_fields(check.evidence),
                            detector=Path(check.source).name or check.suspect_type,
                        )
                    )
                continue

            matched: list[tuple[str, Mapping[str, Any]]] = []
            for index in range(len(vocab.blocks)):
                names = _LazyNames(
                    {**vocab.page_names, **vocab.block_names(index)},
                    feature_map,
                    functions,
                    words=word_map,
                )
                evaluator.names = names
                try:
                    fired = bool(evaluator.eval(check.detect.when))
                except Exception as exc:  # noqa: BLE001 — 一个块算崩不影响其余块
                    logger.warning(
                        "symptom check %s failed on block %s (page %s): %s",
                        check.suspect_type, vocab.blocks[index].id, page_no, exc,
                    )
                    continue
                if fired:
                    matched.append((vocab.blocks[index].id, names))
            if len(matched) < check.detect.min_hits:
                continue
            for block_id, names in matched:
                hits.append(
                    CheckHit(
                        suspect_type=check.suspect_type,
                        severity=check.severity,
                        page_no=page_no,
                        block_id=block_id,
                        evidence=_evidence_of(check, names),
                        locate_by=check.remediation.locate_fields(check.evidence),
                        detector=Path(check.source).name or check.suspect_type,
                    )
                )
        except Exception as exc:  # noqa: BLE001 — 一条坏表达式不能拖垮整页
            logger.warning(
                "symptom check %s failed on page %s: %s", check.suspect_type, page_no, exc
            )
    return hits


# ── 映射：症状 → 模式 → 历史成功路径 ───────────────────────────────────────


def downgrade(severity: str) -> str:
    try:
        idx = _SEVERITY_ORDER.index(severity)
    except ValueError:
        return severity
    return _SEVERITY_ORDER[min(idx + 1, len(_SEVERITY_ORDER) - 1)]


def _patterns_by_suspect(session: Session) -> dict[str, CorpusImprovementPattern]:
    rows = (
        session.query(CorpusImprovementPattern)
        .filter(CorpusImprovementPattern.suspect_type != "")
        .all()
    )
    out: dict[str, CorpusImprovementPattern] = {}
    for row in rows:
        # 同名取成员最多的那条：合并没做干净时也不会映射到空壳上。
        prev = out.get(row.suspect_type)
        if prev is None or int(row.member_count or 0) > int(prev.member_count or 0):
            out[row.suspect_type] = row
    return out


def _historical_fix(session: Session, pattern_id: str, fingerprint: str) -> str:
    """同指纹的历史修正摘要，给「上次是怎么改好的」一句话。"""
    rows = (
        session.query(CorpusImprovementPoint)
        .filter(CorpusImprovementPoint.pattern_id == pattern_id)
        .filter(CorpusImprovementPoint.status == "accepted")
        .limit(50)
        .all()
    )
    for row in rows:
        concrete = loads(row.concrete_json, {}) or {}
        if fingerprint and str(concrete.get("symptom_fingerprint") or "") != fingerprint:
            continue
        after = str(concrete.get("after") or concrete.get("new") or "").strip()
        if after:
            return after[:200]
        if row.summary:
            return str(row.summary)[:200]
    return ""


def enrich_findings(
    session: Session,
    findings: list[dict[str, Any]],
    *,
    count_hits: bool = False,
) -> list[dict[str, Any]]:
    """给每条 finding 挂上模式、处置档位和历史成功路径。

    只做非破坏性富化：命中误报库降一档严重度并加一句提示，**绝不静默丢弃**。
    抑制是人的权力，不是这里的。

    ``count_hits`` 只有导入/重建这类真检测的调用方才开。误报率的分母必须是检测器
    实际命中的处数，读接口反复富化不能计进去。
    """
    if not findings:
        return findings

    suspects = {str(f.get("suspect_type") or "") for f in findings}
    suspects.discard("")
    if not suspects:
        return findings

    patterns = _patterns_by_suspect(session)
    fingerprints = {str(f.get("fingerprint") or "") for f in findings}
    fingerprints.discard("")
    fp_rows = (
        session.query(CorpusSymptomFalsePositive)
        .filter(CorpusSymptomFalsePositive.fingerprint.in_(sorted(fingerprints)))
        .all()
        if fingerprints
        else []
    )
    fp_by_fingerprint: dict[str, int] = {}
    for row in fp_rows:
        fp_by_fingerprint[row.fingerprint] = fp_by_fingerprint.get(row.fingerprint, 0) + 1

    hit_counts: dict[str, int] = {}
    for finding in findings:
        suspect = str(finding.get("suspect_type") or "")
        pattern = patterns.get(suspect)
        if pattern is not None:
            finding["pattern_id"] = pattern.id
            finding["remediation_kind"] = pattern.remediation_kind or "playbook"
            finding["landing_target"] = pattern.landing_target or ""
            if pattern.playbook_ref:
                finding["playbook_ref"] = pattern.playbook_ref
            # 手册步骤按指针解析出全文：下游（Agent、界面）要的是步骤，不是路径。
            playbook = resolve_playbook(pattern.playbook_ref or "", suspect_type=suspect)
            if playbook:
                finding["playbook"] = playbook
            hit_counts[pattern.id] = hit_counts.get(pattern.id, 0) + 1
            known_fps = set(loads(pattern.fingerprints_json, []) or [])
            fingerprint = str(finding.get("fingerprint") or "")
            if fingerprint and fingerprint in known_fps:
                finding["match"] = "exact"
                fix = _historical_fix(session, pattern.id, fingerprint)
                if fix:
                    candidates = list(finding.get("candidates") or [])
                    hint = f"历史修正：{fix}"
                    if hint not in candidates:
                        candidates.append(hint)
                    finding["candidates"] = candidates
            else:
                finding["match"] = "suspect_type"

        # 没有模式桶时也要挂手册：第一本书上的声明式症状以前会变成「未知」。
        attach_read_side_playbook(finding)

        seen_fp = fp_by_fingerprint.get(str(finding.get("fingerprint") or ""), 0)
        if seen_fp:
            finding["severity"] = downgrade(str(finding.get("severity") or "medium"))
            candidates = list(finding.get("candidates") or [])
            hint = f"疑似历史误报（同指纹被判过 {seen_fp} 次不是问题）"
            if hint not in candidates:
                candidates.append(hint)
            finding["candidates"] = candidates
            finding["false_positive_hits"] = seen_fp

    if count_hits and hit_counts:
        stamp = _now_iso()
        for pattern in patterns.values():
            delta = hit_counts.get(pattern.id, 0)
            if not delta:
                continue
            pattern.hits = int(pattern.hits or 0) + delta
            pattern.updated_at = stamp
        session.flush()

    return findings


def summarize_findings(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """按页/症状汇总，写进 job 元数据给界面用。"""
    by_suspect: dict[str, int] = {}
    known_pages: set[int] = set()
    unknown_pages: set[int] = set()
    for finding in findings:
        suspect = str(finding.get("suspect_type") or "")
        by_suspect[suspect] = by_suspect.get(suspect, 0) + 1
        page_no = int(finding.get("page_no") or 0)
        if finding_has_fix_path(finding):
            known_pages.add(page_no)
        else:
            unknown_pages.add(page_no)
    return {
        "total": len(findings),
        "by_suspect_type": dict(sorted(by_suspect.items())),
        "known_pages": sorted(known_pages),
        "unknown_pages": sorted(unknown_pages - known_pages),
    }
