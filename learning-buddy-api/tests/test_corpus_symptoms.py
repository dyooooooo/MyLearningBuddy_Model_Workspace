"""症状检测器：解释器行为 + 每条声明的正反例回归。

最后一个测试是给抽象轨的 Agent 用的：它往 ``configs/checks/`` 加一条检测器、
往 ``tests/data/checks/`` 加正反例，这里自动把新检测器纳入回归，不需要改测试代码。
正例必须全中、反例必须全不中，否则这条检测器不该发布。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.corpus_symptoms import (
    CHECKS_DIR,
    VOCAB_DOC,
    PageVocabulary,
    _atomic_names,
    _eval_word,
    _function_names,
    load_check_source,
    load_checks,
    load_errors,
    load_features,
    load_lexicons,
    load_words,
    finding_has_fix_path,
    partition_findings,
    resolve_playbook,
    run_checks_on_page,
    summarize_findings,
    vocabulary_reference,
)
from app.services.document_ir import DocumentBlock

_FIXTURES = Path(__file__).resolve().parent / "data" / "checks"


def _word_value(name: str, sample: object, directory: Path | None = None) -> bool:
    """按当前生效的词表算一个词的值（sample 写法同 vocabulary.yaml 的样例）。"""
    words = {w.name: w for w in load_words(directory)}
    return bool(
        _eval_word(words[name], sample, words=words, lexicons=load_lexicons(directory))
    )

_HEADER_CHECK = """
suspect_type: HEADER_IN_MAIN
title: 页眉被当成正文
severity: medium
detect:
  scope: block
  when: "zone == 'main' and rel_y0 < 0.08 and char_count <= 30 and not has_math"
  min_hits: 1
evidence: [zone, rel_y0, char_count, text_preview]
remediation:
  kind: rule
  landing_target: L1.layout_rule
  rule_ref: "editions/demo/layout_rules.yaml#header-band"
"""


def _load_fixture(path: Path) -> tuple[list[DocumentBlock], str, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    page_no = int(data.get("page_no") or 1)
    html = str(data.get("html") or "<article><section class=\"lb-main\">x</section></article>")
    blocks = [DocumentBlock.model_validate(b) for b in data.get("blocks") or []]
    return blocks, html, page_no


# ── 解释器 ──────────────────────────────────────────────────────────────────


def test_declared_check_hits_expected_block():
    check = load_check_source(_HEADER_CHECK)
    blocks, html, page_no = _load_fixture(_FIXTURES / "header_in_main_pos.json")
    hits = run_checks_on_page(blocks, html, page_no=page_no, checks=[check])
    assert [h.block_id for h in hits] == ["b1"]
    assert hits[0].evidence["char_count"] == 5


def test_declared_check_quiet_on_negative_page():
    check = load_check_source(_HEADER_CHECK)
    blocks, html, page_no = _load_fixture(_FIXTURES / "header_in_main_neg.json")
    assert run_checks_on_page(blocks, html, page_no=page_no, checks=[check]) == []


def test_min_hits_suppresses_single_match():
    check = load_check_source(_HEADER_CHECK.replace("min_hits: 1", "min_hits: 2"))
    blocks, html, page_no = _load_fixture(_FIXTURES / "header_in_main_pos.json")
    assert run_checks_on_page(blocks, html, page_no=page_no, checks=[check]) == []


def test_blank_page_is_never_flagged():
    check = load_check_source(_HEADER_CHECK)
    blank = DocumentBlock(id="b0", type="paragraph", page_no=3, order=1, meta={"blank": True})
    assert run_checks_on_page([blank], "<article></article>", page_no=3, checks=[check]) == []


def test_page_scope_evaluates_once():
    check = load_check_source(
        """
suspect_type: PAGE_ALL_ASIDE
severity: high
detect:
  scope: page
  when: "page_block_count > 0 and page_main_count == 0"
evidence: [page_block_count, page_main_count]
remediation:
  kind: playbook
  playbook: "检查这一页的分栏判定"
"""
    )
    blocks = [
        DocumentBlock(
            id="a1", type="paragraph", page_no=1, order=1, text="旁注",
            bbox=[10, 10, 100, 40], meta={"layout_zone": "aside_left"},
        )
    ]
    hits = run_checks_on_page(blocks, "<article></article>", page_no=1, checks=[check])
    assert len(hits) == 1
    assert hits[0].block_id == ""


# ── 跨书可用性硬约束 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "when, reason",
    [
        ("page_no == 12 and zone == 'main'", "绝对页码"),
        ("block_id == 'b3'", "块 ID"),
        ("contains(text, '普通高中教科书数学')", "正文文字"),
    ],
)
def test_detector_rejects_book_specific_conditions(when: str, reason: str):
    src = _HEADER_CHECK.replace(
        'when: "zone == \'main\' and rel_y0 < 0.08 and char_count <= 30 and not has_math"',
        f'when: "{when}"',
    )
    with pytest.raises(ValueError):
        load_check_source(src)
    assert reason  # 文档化意图：这三类写法换一本书就失效


def test_playbook_kind_requires_steps():
    with pytest.raises(ValueError):
        load_check_source(
            """
