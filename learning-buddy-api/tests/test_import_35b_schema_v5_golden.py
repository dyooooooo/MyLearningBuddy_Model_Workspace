"""Golden regression for layout_blueprint_v5.

Covers the v4 inline-LaTeX harness plus v5 additions:
- AMS symbols (\\leqslant / \\varnothing) validate and render (server bridge).
- Equation blocks emit only server-validated LaTeX; broken LaTeX never leaks
  into a ``data-latex`` span.
- A figure whose caption is body text is reclassified as a text block.
- The MFR gate rejects degenerate output and out-of-range bboxes.
"""
from __future__ import annotations

import pytest

from app.import_35b.harness_golden import (
    assert_case_expectations,
    assert_spec_aggregate,
    blueprint_from_case,
    check_html_latex_quality,
    compile_case,
    load_inline_cases,
    load_spec,
    paragraph_has_bare_unicode,
    prompt_version_matches,
)
from app.import_35b.metrics import page_quality_metrics

_NON_AGGREGATE_KINDS = {"equation", "figure"}


def test_prompt_version_is_v5() -> None:
    spec = load_spec()
    assert prompt_version_matches()
    assert spec["prompt_target"] == "layout_blueprint_v5"


def test_v5_spec_root_causes_documented() -> None:
    spec = load_spec()
    causes = " ".join(spec.get("root_causes") or [])
    assert "figure" in causes and "MFR" in causes
    acc = spec["acceptance"]
    assert acc["equation_latex_must_be_server_validated"] is True
    assert acc["figure_caption_body_text_reclassified_as_text"] is True


@pytest.mark.parametrize("case", load_inline_cases(), ids=lambda c: c.get("id", "?"))
def test_v5_golden_case(case: dict) -> None:
    if case.get("expect_v4_model_output") is False:
        assert paragraph_has_bare_unicode(case["paragraphs"])
        return
    bp, html = compile_case(case)
    assert_case_expectations(case, bp, html)
    if case.get("kind") not in _NON_AGGREGATE_KINDS:
        metrics = page_quality_metrics(bp, html)
        assert not metrics["inline_math_span_health"]["oversized_inline_math"]


def test_v5_golden_aggregate_spec() -> None:
    spec = load_spec()
    per_page = []
    for case in load_inline_cases():
        if case.get("expect_v4_model_output") is False:
            continue
        if case.get("kind") in _NON_AGGREGATE_KINDS:
            continue
        bp, html = compile_case(case)
        per_page.append(page_quality_metrics(bp, html))
    assert_spec_aggregate(spec, per_page)


def test_v5_dollar_latex_no_ascii_in_debris() -> None:
    _, html = compile_case(
        {
            "id": "smoke",
            "page_no": 10,
            "paragraphs": [r"因为 $x \in \mathbb{N}$ 且 $y \notin \mathbb{Q}$."],
            "expect": {},
        }
    )
    q = check_html_latex_quality(html)
    assert q["ascii_in_token"] is False
    assert q["math_fallback_count"] == 0
    assert q["inline_math_spans"] >= 2


# ── MFR gate unit tests (deterministic: regex + geometry) ───────────────────

def test_mfr_looks_like_single_formula() -> None:
    from app.import_35b.mfr import _looks_like_single_formula

    assert _looks_like_single_formula([300, 350, 480, 372]) is True  # short & narrow
    assert _looks_like_single_formula([50, 100, 950, 800]) is False  # whole-page block
    assert _looks_like_single_formula([100, 100, 900, 400]) is False  # tall text block
    assert _looks_like_single_formula([100, 100, 100, 100]) is False  # zero area


def test_mfr_output_sane_rejects_degenerate() -> None:
    from app.import_35b.mfr import _mfr_output_sane

    degenerate = r"\frac{i}{2}" * 8
    assert _mfr_output_sane(degenerate) is False
    assert _mfr_output_sane("x" * 500) is False  # too long
    assert _mfr_output_sane("") is False
