"""Corpus ingest locale policy.

非英语学科：label/summary/正文以中文 + 学科符号（公式、希腊字母、化学式等）为主，禁止英文叙述。
英语学科 (subject=english|en)：label/summary/正文应全部为英文。
"""

from __future__ import annotations

import html
import re

ENGLISH_SUBJECTS = frozenset({"english", "en"})

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_WORD_RE = re.compile(r"\b[A-Za-z]{2,}\b")
_MATH_BLOCK_RE = re.compile(r"\$\$[\s\S]*?\$\$|\$[^$]+?\$")
# Strip HTML tags (and their attributes) before scanning: 35B chunk full_text
# is rendered HTML, and tag/attribute tokens like ``article``/``class``/``lb``/
# ``page``/``data`` are not English narrative — counting them was a false alarm.
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Math spans/MathML carry raw LaTeX (``\triangle``, ``\circ`` …) as visible
# text or <annotation> content; they are formulas, never English narrative.
_MATH_SPAN_RE = re.compile(
    r"<span class=\"math[^\"]*\"[^>]*>[\s\S]*?</span>|<math\b[^>]*>[\s\S]*?</math>"
)

# 公式/学科符号中允许的拉丁缩写（非叙述性英文）
_ALLOWED_LATIN = frozenset({
    "sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "lg", "lim", "max", "min",
    "det", "gcd", "lcm", "exp", "mod", "arg", "deg", "rad",
    "alpha", "beta", "gamma", "delta", "epsilon", "theta", "lambda", "mu", "pi", "sigma",
    "phi", "psi", "omega", "rho", "tau", "chi", "eta", "kappa", "nu", "xi", "zeta",
    "sqrt", "frac", "cdot", "times", "div", "pm", "mp", "neq", "leq", "geq", "infty",
    "subset", "supset", "cup", "cap", "vec", "bar", "hat", "overline",
    "left", "right", "begin", "end", "text", "mathrm", "mathbf", "mathit",
    # 常见 LaTeX 命令名（fallback 渲染或标题残片中以裸命令形式出现，非英文叙述）
    "circ", "triangle", "quad", "qquad", "array", "angle", "sim", "simeq",
    "approx", "parallel", "perp", "cong", "mid", "to", "in", "notin", "ni",
    "oplus", "otimes", "odot", "cdots", "ldots", "dots", "vdots", "ddots",
    "emptyset", "varnothing", "degree", "binom", "cases", "matrix", "pmatrix",
    "overrightarrow", "overleftarrow", "widehat", "widetilde", "mathbb", "mathcal",
    "forall", "exists", "because", "therefore", "propto", "equiv", "arc",
    "ce", "pu",  # mhchem 类
    "ok", "id", "no",  # 技术 id 片段
})

# 常见可保留的英文专名（教材版本等），其余应中文化
_ALLOWED_PROPER = frozenset({
    "pdf", "url", "api", "kp", "hs", "pep", "vip",
})


class LocaleValidationError(ValueError):
    pass


def is_english_subject(subject: str) -> bool:
    return (subject or "").strip().lower() in ENGLISH_SUBJECTS


def content_locale(subject: str) -> str:
    return "en" if is_english_subject(subject) else "zh"


def _remove_math_blocks(text: str) -> str:
    return _MATH_BLOCK_RE.sub(" ", text or "")


def _strip_html(text: str) -> str:
    if not text or "<" not in text:
        return text or ""
    # Drop math spans/MathML subtrees (incl. their LaTeX text) before tag removal.
    text = _MATH_SPAN_RE.sub(" ", text)
    return html.unescape(_HTML_TAG_RE.sub(" ", text))


def _flagged_latin_words(text: str) -> list[str]:
    plain = _remove_math_blocks(_strip_html(text))
    out: list[str] = []
    for m in _LATIN_WORD_RE.finditer(plain):
        w = m.group().lower()
        if w in _ALLOWED_LATIN or w in _ALLOWED_PROPER:
            continue
        out.append(m.group())
    return out


def validate_corpus_text(
    subject: str,
    *,
    label: str = "",
    summary: str = "",
    full_text: str = "",
) -> list[str]:
    """Return human-readable validation errors (empty = ok)."""
    errors: list[str] = []
    subj = subject or "math"

    if is_english_subject(subj):
        combined = f"{label}\n{summary}\n{full_text[:500]}"
        if _CJK_RE.search(combined):
            errors.append("英语学科入库内容不应包含中文（除引用原文标注外）")
        if not _LATIN_WORD_RE.search(combined):
            errors.append("英语学科内容应使用英文表述")
        return errors

    for field, text in (("标题", label), ("摘要", summary)):
        bad = _flagged_latin_words(text)
        if bad:
            sample = "、".join(dict.fromkeys(bad[:5]))
            errors.append(f"{field}含英文叙述「{sample}」，请改为中文（公式与符号除外）")

    body_bad = _flagged_latin_words(full_text[:8000] if full_text else "")
    if len(body_bad) > 8:
        sample = "、".join(dict.fromkeys(body_bad[:5]))
        errors.append(f"正文含较多英文叙述（如 {sample}…），请中文化或放入公式块 $...$")
    elif body_bad and not _CJK_RE.search(full_text or ""):
        sample = "、".join(dict.fromkeys(body_bad[:5]))
        errors.append(f"正文以英文为主（{sample}），非英语学科应使用中文")

    if label and not _CJK_RE.search(label):
        # 纯符号标题如「U」「A̅」允许
        if _LATIN_WORD_RE.search(label) and not _MATH_BLOCK_RE.search(label):
            bad = _flagged_latin_words(label)
            if bad:
                errors.append(f"标题「{label}」应使用中文名称")

    return errors


def apply_corpus_locale(
    subject: str,
    *,
    label: str = "",
    summary: str = "",
    full_text: str = "",
    meta: dict | None = None,
) -> tuple[str, str, str, dict, list[str]]:
    """Normalize meta + return validation warnings (non-blocking on ingest)."""
    meta = dict(meta or {})
    loc = content_locale(subject)
    meta["content_locale"] = loc

    # label_en / description_en 仅保留在 meta，不覆盖中文展示字段
    if not is_english_subject(subject):
        if meta.get("label_en") and label == meta.get("label_en"):
            label = meta.get("label") or label
        if meta.get("description_en") and summary == meta.get("description_en"):
            summary = meta.get("description") or summary

    warnings = validate_corpus_text(subject, label=label, summary=summary, full_text=full_text)
    if warnings:
        meta["locale_warnings"] = warnings
    elif "locale_warnings" in meta:
        meta.pop("locale_warnings", None)

    return label, summary, full_text, meta, warnings


def assert_publish_locale(
    subject: str,
    *,
    label: str = "",
    summary: str = "",
    full_text: str = "",
) -> None:
    errors = validate_corpus_text(subject, label=label, summary=summary, full_text=full_text)
    if errors:
        raise LocaleValidationError("；".join(errors))