suspect_type: SOMETHING_ODD
detect:
  when: "char_count > 0"
remediation:
  kind: playbook
"""
        )


def test_lowercase_suspect_type_rejected():
    with pytest.raises(ValueError):
        load_check_source(
            """
suspect_type: header_in_main
detect:
  when: "char_count > 0"
"""
        )


# ── 名字校验：写错的名字必须当场报错，不能留到求值期静默失效 ────────────────


def test_misspelled_field_name_is_rejected_with_hint():
    """拼错一个字母的检测器，过去会安静地永远不命中。"""
    with pytest.raises(ValueError) as exc:
        load_check_source(_HEADER_CHECK.replace("rel_y0 < 0.08", "rel_yo < 0.08"))
    msg = str(exc.value)
    assert "词表外的名字 rel_yo" in msg
    assert "rel_y0" in msg  # 直接告诉作者正确的名字，省一次翻词表


def test_unknown_evidence_field_is_rejected():
    src = _HEADER_CHECK.replace(
        "evidence: [zone, rel_y0, char_count, text_preview]",
        "evidence: [zone, bogus_field]",
    )
    with pytest.raises(ValueError) as exc:
        load_check_source(src)
    assert "evidence 里有词表外的名字 bogus_field" in str(exc.value)


def test_page_scope_cannot_use_block_fields():
    """页级求值时没有块，用块级字段就是必然失效。"""
    with pytest.raises(ValueError) as exc:
        load_check_source(
            """
suspect_type: PAGE_USES_BLOCK_FIELD
detect:
  scope: page
  when: "page_block_count > 0 and rel_y0 < 0.1"
remediation:
  kind: playbook
  playbook: "x"
