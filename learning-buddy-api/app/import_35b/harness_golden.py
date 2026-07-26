"""Golden regression harness for 35B import (layout_blueprint_v5).

Loads ``data/eval/35b_schema_v5_golden.json`` and
``data/eval/35b_inline_math_v5_golden.jsonl``; runs repair → normalize → compile
and asserts HTML / LaTeX quality gates without a live vLLM call.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.import_35b.metrics import (
    inline_math_span_health,
    page_quality_metrics,
    paragraph_fidelity,
)
from app.import_35b.normalize import normalize_blueprint
from app.import_35b.page_compiler import compile_page_html
from app.import_35b.schemas import PageBlueprint

_EVAL_DIR = Path(__file__).resolve().parents[2] / "data" / "eval"
SPEC_PATH = _EVAL_DIR / "35b_schema_v5_golden.json"
CASES_PATH = _EVAL_DIR / "35b_inline_math_v5_golden.jsonl"

_INLINE_LATEX_RE = re.compile(r'math--inline[^"]*" data-latex(?:-src)?="([^"]*)"')
_DISPLAY_LATEX_RE = re.compile(r'math--display[^"]*" data-latex(?:-src)?="([^"]*)"')
_ANY_DATA_LATEX_RE = re.compile(r'data-latex(?:-src)?="([^"]*)"')
_FALLBACK_RE = re.compile(r'math--fallback')
# AsciiMath debris after stripping real LaTeX commands like \in, \infty, \int.
_ASCII_IN_TOKEN_RE = re.compile(r"(?<![\\a-zA-Z])in(?![a-zA-Z])")
_BARE_UNICODE_MATH = re.compile(r"[∈∩∪√∉⊆⊇≤≥≠∞]")

_PROMPT_VERSION = "layout_blueprint_v6"


def load_spec() -> dict[str, Any]:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def load_inline_cases() -> list[dict[str, Any]]:
    if not CASES_PATH.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in CASES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _text_block(
    paragraphs: list[str],
    *,
    page_no: int,
    bid: str = "b1",
    role: str = "body",
) -> PageBlueprint:
    return PageBlueprint.model_validate(
        {
            "page_no": page_no,
            "page_size": [1000, 1000],
            "blocks": [
                {
                    "id": bid,
                    "kind": "text",
                    "content_type": "text",
                    "zone": "main",
                    "role": role,
                    "bbox": [100, 100, 900, 400],
                    "content": {"text": {"paragraphs": paragraphs}},
                }
            ],
        }
    )


def _table_block(
    rows: list[list[str]],
    *,
    page_no: int,
    bid: str = "t1",
) -> PageBlueprint:
    return PageBlueprint.model_validate(
        {
            "page_no": page_no,
            "page_size": [1000, 1000],
            "blocks": [
                {
                    "id": bid,
                    "kind": "table",
                    "content_type": "table",
                    "zone": "main",
                    "role": "table",
                    "bbox": [100, 100, 900, 400],
                    "content": {"table": {"rows": rows}},
                }
            ],
        }
    )


def _equation_block(
    *,
    page_no: int,
    latex: str | None,
    fallback_text: str | None = None,
    bid: str = "e1",
) -> PageBlueprint:
    return PageBlueprint.model_validate(
        {
            "page_no": page_no,
            "page_size": [1000, 1000],
            "blocks": [
                {
                    "id": bid,
                    "kind": "equation",
                    "content_type": "equation",
                    "zone": "main",
                    "role": "equation",
                    "bbox": [300, 350, 480, 372],
                    "content": {"equation": {"latex": latex, "fallback_text": fallback_text}},
                }
            ],
        }
    )


def _figure_block(
    *,
    page_no: int,
    caption: str | None,
    bid: str = "f1",
    asset_url: str | None = None,
    bbox: list[float] | None = None,
) -> PageBlueprint:
    content: dict[str, Any] = {"figure": {"caption": caption}}
    if asset_url:
        content["asset_url"] = asset_url
    return PageBlueprint.model_validate(
        {
            "page_no": page_no,
            "page_size": [1000, 1000],
            "blocks": [
                {
                    "id": bid,
                    "kind": "figure",
                    "content_type": "figure",
                    "zone": "main",
                    "role": "figure",
                    "bbox": bbox or [120, 120, 900, 320],
                    "content": content,
                }
            ],
        }
    )


def _text_with_figure_caption(
    *,
    page_no: int,
    caption: str,
    bid: str = "b1",
) -> PageBlueprint:
    """p3 mis-label: kind=text but prose in figure.caption."""
    return PageBlueprint.model_validate(
        {
            "page_no": page_no,
            "page_size": [1000, 1000],
            "blocks": [
                {
                    "id": bid,
                    "kind": "text",
                    "content_type": "text",
                    "zone": "main",
                    "role": "body",
                    "bbox": [100, 100, 900, 200],
                    "content": {
                        "text": None,
                        "figure": {"caption": caption},
                    },
                }
            ],
        }
    )


def blueprint_from_case(case: dict[str, Any]) -> PageBlueprint | None:
    if case.get("expect_v4_model_output") is False:
        return None
    page_no = int(case.get("page_no") or 1)
    kind = case.get("kind")
    if kind == "table":
        return _table_block(case["table_rows"], page_no=page_no, bid=f"p{page_no}_t1")
    if kind == "equation":
        return _equation_block(
            page_no=page_no,
            latex=case.get("latex"),
            fallback_text=case.get("fallback_text"),
            bid=f"p{page_no}_e1",
        )
    if kind == "figure":
        return _figure_block(
            page_no=page_no,
            caption=case.get("caption"),
            bid=f"p{page_no}_f1",
            asset_url=case.get("asset_url"),
            bbox=case.get("bbox"),
        )
    if kind == "text_with_figure_caption":
        return _text_with_figure_caption(
            page_no=page_no,
            caption=str(case.get("caption") or ""),
            bid=f"p{page_no}_b1",
        )
    if kind == "aside_gutter":
        return PageBlueprint.model_validate(
            {
                "page_no": page_no,
                "page_size": [1000, 1000],
                "blocks": [
                    {
                        "id": f"p{page_no}_a1",
                        "kind": "text",
                        "zone": "main",
                        "role": "body",
        "bbox": case.get("bbox") or [80, 200, 250, 600],
                        "content": {
                            "text": {
                                "paragraphs": case.get("paragraphs")
                                or ["康托(G. Cantor, 1845-1918), 德国数学家。"]
                            }
                        },
                    }
                ],
            }
        )
    paragraphs = case.get("paragraphs") or []
    return _text_block(paragraphs, page_no=page_no, bid=f"p{page_no}_b1")


def compile_case(case: dict[str, Any]) -> tuple[PageBlueprint, str]:
    bp = blueprint_from_case(case)
    if bp is None:
        raise ValueError("case skipped")
    # v4 净化面在 model_validate 前跑；这里对 case 数据重放同一顺序
    # （repair → validate → normalize → compile），干净数据是 no-op。
    from app.import_35b.model_boundary import repair_blueprint_dict

    raw, _repairs = repair_blueprint_dict(bp.model_dump(mode="json"))
    bp = PageBlueprint.model_validate(raw)
    bp = normalize_blueprint(bp)
    html = compile_page_html(bp)
    return bp, html


def inline_latex_spans(html: str) -> list[str]:
    return _INLINE_LATEX_RE.findall(html or "")


def display_latex_spans(html: str) -> list[str]:
    return _DISPLAY_LATEX_RE.findall(html or "")


def all_data_latex(html: str) -> list[str]:
    return _ANY_DATA_LATEX_RE.findall(html or "")


def has_ascii_in_token(latex_blob: str) -> bool:
    """Detect AsciiMath ``in`` token left after stripping ``\\in`` commands."""
    scrubbed = latex_blob
    for cmd in ("\\in", "\\infty", "\\int", "\\inf", "\\inside"):
        scrubbed = scrubbed.replace(cmd, "")
    return bool(_ASCII_IN_TOKEN_RE.search(scrubbed))


def paragraph_has_bare_unicode(paragraphs: list[str]) -> bool:
    for para in paragraphs:
        # Strip v4 $...$ regions before scanning for bare glyphs.
        stripped = re.sub(r"\$[^$]+\$", "", para)
        if _BARE_UNICODE_MATH.search(stripped):
            return True
    return False


def check_html_latex_quality(html: str, *, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = spec or load_spec()
    acceptance = spec.get("acceptance") or {}
    spans = inline_latex_spans(html)
    blob = " ".join(spans)
    forbid = acceptance.get("forbid_data_latex_patterns") or []
    bad_patterns = [p for p in forbid if p in blob]
    ascii_in = has_ascii_in_token(blob) if acceptance.get("inline_math_forbid_ascii_in_token") else False
    fallbacks = len(_FALLBACK_RE.findall(html or ""))
    valid_spans = len(spans)
    valid_rate = 1.0 if valid_spans else 1.0
    if valid_spans and fallbacks:
        valid_rate = round((valid_spans - fallbacks) / valid_spans, 4)
    return {
        "inline_math_spans": valid_spans,
        "math_fallback_count": fallbacks,
        "valid_rate": valid_rate,
        "ascii_in_token": ascii_in,
        "forbidden_patterns": bad_patterns,
        "oversized": inline_math_span_health(html).get("oversized_inline_math", False),
    }


def assert_case_expectations(case: dict[str, Any], bp: PageBlueprint, html: str) -> None:
    expect = case.get("expect") or {}
    quality = check_html_latex_quality(html)

    if expect.get("no_math_fallback"):
        assert quality["math_fallback_count"] == 0, (
            f"{case['id']}: unexpected math--fallback in HTML"
        )

    min_spans = expect.get("min_valid_math_spans")
    if min_spans is not None:
        assert quality["inline_math_spans"] >= min_spans, (
            f"{case['id']}: expected >={min_spans} inline math spans, got {quality}"
        )

    if expect.get("no_ascii_in_token"):
        assert not quality["ascii_in_token"], f"{case['id']}: AsciiMath 'in' token in data-latex"

    for needle in expect.get("latex_must_contain") or []:
        blob = " ".join(inline_latex_spans(html))
        assert needle in blob, f"{case['id']}: missing {needle!r} in {blob!r}"

    if expect.get("expected_paragraphs") is not None:
        pf = paragraph_fidelity(bp, html)
        assert pf["expected_paragraphs"] == expect["expected_paragraphs"], pf
        ratio_min = expect.get("paragraph_ratio_min", 1.0)
        assert pf["paragraph_ratio"] >= ratio_min, pf

    if expect.get("table_has_dollar_latex_cells"):
        assert "$\\alpha$" in html or "data-latex" in html, case["id"]

    if expect.get("no_raw_backslash_alpha_in_td"):
        # Raw escaped ``$\\alpha$`` in td without math span is the v3 table bug.
        assert '$\\alpha$' not in html or 'data-latex="\\alpha"' in html, case["id"]

    # ── v5: equation / figure guarantees ──────────────────────────────────
    span_needle = expect.get("equation_span_contains")
    if span_needle is not None:
        blob = " ".join(display_latex_spans(html))
        assert span_needle in blob, (
            f"{case['id']}: expected display span to contain {span_needle!r}, got {blob!r}"
        )

    forbid_substr = expect.get("forbid_data_latex_substr")
    if forbid_substr is not None:
        for span in all_data_latex(html):
            assert forbid_substr not in span, (
                f"{case['id']}: broken latex leaked into data-latex: {span!r}"
            )

    for needle in expect.get("html_must_contain") or []:
        assert needle in html, f"{case['id']}: expected HTML to contain {needle!r}"

    if expect.get("no_figure_tag"):
        assert "<figure" not in html, f"{case['id']}: body-text caption must not render as <figure>"

    if expect.get("figure_tag"):
        assert "<figure" in html, f"{case['id']}: real figure should render a <figure> element"

    if expect.get("no_table_tag"):
        assert "<table" not in html, f"{case['id']}: TOC must not render as <table>"

    if expect.get("toc_list"):
        assert 'class="lb-toc"' in html, f"{case['id']}: expected lb-toc list"

    if expect.get("aside_left"):
        assert any(b.zone == "aside_left" for b in bp.blocks), (
            f"{case['id']}: expected aside_left zone after normalize"
        )
        assert "lb-aside--left" in html or "aside_left" in html, (
            f"{case['id']}: expected left aside in HTML"
        )

    if expect.get("no_main_empty"):
        from app.import_35b.validation import run_page_checks

        hard = {
            f.suspect_type
            for f in run_page_checks(bp, html)
            if f.suspect_type == "MAIN_EMPTY"
        }
        assert not hard, f"{case['id']}: MAIN_EMPTY after repair: {hard}"


def assert_spec_aggregate(spec: dict[str, Any], per_page_metrics: list[dict]) -> None:
    acc = spec.get("acceptance") or {}
    from app.import_35b.metrics import aggregate_quality

    agg = aggregate_quality(per_page_metrics)
    if acc.get("html_block_coverage_min") is not None:
        cov = agg["html_block_coverage"]["coverage"]
        assert cov >= acc["html_block_coverage_min"], agg
    if acc.get("polluted_payload_count_max") is not None:
        assert agg["polluted_payload_count"] <= acc["polluted_payload_count_max"], agg
    if acc.get("min_avg_paragraph_ratio") is not None:
        assert agg["avg_paragraph_ratio"] >= acc["min_avg_paragraph_ratio"], agg
    allowed = acc.get("pages_with_oversized_inline_math")
    if allowed is not None:
        assert agg["pages_with_oversized_inline_math"] == allowed, agg


def prompt_version_matches() -> bool:
    from app.import_35b.vision_layout import PROMPT_VERSION

    return PROMPT_VERSION == _PROMPT_VERSION


__all__ = [
    "CASES_PATH",
    "SPEC_PATH",
    "all_data_latex",
    "assert_case_expectations",
    "assert_spec_aggregate",
    "blueprint_from_case",
    "check_html_latex_quality",
    "compile_case",
    "display_latex_spans",
    "has_ascii_in_token",
    "inline_latex_spans",
    "load_inline_cases",
    "load_spec",
    "paragraph_has_bare_unicode",
    "prompt_version_matches",
]
