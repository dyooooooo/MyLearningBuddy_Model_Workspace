"""v4 架构 golden：防腐层 / 统一富文本管线 / running_header 收敛 / SSR / T1 校验。

台账页映射（doc/archive/2026-07-04-35b-preview-math-issues.md）：
p3 吞块 · p14 残缺 cases · p20/21/26 转义损伤 · p24 表格公式。
纯代码路径（repair → normalize → compile → validate），不调 vLLM。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.import_35b.harness_golden import (
    assert_case_expectations,
    compile_case,
)
from app.import_35b.model_boundary import (
    close_environments,
    has_hard_control_chars,
    repair_blueprint_dict,
    repair_escape_damage,
    repair_string,
)
from app.import_35b.page_compiler import compile_page_html
from app.import_35b.schemas import PageBlueprint
from app.import_35b.ssr_math import ssr_available, ssr_typeset_page
from app.import_35b.validation import run_page_checks
from app.math.normalize import _bridge_available

_V4_CASES_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "eval" / "35b_v4_arch_golden.jsonl"
)


def _load_v4_cases() -> list[dict]:
    rows = []
    for line in _V4_CASES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


# ── 防腐层：JSON 转义损伤白名单还原 ────────────────────────────────────────


def test_repair_escape_damage_whitelist():
    fixed, repairs = repair_escape_damage("若 $\x08eta$ 且 $\x0crac{1}{2}$")
    assert fixed == "若 $\\beta$ 且 $\\frac{1}{2}$"
    assert {r["command"] for r in repairs} == {"beta", "frac"}


def test_repair_escape_damage_newline_tab():
    fixed, _ = repair_escape_damage("（$k \notin \text{N}$）")
    assert "\\notin" in fixed and "\\text{N}" in fixed


def test_repair_no_false_positive_on_prose():
    """换行后跟英文单词（\\nequation 场景）绝不能被误还原。"""
    text = "first line\nequation follows\textra"
    fixed, repairs = repair_escape_damage(text)
    assert fixed == text
    assert repairs == []


def test_close_environments_stack():
    fixed, r = close_environments("\\begin{cases} x=1, \\\\ y=2.")
    assert fixed.endswith("\\end{cases}")
    assert r and r[0]["env"] == "cases"
    # 已配对的不动
    ok = "\\begin{cases} a \\end{cases}"
    assert close_environments(ok) == (ok, [])


def test_repair_string_closes_env_inside_dollars():
    text = "解得 $\\begin{cases} x=4, \\\\ y=0.$ 或者"
    fixed, _ = repair_string(text)
    assert "\\end{cases}$" in fixed  # \end 落在 $ 之内


def test_repair_blueprint_dict_walks_paths():
    raw = {
        "blocks": [
            {
                "content": {
                    "equation": {"latex": "\\begin{cases} 2x=4, \\\\ x+y=8"},
                    "text": {"paragraphs": ["$\x08eta$"]},
                }
            }
        ]
    }
    fixed, repairs = repair_blueprint_dict(raw)
    assert fixed["blocks"][0]["content"]["equation"]["latex"].endswith("\\end{cases}")
    assert fixed["blocks"][0]["content"]["text"]["paragraphs"][0] == "$\\beta$"
    assert any(r["rule"] == "env_autoclose" for r in repairs)
    assert any(r["rule"] == "ctrl_b" for r in repairs)


def test_hard_control_char_gate():
    assert has_hard_control_chars("90^\x08ad")
    assert not has_hard_control_chars("normal $x$ text\nwith newline")


# ── running_header 收敛（p3 吞块）─────────────────────────────────────────


def _bp(blocks: list[dict], page_no: int = 3) -> PageBlueprint:
    return PageBlueprint.model_validate(
        {"page_no": page_no, "page_size": [1000, 1000], "blocks": blocks}
    )


def _text_block_dict(bid: str, paragraphs: list[str], *, role: str, zone: str = "main") -> dict:
    return {
        "id": bid,
        "kind": "text",
        "content_type": "text",
        "zone": zone,
        "role": role,
        "bbox": [100, 100, 900, 400],
        "content": {"text": {"paragraphs": paragraphs}},
    }


def test_running_header_role_mislabel_stays_in_main():
    """p3：多段长文本被误标 role=running_header，v4 必须留在 main 渲染。"""
    bp = _bp(
        [
            _text_block_dict(
                "b1",
                ["高中数学必修第一册", "主编：某某某", "编委：甲、乙、丙、丁", "出版社说明文字"],
                role="running_header",
            ),
            _text_block_dict("b2", ["图书在版编目（CIP）数据，一段很长的版权页说明文字，远超页眉长度上限。"], role="running_header"),
        ]
    )
    html = compile_page_html(bp)
    assert "主编" in html and "CIP" in html
    findings = run_page_checks(bp, html)
    assert {f.suspect_type for f in findings} >= {"ROLE_ZONE_MISMATCH"}
    assert not any(f.suspect_type == "MAIN_EMPTY" for f in findings)


def test_running_header_short_single_line_still_header():
    bp = _bp(
        [
            _text_block_dict("h1", ["1.1 集合初步"], role="running_header"),
            _text_block_dict("b1", ["正文内容一段。"], role="body"),
        ]
    )
    html = compile_page_html(bp)
    assert '<header class="lb-running-hd">1.1 集合初步</header>' in html
    # 页眉块不重复出现在 main
    assert html.count("1.1 集合初步") == 1


def test_running_zone_block_always_header():
    bp = _bp(
        [
            {
                "id": "ft",
                "kind": "running",
                "content_type": "text",
                "zone": "running",
                "role": "running_header",
                "bbox": [0, 0, 1000, 30],
                "content": {"text": {"paragraphs": ["1 集合与逻辑"]}},
            },
            _text_block_dict("b1", ["正文。"], role="body"),
        ]
    )
    html = compile_page_html(bp)
    assert "lb-running-hd" in html and "1 集合与逻辑" in html


# ── T1 校验规则 ────────────────────────────────────────────────────────────


def test_finding_main_empty():
    bp = _bp([_text_block_dict("b1", [""], role="body")])
    html = compile_page_html(bp)
    findings = run_page_checks(bp, html)
    assert any(f.suspect_type == "MAIN_EMPTY" for f in findings)


def test_finding_main_with_content_not_flagged():
    bp = _bp(
        [
            _text_block_dict("b1", ["正文内容。"], role="body"),
            _text_block_dict("a1", ["旁注内容。"], role="note", zone="aside_right"),
        ]
    )
    html = compile_page_html(bp)
    findings = run_page_checks(bp, html)
    assert not any(f.suspect_type == "MAIN_EMPTY" for f in findings)


def test_finding_control_char_residue():
    bp = _bp([_text_block_dict("b1", ["残留 \x08ad 损伤"], role="body")])
    html = compile_page_html(bp)
    findings = run_page_checks(bp, html)
    assert any(f.suspect_type == "CONTROL_CHAR_RESIDUE" for f in findings)


def test_finding_fingerprints_stable():
    bp = _bp([_text_block_dict("b1", ["残留 \x08ad 损伤"], role="body")])
    html = compile_page_html(bp)
    f1 = run_page_checks(bp, html)
    f2 = run_page_checks(bp, html)
    assert [f.fingerprint for f in f1] == [f.fingerprint for f in f2]
    assert all(f.fingerprint and f.fingerprint_exact for f in f1)


# ── v4 golden 页 case（需要 math 桥）──────────────────────────────────────

_bridge = pytest.mark.skipif(
    not _bridge_available(), reason="math bridge (node_modules) unavailable"
)


@_bridge
@pytest.mark.parametrize("case", _load_v4_cases(), ids=lambda c: c["id"])
def test_v4_golden_case(case):
    bp, html = compile_case(case)
    assert_case_expectations(case, bp, html)


# ── SSR 自包含 ─────────────────────────────────────────────────────────────


@pytest.mark.skipif(not ssr_available(), reason="mathjax-full unavailable")
def test_ssr_page_self_contained():
    bp = _bp([_text_block_dict("b1", ["函数 $f(x) = x^{2}$ 的图像。"], role="body")], page_no=1)
    from app.import_35b.normalize import normalize_blueprint

    html = compile_page_html(normalize_blueprint(bp))
    assert "<svg" in html
    assert 'data-latex="' not in html  # 只剩 data-latex-src 元数据
    assert 'data-latex-src="' in html


@pytest.mark.skipif(not ssr_available(), reason="mathjax-full unavailable")
def test_ssr_error_visible_marker():
    report: list[dict] = []
    html = ssr_typeset_page(
        '<p><span class="math math--inline" data-latex="\\badcmd{"></span></p>',
        report=report,
    )
    assert "math--error" in html
    assert report and report[0]["suspect_type"] == "MATH_RENDER_FAILED"