"""
        )
    assert "rel_y0" in str(exc.value)


def test_broken_declaration_is_visible_not_just_logged(tmp_path: Path):
    """坏声明不能只写日志：清单里看不见，等于这条检测器从没存在过。"""
    (tmp_path / "broken.yaml").write_text(
        "这是一句误粘贴进来的话\nsuspect_type: BROKEN_ONE\n"
        "detect:\n  when: \"char_count > 0\"\n",
        encoding="utf-8",
    )
    assert load_checks(tmp_path) == ()
    errors = load_errors(tmp_path)
    assert [e.file for e in errors] == ["broken.yaml"]
    assert errors[0].errors


# ── 词表 ────────────────────────────────────────────────────────────────────


def test_cross_block_predicates_available():
    blocks = [
        DocumentBlock(id="b1", type="heading", page_no=1, order=1, text="标题",
                      bbox=[0, 0, 500, 40], meta={"layout_zone": "main"}),
        DocumentBlock(id="b2", type="paragraph", page_no=1, order=2, text="标题",
                      bbox=[0, 50, 500, 90], meta={"layout_zone": "main"}),
    ]
    vocab = PageVocabulary(blocks, "<article></article>", 1)
    first = vocab.block_names(0)
    assert first["next_type"] == "paragraph"
    assert first["same_zone_count"] == 2
    assert first["dup_text_count"] == 1
    assert first["is_first_in_zone"] is True


def test_vocabulary_reference_is_serializable():
    ref = vocabulary_reference()
    json.dumps(ref)
    assert "rel_y0" in ref["block"]
    assert "page_block_count" in ref["page"]
    assert "count_zone" in ref["functions"]
    assert isinstance(ref["derived"], list)


# L0 词表快照：这是一份对 configs/checks/ 的契约，只锁**镜像里那两层**。
# L2 配置词与 L2b 词插件不在这里锁——它们住在挂载目录，加一个词不该改测试代码；
# 它们的正确性由各自必填的双极性正反例在加载期保证。
# 加名字是安全的（改这里即可）；**改名或删名是破坏性变更**——所有引用它的检测器
# 会在加载期报错停用。真要改，先把引用它的 YAML 一起改掉。
_PAGE_NAMES = frozenset(
    {
        "html_len", "main_text_len", "page_aside_count", "page_block_count",
        "page_figure_count", "page_h", "page_heading_count", "page_low_conf_count",
        "page_main_count", "page_no", "page_table_count", "page_text_len", "page_w",
    }
)
_BLOCK_NAMES = frozenset(
    {
        "block_id", "caption", "char_count", "column_count",
        "column_method",
        "confidence", "confidence_label", "cx", "cy", "dup_text_count", "has_bbox",
        "has_caption", "has_text", "height", "height_ratio", "is_figure",
        "is_first_in_zone", "is_full_width", "is_heading", "is_last_in_zone",
        "is_table", "is_wide", "key", "level", "mineru_type", "next_type", "next_zone",
        "order", "overlap_count", "prev_type", "prev_zone", "rel_x0", "rel_x1",
        "rel_y0", "rel_y1", "role", "rule_id", "rule_layer", "same_type_count",
        "same_zone_count", "text", "text_preview", "type", "width", "width_ratio",
        "x0", "x1", "y0", "y1", "y_overlap_max", "zone",
    }
)
_FUNCTIONS = frozenset(
    {
        "abs", "all", "any", "contains", "count_role", "count_type", "count_zone",
        "endswith", "findall", "has_cjk", "html_contains", "html_count", "latex_texts",
        "len", "lexicon", "matches", "max", "min", "round", "startswith",
    }
)


def test_vocabulary_snapshot_is_a_contract():
    hint = "删名字或改名会让引用它的检测器在加载期停用，请连同 configs/checks/ 一起改"
    assert set(_atomic_names("page")) == set(_PAGE_NAMES), hint
    assert set(_atomic_names("block")) == set(_PAGE_NAMES | _BLOCK_NAMES), hint
    assert set(_function_names()) == set(_FUNCTIONS), hint


def test_every_vocabulary_name_has_a_chinese_doc():
    """加了词却不写释义，界面上那条判据就没人读得懂。"""
    named = set(_atomic_names("block")) | set(_function_names())
    missing = sorted(named - set(VOCAB_DOC))
    stale = sorted(set(VOCAB_DOC) - named)
    assert not missing, f"这些词缺中文释义：{missing}"
    assert not stale, f"释义表里有词表外的名字（已删名？）：{stale}"


@pytest.mark.parametrize(
    "latex",
    [
        # 第 20/22/35 页的真实内容：推导对齐标签，合法排版，报了就是误报
        r"\begin{array}{r l} \text{原式} & = \tan\alpha\cot\alpha = 1. \end{array}",
        r"\begin{array}{r l} \text{左边} & = \sin^{2}\alpha \\ & = \text{右边} \end{array}",
        r"\text{当 } x>0 \text{ 时}",  # 分段函数的条件词也在例外表内
        r"v = 3.0\times 10^{8}\ \text{m/s}",  # 拉丁单位本就该写在 \text 里
        r"S = \pi r^{2}",
    ],
)
def test_math_label_and_latin_unit_are_not_cjk_leak(latex):
    """判据搬进 vocabulary.yaml 之后，这批实例的结论必须一字不变。"""
    assert _word_value("cjk_text_in_math", latex) is False


@pytest.mark.parametrize(
    "latex",
    [
        r"1^{\circ}=\frac{\pi}{180}\text{弧度}",  # 中文单位黏进公式
        r"\sin\alpha=0,\ \text{cot }\alpha\text{ 不存在}",  # 整句中文塞进公式
        r"x=\textrm{每千米收费}",
    ],
)
def test_cjk_prose_in_math_is_flagged(latex):
    assert _word_value("cjk_text_in_math", latex) is True


def test_vocabulary_reference_carries_docs():
    ref = vocabulary_reference()
    assert ref["docs"]["width_ratio"].startswith("块宽")
    assert "matches" in ref["docs"]


# ── 派生特征 ────────────────────────────────────────────────────────────────


_DERIVED_CHECK = """
suspect_type: DERIVED_DEMO
detect:
  scope: block
  when: "is_plain_main_text and char_count <= 6"
evidence: [zone, is_plain_main_text]
remediation:
  kind: playbook
  playbook: "合并到后续正文"
"""


def _write_features(directory: Path, body: str) -> None:
    (directory / "features.yaml").write_text(body, encoding="utf-8")


_DEMO_VOCABULARY = """
words:
  looks_like_math:
    scope: block
    doc: 正文里出现了行内公式标记
    expr: "contains(text, '$')"
    examples:
      positive: ["$x$"]
      negative: ["纯文字"]
"""


def test_derived_feature_resolves_through_its_dependency(tmp_path: Path):
    """派生特征可以引用别的派生特征，也可以引用挂载目录里的配置词。"""
    (tmp_path / "vocabulary.yaml").write_text(_DEMO_VOCABULARY, encoding="utf-8")
    _write_features(
        tmp_path,
        "features:\n"
        "  is_main_prose: \"zone == 'main' and not is_figure\"\n"
        "  is_plain_main_text: \"is_main_prose and not looks_like_math\"\n",
    )
    (tmp_path / "derived_demo.yaml").write_text(_DERIVED_CHECK, encoding="utf-8")

    checks = load_checks(tmp_path)
    assert [c.suspect_type for c in checks] == ["DERIVED_DEMO"]
    assert load_errors(tmp_path) == ()

    blocks = [
        DocumentBlock(id="b1", type="paragraph", page_no=1, order=1, text="（1）",
                      bbox=[0, 0, 200, 40], meta={"layout_zone": "main"}),
        DocumentBlock(id="b2", type="figure", page_no=1, order=2, text="",
                      bbox=[0, 50, 200, 90], meta={"layout_zone": "main"}),
        DocumentBlock(id="b3", type="paragraph", page_no=1, order=3, text="$x$",
                      bbox=[0, 100, 200, 140], meta={"layout_zone": "main"}),
    ]
    hits = run_checks_on_page(
        blocks, "<article></article>", page_no=1,
        checks=checks, features=load_features(tmp_path),
        words=load_words(tmp_path), lexicons=load_lexicons(tmp_path),
    )
    assert [h.block_id for h in hits] == ["b1"]
    # 派生特征也能当证据，界面上直接看到「判定为什么成立」。
    assert hits[0].evidence["is_plain_main_text"] is True


def test_feature_cycle_is_reported_and_dropped(tmp_path: Path):
    """互相引用会在求值时递归到栈溢出，必须在加载期就拦掉。"""
    _write_features(
        tmp_path,
        "features:\n"
        "  feature_a: \"feature_b and has_text\"\n"
        "  feature_b: \"feature_a or is_figure\"\n",
    )
    assert load_features(tmp_path) == ()
    errors = load_errors(tmp_path)
    assert [e.file for e in errors] == ["features.yaml"]
    assert any("循环引用" in msg for msg in errors[0].errors)


def test_feature_referencing_unknown_name_is_dropped(tmp_path: Path):
    _write_features(tmp_path, "features:\n  is_odd_block: \"widht_ratio > 0.5\"\n")
    assert load_features(tmp_path) == ()
    assert any(
        "widht_ratio" in msg and "width_ratio" in msg
        for msg in load_errors(tmp_path)[0].errors
    )


def test_feature_cannot_shadow_an_atomic_name(tmp_path: Path):
    _write_features(tmp_path, "features:\n  width_ratio: \"width / page_w\"\n")
    assert load_features(tmp_path) == ()
    assert any("重名" in msg for msg in load_errors(tmp_path)[0].errors)


def test_block_only_feature_rejected_in_page_scope(tmp_path: Path):
    """页级检测器引用了块级派生特征，同样是必然失效，要在加载期拦下。"""
    _write_features(tmp_path, "features:\n  is_main_prose: \"zone == 'main'\"\n")
    (tmp_path / "page_demo.yaml").write_text(
        "suspect_type: PAGE_USES_BLOCK_FEATURE\n"
        "detect:\n  scope: page\n  when: \"is_main_prose and page_block_count > 0\"\n"
        "remediation:\n  kind: playbook\n  playbook: \"x\"\n",
        encoding="utf-8",
    )
    assert load_checks(tmp_path) == ()
    assert any("is_main_prose" in msg for msg in load_errors(tmp_path)[0].errors)


def test_features_file_is_not_loaded_as_a_detector():
    """features.yaml 和检测器同目录，不能被当成一条写坏的检测器。"""
    names = {Path(c.source).name for c in load_checks(CHECKS_DIR)}
    assert "features.yaml" not in names
    assert load_errors(CHECKS_DIR) == ()


@pytest.mark.parametrize("reserved", ["vocabulary.yaml", "lexicons.yaml"])
def test_reserved_files_are_not_loaded_as_detectors(reserved: str):
    """词表文件和检测器同目录，同样不能被当成检测器。"""
    names = {Path(c.source).name for c in load_checks(CHECKS_DIR)}
    assert reserved not in names


# ── 配置词（vocabulary.yaml）：元数据必填、自证、无环 ──────────────────────────


def _write_vocabulary(directory: Path, body: str) -> None:
    (directory / "vocabulary.yaml").write_text(body, encoding="utf-8")


def test_config_word_without_doc_is_dropped(tmp_path: Path):
    """没有中文释义的词，界面上那条判据就是一串看不懂的名字。"""
    _write_vocabulary(
        tmp_path,
        "words:\n"
        "  looks_odd:\n"
        "    expr: \"char_count > 0\"\n"
        "    examples:\n      positive: ['x']\n      negative: [{text: ''}]\n",
    )
    assert load_words(tmp_path) == ()
    assert any("缺 doc" in m for m in load_errors(tmp_path)[0].errors)


def test_config_word_without_examples_is_dropped(tmp_path: Path):
    """正反例是配置词唯一的正确性保证，缺了就不许上线。"""
    _write_vocabulary(
        tmp_path,
        "words:\n"
        "  looks_odd:\n"
        "    doc: 随便一个词\n"
        "    expr: \"char_count > 0\"\n",
    )
    assert load_words(tmp_path) == ()
    assert any("examples" in m for m in load_errors(tmp_path)[0].errors)


def test_config_word_failing_its_own_examples_is_dropped(tmp_path: Path):
    """自己声明的正例都算不出真：这词算错了，不该被任何检测器用上。"""
    _write_vocabulary(
        tmp_path,
        "words:\n"
        "  has_dollar:\n"
        "    doc: 正文里有美元符号\n"
        "    expr: \"contains(text, '$')\"\n"
        "    examples:\n"
        "      positive: ['这里没有美元符号']\n"
        "      negative: ['$x$']\n",
    )
    assert load_words(tmp_path) == ()
    errors = load_errors(tmp_path)
    assert any("正例" in m or "反例" in m for m in errors[0].errors)


def test_config_word_cycle_is_reported_and_dropped(tmp_path: Path):
    """词之间成环会在求值时递归到栈溢出，必须加载期拦掉。"""
    _write_vocabulary(
        tmp_path,
        "words:\n"
        "  word_a:\n"
        "    doc: 甲\n"
        "    expr: \"word_b and has_text\"\n"
        "    examples:\n      positive: ['x']\n      negative: [{text: ''}]\n"
        "  word_b:\n"
        "    doc: 乙\n"
        "    expr: \"word_a or is_figure\"\n"
        "    examples:\n      positive: ['x']\n      negative: [{text: ''}]\n",
    )
    assert load_words(tmp_path) == ()
    assert any("循环引用" in m for m in load_errors(tmp_path)[0].errors)


def test_config_word_cannot_shadow_an_atomic_name(tmp_path: Path):
    _write_vocabulary(
        tmp_path,
        "words:\n"
        "  width_ratio:\n"
        "    doc: 想覆盖 L0 字段\n"
        "    expr: \"width / page_w\"\n"
        "    examples:\n      positive: ['x']\n      negative: [{text: ''}]\n",
    )
    assert load_words(tmp_path) == ()
    assert any("重名" in m for m in load_errors(tmp_path)[0].errors)


def test_config_word_cannot_hardcode_book_specific_prose(tmp_path: Path):
    """词和检测器同权：中文正文只能进 lexicons.yaml，不能硬写在表达式里。"""
    _write_vocabulary(
        tmp_path,
        "words:\n"
        "  is_that_book:\n"
        "    doc: 认某一本教材的书名\n"
        "    expr: \"contains(text, '普通高中教科书数学必修第一册')\"\n"
        "    examples:\n      positive: ['x']\n      negative: [{text: ''}]\n",
    )
    assert load_words(tmp_path) == ()
    assert load_errors(tmp_path)[0].errors


def test_bad_vocabulary_file_does_not_block_detectors(tmp_path: Path):
    """一份坏词表不能把整个导入拖停：检测器该加载的照样加载。"""
    _write_vocabulary(tmp_path, "words: 这不是映射\n")
    (tmp_path / "header.yaml").write_text(
        _HEADER_CHECK.replace(" and not has_math", ""), encoding="utf-8"
    )
    assert [c.suspect_type for c in load_checks(tmp_path)] == ["HEADER_IN_MAIN"]
    assert [e.file for e in load_errors(tmp_path)] == ["vocabulary.yaml"]


def test_lexicon_requires_name_and_chinese_doc(tmp_path: Path):
    """词表是白名单数据文件，可以写中文，但必须说清收的是什么。"""
    (tmp_path / "lexicons.yaml").write_text(
        "lexicons:\n  math_label_words:\n    words: ['原式', '左边']\n",
        encoding="utf-8",
    )
    assert load_lexicons(tmp_path) == {}
    assert any("doc" in m for m in load_errors(tmp_path)[0].errors)


def test_word_referencing_missing_lexicon_is_dropped(tmp_path: Path):
    _write_vocabulary(
        tmp_path,
        "words:\n"
        "  in_some_list:\n"
        "    doc: 引用了不存在的词表\n"
        "    expr: \"text in lexicon('nope_words')\"\n"
        "    examples:\n      positive: ['x']\n      negative: [{text: ''}]\n",
    )
    assert load_words(tmp_path) == ()
    assert any("nope_words" in m for m in load_errors(tmp_path)[0].errors)


# ── 词插件（vocab_ext/*.py）：沙箱越权、自证、草稿不执行 ──────────────────────


_PLUGIN_OK = '''
NAME = "text_has_two_dollars"
SCOPE = "block"
DOC = "正文里出现了两个以上美元符号"
REQUIRES = ["text"]
EXAMPLES = {"positive": ["$a$ 与 $b$"], "negative": ["纯文字"]}


def evaluate(fields):
    return fields["text"].count("$") >= 2
'''


def _write_plugin(directory: Path, filename: str, body: str) -> None:
    ext = directory / "vocab_ext"
    ext.mkdir(exist_ok=True)
    (ext / filename).write_text(body, encoding="utf-8")


def test_plugin_word_loads_and_is_usable_in_a_detector(tmp_path: Path):
    _write_plugin(tmp_path, "two_dollars.py", _PLUGIN_OK)
    (tmp_path / "plugin_demo.yaml").write_text(
        "suspect_type: PLUGIN_DEMO\n"
        "detect:\n  scope: block\n  when: \"text_has_two_dollars\"\n"
        "evidence: [zone, text_has_two_dollars]\n"
        "remediation:\n  kind: playbook\n  locate_by: [zone]\n  playbook: \"拆开\"\n",
        encoding="utf-8",
    )
    assert load_errors(tmp_path) == ()
    assert [w.name for w in load_words(tmp_path)] == ["text_has_two_dollars"]

    blocks = [
        DocumentBlock(id="b1", type="paragraph", page_no=1, order=1, text="$a$ 与 $b$",
                      bbox=[0, 0, 200, 40], meta={"layout_zone": "main"}),
        DocumentBlock(id="b2", type="paragraph", page_no=1, order=2, text="纯文字",
                      bbox=[0, 50, 200, 90], meta={"layout_zone": "main"}),
    ]
    hits = run_checks_on_page(
        blocks, "<article></article>", page_no=1,
        checks=load_checks(tmp_path), features=load_features(tmp_path),
        words=load_words(tmp_path), lexicons=load_lexicons(tmp_path),
    )
    assert [h.block_id for h in hits] == ["b1"]


@pytest.mark.parametrize(
    "body, reason",
    [
        ("import os\n" + _PLUGIN_OK, "import"),
        (_PLUGIN_OK.replace(
            'return fields["text"].count("$") >= 2',
            'return fields["text"].__class__ is str',
        ), "属性穿透"),
        (_PLUGIN_OK.replace(
            'return fields["text"].count("$") >= 2',
            "n = 0\n    while True:\n        n += 1\n    return n > 0",
        ), "死循环"),
        (_PLUGIN_OK.replace('REQUIRES = ["text"]', ""), "缺 REQUIRES"),
        (_PLUGIN_OK.replace(
            'EXAMPLES = {"positive": ["$a$ 与 $b$"], "negative": ["纯文字"]}',
            'EXAMPLES = {"positive": ["$a$ 与 $b$"]}',
        ), "缺反例"),
    ],
)
def test_plugin_overreach_is_refused_at_load(tmp_path: Path, body: str, reason: str):
    """沙箱挡越权：挡不住的那部分（算得对不对）交给正反例。"""
    _write_plugin(tmp_path, "bad.py", body)
    assert load_words(tmp_path) == (), f"{reason} 的插件不该被加载"
    errors = load_errors(tmp_path)
    assert [e.file for e in errors] == ["vocab_ext/bad.py"]
    assert errors[0].errors


def test_plugin_failing_its_own_examples_is_dropped(tmp_path: Path):
    _write_plugin(
        tmp_path,
        "wrong.py",
        _PLUGIN_OK.replace(
            'EXAMPLES = {"positive": ["$a$ 与 $b$"], "negative": ["纯文字"]}',
            'EXAMPLES = {"positive": ["纯文字"], "negative": ["$a$ 与 $b$"]}',
        ),
    )
    assert load_words(tmp_path) == ()
    assert load_errors(tmp_path)[0].errors


def test_plugin_can_only_see_fields_it_declares(tmp_path: Path):
    """依赖契约要有牙齿：没写进 REQUIRES 的字段就是看不见。"""
    _write_plugin(
        tmp_path,
        "peek.py",
        _PLUGIN_OK.replace(
            'return fields["text"].count("$") >= 2',
            'return "zone" not in fields and fields["text"].count("$") >= 2',
        ),
    )
    assert [w.name for w in load_words(tmp_path)] == ["text_has_two_dollars"]


def test_plugin_proposals_are_never_executed(tmp_path: Path):
    """未审草稿放 vocab_ext/proposals/：加载器不看子目录，所以它不会被执行。"""
    ext = tmp_path / "vocab_ext" / "proposals"
    ext.mkdir(parents=True)
    (ext / "draft.py").write_text("raise RuntimeError('草稿被执行了')\n", encoding="utf-8")
    assert load_words(tmp_path) == ()
    assert load_errors(tmp_path) == ()


# ── locate_by：改进器凭什么定位 ─────────────────────────────────────────────


def test_locate_by_must_be_a_subset_of_evidence():
    with pytest.raises(ValueError) as exc:
        load_check_source(
            """
suspect_type: LOCATE_OUTSIDE
detect:
  scope: block
  when: "char_count > 0"
evidence: [zone, char_count]
remediation:
  kind: playbook
  locate_by: [zone, rel_y0]
  playbook: "x"
"""
        )
    assert "locate_by" in str(exc.value) and "rel_y0" in str(exc.value)


def test_playbook_without_any_locate_field_is_rejected():
    """手册照着做的人得知道改哪一块：evidence 和 locate_by 全空就是没给定位依据。"""
    with pytest.raises(ValueError) as exc:
        load_check_source(
            """
suspect_type: LOCATE_MISSING
detect:
  scope: block
  when: "char_count > 0"
remediation:
  kind: playbook
  playbook: "x"
"""
        )
    assert "locate_by" in str(exc.value)


def test_locate_by_defaults_to_whole_evidence():
    check = load_check_source(
        """
suspect_type: LOCATE_DEFAULT
detect:
  scope: block
  when: "char_count > 0"
evidence: [zone, char_count]
remediation:
  kind: playbook
  playbook: "x"
"""
    )
    assert check.remediation.locate_fields(check.evidence) == ["zone", "char_count"]


def test_hit_carries_locate_by_for_the_improver():
    check = load_check_source(
        """
suspect_type: LOCATE_ON_HIT
detect:
  scope: block
  when: "char_count > 0"
evidence: [zone, char_count, text_preview]
remediation:
  kind: playbook
  locate_by: [zone, text_preview]
  playbook: "x"
"""
    )
    blocks, html, page_no = _load_fixture(_FIXTURES / "header_in_main_pos.json")
    hits = run_checks_on_page(blocks, html, page_no=page_no, checks=[check])
    assert hits and hits[0].locate_by == ["zone", "text_preview"]


def test_every_declared_check_declares_its_locate_fields():
    """18 条现存检测器都要说清凭什么定位，否则改进器只能瞎猜。"""
    for check in load_checks(CHECKS_DIR):
        assert check.remediation.locate_by, f"{check.suspect_type} 没声明 locate_by"
        assert set(check.remediation.locate_by) <= set(check.evidence)


# ── 词表清单：来源层级、依赖、孤词 ──────────────────────────────────────────


def test_vocabulary_reference_marks_source_layers():
    """同一个名字，写在镜像里和写在挂载目录里，维护代价差一次重建加一次中断。"""
    words = {w["name"]: w for w in vocabulary_reference()["words"]}
    assert words["zone"]["layer"] == "L0"
    assert words["matches"]["layer"] == "L1"
    assert words["has_math"]["layer"] == "L2"
    assert words["has_math"]["source"] == "vocabulary.yaml"
    assert words["is_plain_main_text"]["layer"] == "derived"
    assert words["has_math"]["doc"]


def test_vocabulary_reference_tracks_indirect_users():
    """只被别的词引用的算子不是孤词——间接也算在用。"""
    words = {w["name"]: w for w in vocabulary_reference()["words"]}
    assert "has_math" in words["lexicon"]["used_by_words"]
    assert words["lexicon"]["used_by"], "lexicon 经 has_math 被检测器用到"
    assert words["lexicon"]["unused"] is False


def test_vocabulary_diagnostics_flag_orphan_hot_words(tmp_path: Path):
    """孤词说明这只眼睛白开了：没有任何检测器会因为它命中。"""
    _write_vocabulary(
        tmp_path,
        "words:\n"
        "  nobody_uses_me:\n"
        "    doc: 没人引用的词\n"
        "    expr: \"char_count > 3\"\n"
        "    examples:\n      positive: ['一二三四']\n      negative: ['一']\n",
    )
    ref = vocabulary_reference(tmp_path)
    assert "nobody_uses_me" in ref["diagnostics"]["unused_hot"]
    assert ref["diagnostics"]["coverage"]["L2"] == {"total": 1, "used": 0}


def test_vocabulary_diagnostics_flag_duplicate_expressions(tmp_path: Path):
    """两个词算同一件事，应该合并成一个。"""
    _write_vocabulary(
        tmp_path,
        "words:\n"
        "  first_word:\n"
        "    doc: 甲\n"
        "    expr: \"char_count > 3\"\n"
        "    examples:\n      positive: ['一二三四']\n      negative: ['一']\n"
        "  second_word:\n"
        "    doc: 乙\n"
        "    expr: \"char_count > 3\"\n"
        "    examples:\n      positive: ['一二三四']\n      negative: ['一']\n",
    )
    dups = vocabulary_reference(tmp_path)["diagnostics"]["duplicate_expr"]
    assert dups and dups[0]["names"] == ["first_word", "second_word"]


def test_vocabulary_reference_lists_lexicons():
    lex = {x["name"]: x for x in vocabulary_reference()["lexicons"]}
    assert lex["math_label_words"]["size"] > 0
    assert lex["math_label_words"]["doc"]


def test_summary_splits_known_and_unknown_pages():
    out = summarize_findings(
        [
            {"suspect_type": "A", "page_no": 3, "playbook": "写规则再 rebuild_ir"},
            {"suspect_type": "B", "page_no": 4},
        ]
    )
    assert out["known_pages"] == [3]
    assert out["unknown_pages"] == [4]
    assert out["by_suspect_type"] == {"A": 1, "B": 1}



def test_yaml_playbook_without_pattern_id_is_a_fix_path():
    """第一本书上也要看得见手册，不能只认模式库里的 pattern_id。"""
    assert finding_has_fix_path({"suspect_type": "ASIDE_FIGURE_MISPLACED_IN_MAIN"})
    assert finding_has_fix_path({"suspect_type": "MATH_FALLBACK"})
    assert not finding_has_fix_path({"suspect_type": "TOTALLY_FAKE_SYMPTOM"})
    known, unknown = partition_findings(
        [
            {"suspect_type": "ASIDE_FIGURE_MISPLACED_IN_MAIN", "page_no": 12},
            {"suspect_type": "NO_SUCH_CHECK", "page_no": 13},
        ]
    )
    assert [f["page_no"] for f in known] == [12]
    assert known[0]["playbook"]
    assert [f["page_no"] for f in unknown] == [13]


def test_builtin_math_fallback_has_playbook():
    text = resolve_playbook("", suspect_type="MATH_FALLBACK")
    assert "math--fallback" in text
    assert "report_false_positive" in text


# ── 声明库回归：Agent 每加一条检测器都自动进这里 ───────────────────────────


def _example_cases() -> list[tuple[str, str, str]]:
    cases: list[tuple[str, str, str]] = []
    for check in load_checks(CHECKS_DIR):
        for rel in check.examples.positive:
            cases.append((check.suspect_type, "positive", rel))
        for rel in check.examples.negative:
            cases.append((check.suspect_type, "negative", rel))
    return cases


@pytest.mark.parametrize("suspect_type, polarity, rel", _example_cases())
def test_declared_checks_match_their_examples(suspect_type: str, polarity: str, rel: str):
    check = next(c for c in load_checks(CHECKS_DIR) if c.suspect_type == suspect_type)
    path = Path(__file__).resolve().parents[1] / rel
    assert path.is_file(), f"{suspect_type} 的样例文件不存在: {rel}"
    blocks, html, page_no = _load_fixture(path)
    hits = run_checks_on_page(blocks, html, page_no=page_no, checks=[check])
    if polarity == "positive":
        assert hits, f"{suspect_type} 漏报了正例 {rel}"
    else:
        assert not hits, f"{suspect_type} 在反例 {rel} 上误报"


def test_every_declared_check_has_both_polarities():
    """没有反例的检测器等于没测过误报率，不许发布。"""
    for check in load_checks(CHECKS_DIR):
        assert check.examples.positive, f"{check.suspect_type} 缺正例"
        assert check.examples.negative, f"{check.suspect_type} 缺反例"


def test_every_playbook_check_resolves_to_steps():
    """模式库存的是指针，步骤真身必须在 YAML 里解析得到，否则下游拿到的是路径。"""
    for check in load_checks(CHECKS_DIR):
        if check.remediation.kind != "playbook":
            continue
        pointer = f"configs/checks/{Path(check.source).name}#remediation.playbook"
        steps = resolve_playbook(pointer, suspect_type=check.suspect_type)
        assert steps.strip(), f"{check.suspect_type} 的手册指针解析不出步骤"
        assert not steps.strip().endswith(".yaml"), "解析结果仍然是路径"


def test_resolve_playbook_falls_back_to_pointer_file(tmp_path: Path):
    """症状名对不上时按文件名解析，老数据里存了步骤原文就原样带回。"""
    (tmp_path / "x.yaml").write_text(
        "suspect_type: DEMO_SYMPTOM\n"
        "detect:\n  scope: block\n  when: \"char_count > 0\"\n"
        "evidence: [zone, text_preview]\n"
        "remediation:\n  kind: playbook\n  locate_by: [zone]\n"
        "  playbook: |\n    1. 照这个做\n",
        encoding="utf-8",
    )
    by_file = resolve_playbook("configs/checks/x.yaml", directory=tmp_path)
    assert "照这个做" in by_file
    # 没绑检测器的模式：ref 里存的就是步骤原文（可能只有一行），原样带回。
    legacy = "1. 定位粘连块；2. 拆开相邻 angle；3. 抽查表格"
    assert resolve_playbook(legacy, directory=tmp_path) == legacy
    # 指针指向的检测器不存在：宁可空着，也不要把路径当步骤发出去。
    assert resolve_playbook("configs/checks/gone.yaml", directory=tmp_path) == ""
    assert (
        resolve_playbook("configs/checks/gone.yaml#remediation.playbook", directory=tmp_path)
        == ""
    )
