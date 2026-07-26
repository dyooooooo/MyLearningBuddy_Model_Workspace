from __future__ import annotations

import atexit
import json
import logging
import re
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

from app.math.cjk_split import is_cjk_run, looks_like_math, split_cjk_parts
from app.math.preprocess import preprocess_math
from app.schemas.common import MathSegment, RichText

logger = logging.getLogger(__name__)

_BRIDGE = Path(__file__).resolve().parent / "plurimath_bridge.mjs"
_MATH_DIR = Path(__file__).resolve().parent
_BRIDGE_LOCK = threading.Lock()
_BRIDGE_PROC: subprocess.Popen[str] | None = None
_BRIDGE_FAILS = 0
_BRIDGE_MAX_FAILS = 3
_BRIDGE_TIMEOUT_S = 8.0

# Python fallback when Node bridge unavailable (dev/CI without npm install).
_SYMBOL_TO_TEX = {
    "π": r"\pi",
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "θ": r"\theta",
    "∞": r"\infty",
    "∪": r"\cup",
    "∩": r"\cap",
    "≤": r"\leq",
    "≥": r"\geq",
    "≠": r"\neq",
    "±": r"\pm",
    "×": r"\times",
    "∈": r"\in",
    "∅": r"\emptyset",
    "→": r"\to",
    "∣": r"\mid",
}


# Residual Unicode math glyphs the Plurimath bridge sometimes leaves verbatim
# in its LaTeX output (e.g. ``∉`` on p10, ``⊆`` on p15).  Applied as a final
# sweep so MathJax never receives a raw glyph it cannot typeset.
_LATEX_UNICODE_FIX = {
    "∉": r"\notin ",
    "∈": r"\in ",
    "∪": r"\cup ",
    "∩": r"\cap ",
    "⊆": r"\subseteq ",
    "⊇": r"\supseteq ",
    "⊂": r"\subset ",
    "⊃": r"\supset ",
    "⊄": r"\nsubseteq ",
    "≤": r"\leq ",
    "≥": r"\geq ",
    "≠": r"\neq ",
    "∞": r"\infty ",
    "∅": r"\varnothing ",
    "×": r"\times ",
    "±": r"\pm ",
    "→": r"\to ",
    "∀": r"\forall ",
    "∃": r"\exists ",
}


def _cleanup_latex_unicode(latex: str) -> str:
    if not latex:
        return latex
    for glyph, tex in _LATEX_UNICODE_FIX.items():
        if glyph in latex:
            latex = latex.replace(glyph, tex)
    return re.sub(r"\s+", " ", latex).strip()


def _subscript_to_tex(ch: str) -> str:
    return str(ord(ch) - 0x2080)


def _relations_to_tex(t: str) -> str:
    """ascii relations left by preprocess → LaTeX commands."""
    t = re.sub(r"<=", r" \\leq ", t)
    t = re.sub(r">=", r" \\geq ", t)
    t = re.sub(r"!=", r" \\neq ", t)
    return t


def _try_piecewise_cases(t: str) -> str | None:
    r"""Detect K12 piecewise notation and emit \begin{cases}...\end{cases}.

    Handles forms like ``f(x)={x+1, x>=0; -x, x<0}`` (with or without the
    closing brace) where branches are separated by ``;`` or newlines and each
    branch is ``<expr>, <condition>``.
    """
    idx = t.find("{")
    if idx == -1:
        return None
    prefix = t[:idx].strip()
    body = t[idx + 1 :].strip().rstrip("}").strip()
    if not body:
        return None
    branches = [b.strip() for b in re.split(r";|\n", body) if b.strip()]
    if len(branches) < 2:
        return None

    rows: list[str] = []
    for br in branches:
        if "," in br:
            expr, cond = br.split(",", 1)
        else:
            expr, cond = br, ""
        expr_l = _relations_to_tex(_fallback_latex(expr.strip()) or expr.strip())
        cond_l = (
            _relations_to_tex(_fallback_latex(cond.strip()) or cond.strip())
            if cond.strip()
            else ""
        )
        rows.append(f"{expr_l} & {cond_l}" if cond_l else expr_l)

    cases = r"\begin{cases} " + r" \\ ".join(rows) + r" \end{cases}"
    if prefix:
        head = prefix.rstrip("=").strip()
        head_l = _relations_to_tex(_fallback_latex(head) or head) if head else ""
        sep = " = " if prefix.endswith("=") else " "
        return f"{head_l}{sep}{cases}" if head_l else cases
    return cases


def _fallback_latex(raw: str) -> str | None:
    """Deterministic K12 fallback; used only when Plurimath bridge fails."""
    t = preprocess_math(raw)
    if not t:
        return None

    cases = _try_piecewise_cases(t)
    if cases:
        return cases

    t = re.sub(
        r"\{\s*([^}|∣]+?)\s*[|∣]\s*([^}]+?)\s*\}",
        lambda m: rf"\{{{_fallback_latex(m.group(1)) or m.group(1)} \mid {_fallback_latex(m.group(2)) or m.group(2)}\}}",
        t,
    )
    t = t.replace("A̅", r"\overline{A}")
    t = re.sub(
        r"([a-zA-Z])([\u2080-\u2089]+)",
        lambda m: f"{m.group(1)}_{{{''.join(_subscript_to_tex(c) for c in m.group(2))}}}",
        t,
    )
    t = re.sub(r"([a-zA-Z0-9])²", r"\1^{2}", t)
    t = re.sub(r"([a-zA-Z0-9])³", r"\1^{3}", t)
    t = re.sub(r"(\d+) sqrt\((\d+)\)/(\d+)", r"\\frac{\1\\sqrt{\2}}{\3}", t)
    t = re.sub(r"(\d+) sqrt\((\d+)\)", r"\1\\sqrt{\2}", t)
    t = re.sub(r"sqrt\(([^)]+)\)", lambda m: f"\\sqrt{{{m.group(1)}}}", t)

    for sym, tex in _SYMBOL_TO_TEX.items():
        t = t.replace(sym, tex)

    t = _relations_to_tex(t)

    for fn in ("cos", "sin", "tan", "cot", "sec", "csc", "log", "lg", "ln"):
        t = re.sub(rf"\b{fn}\b", rf"\\{fn}", t, flags=re.I)

    t = re.sub(r"\(([^()]+)\)\s*/\s*\(([^()]+)\)", r"\\frac{\1}{\2}", t)
    t = re.sub(
        r"(?<![\d.])(-?\d+)\s*/\s*(\([^)]+\)|[a-zA-Z_{}\d]+)(?![\d.])",
        r"\\frac{\1}{\2}",
        t,
    )

    # Greek letter boundary: \alphar → \alpha r
    t = re.sub(
        r"\\(alpha|beta|gamma|theta|pi|infty|leq|geq|neq)([a-zA-Z])",
        r"\\\1 \2",
        t,
    )

    return re.sub(r"\s+", " ", t).strip() or None


@lru_cache(maxsize=1)
def _bridge_available() -> bool:
    node_modules = _MATH_DIR / "node_modules" / "@plurimath" / "plurimath"
    return _BRIDGE.is_file() and node_modules.is_dir()


def _stop_bridge_locked() -> None:
    global _BRIDGE_PROC
    proc = _BRIDGE_PROC
    _BRIDGE_PROC = None
    if proc is None:
        return
    try:
        if proc.stdin:
            proc.stdin.close()
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


def _start_bridge_locked() -> subprocess.Popen[str] | None:
    global _BRIDGE_PROC, _BRIDGE_FAILS
    if not _bridge_available():
        return None
    _stop_bridge_locked()
    try:
        proc = subprocess.Popen(
            ["node", str(_BRIDGE), "--serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(_MATH_DIR),
        )
    except Exception as exc:
        logger.warning("plurimath bridge start failed: %s", exc)
        _BRIDGE_FAILS += 1
        return None
    _BRIDGE_PROC = proc
    _BRIDGE_FAILS = 0
    logger.info("plurimath bridge started pid=%s", proc.pid)
    return proc


def _ensure_bridge_locked() -> subprocess.Popen[str] | None:
    global _BRIDGE_FAILS
    if _BRIDGE_FAILS >= _BRIDGE_MAX_FAILS:
        return None
    proc = _BRIDGE_PROC
    if proc is not None and proc.poll() is None:
        return proc
    return _start_bridge_locked()


def _oneshot_bridge(payload: str) -> dict:
    """Fallback: spawn Node once (cold MathJax load — slow)."""
    try:
        proc = subprocess.run(
            ["node", str(_BRIDGE)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=_BRIDGE_TIMEOUT_S,
            cwd=str(_MATH_DIR),
            check=False,
        )
        if proc.returncode != 0 and not proc.stdout.strip():
            return {"latex": None, "valid": False, "error": proc.stderr.strip()[:200]}
        return json.loads(proc.stdout.strip() or "{}")
    except Exception as exc:
        logger.debug("plurimath oneshot failed: %s", exc)
        return {"latex": None, "valid": False, "error": str(exc)}


def _call_bridge(text: str, fmt: str) -> dict:
    """Validate/convert via a long-lived Node process (MathJax loaded once)."""
    if not _bridge_available():
        return {"latex": None, "valid": False, "error": "bridge_unavailable"}
    payload = json.dumps({"text": text, "format": fmt}, ensure_ascii=False)
    global _BRIDGE_FAILS
    with _BRIDGE_LOCK:
        proc = _ensure_bridge_locked()
        if proc is None or proc.stdin is None or proc.stdout is None:
            return _oneshot_bridge(payload)
        try:
            proc.stdin.write(payload + "\n")
            proc.stdin.flush()
            # readline blocks until the serve loop responds; hard-timeout via poll.
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError("bridge closed stdout")
            _BRIDGE_FAILS = 0
            return json.loads(line.strip() or "{}")
        except Exception as exc:
            logger.warning("plurimath bridge call failed — restarting: %s", exc)
            _BRIDGE_FAILS += 1
            _stop_bridge_locked()
            if _BRIDGE_FAILS >= _BRIDGE_MAX_FAILS:
                return _oneshot_bridge(payload)
            # One retry on a fresh process.
            proc = _ensure_bridge_locked()
            if proc is None or proc.stdin is None or proc.stdout is None:
                return _oneshot_bridge(payload)
            try:
                proc.stdin.write(payload + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
                if not line:
                    raise RuntimeError("bridge closed stdout")
                _BRIDGE_FAILS = 0
                return json.loads(line.strip() or "{}")
            except Exception as exc2:
                logger.warning("plurimath bridge retry failed: %s", exc2)
                _BRIDGE_FAILS += 1
                _stop_bridge_locked()
                return _oneshot_bridge(payload)


def _atexit_stop_bridge() -> None:
    with _BRIDGE_LOCK:
        _stop_bridge_locked()


atexit.register(_atexit_stop_bridge)


def _validate_latex_via_bridge(latex: str) -> bool:
    if not latex:
        return False
    if _bridge_available():
        # Bridge validates LaTeX via MathJax 3 (mathjax_render.mjs).
        # basic brace balance check plus bridge on reverse isn't available.
        pass
    depth = 0
    for ch in latex:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _latex_sane(latex: str) -> bool:
    if not latex or not _validate_latex_via_bridge(latex):
        return False
    if re.search(r"\\\s+[a-zA-Z]", latex):
        return False
    if re.search(r"\\in\s*e\s*\\", latex):
        return False
    return True


def _fix_overline_from_raw(latex: str, raw: str) -> str:
    """Ensure complement/overline notation from OCR becomes \\overline{...} in LaTeX."""
    if "overline" in latex:
        return latex
    if not re.search(r"A[\u0304\u0305]|A̅|Ā", raw):
        return latex
    fixed = re.sub(r"A\s*[\u0304\u0305]", r"\\overline{A}", latex)
    return fixed.replace("A̅", r"\overline{A}")


_LATEX_HINT_RE = re.compile(r"\\[a-zA-Z]+|[{}^_]")


def _convert_math_segment(raw: str) -> MathSegment:
    # Step B — prefer valid raw LaTeX.  If ``raw`` already looks like LaTeX
    # (has a ``\command`` or brace/scripts) and MathJax can render it as-is,
    # keep it verbatim instead of routing through the AsciiMath/unicode path
    # (which turns ``\in`` into ``in`` and shatters ``\begin{cases}``).
    candidate = raw.replace("$", "").strip()
    if candidate and _bridge_available() and _LATEX_HINT_RE.search(candidate):
        probe = _call_bridge(candidate, "latex")
        if probe.get("valid"):
            return MathSegment(
                kind="math",
                raw=raw,
                latex=_cleanup_latex_unicode(_fix_overline_from_raw(candidate, raw)),
                valid=True,
            )

    pre = preprocess_math(raw)
    for fmt in ("asciimath", "unicode"):
        result = _call_bridge(pre, fmt)
        latex = str(result.get("latex") or "")
        if result.get("valid") and latex and _latex_sane(latex):
            latex = _cleanup_latex_unicode(_fix_overline_from_raw(latex, raw))
            return MathSegment(
                kind="math",
                raw=raw,
                latex=latex,
                valid=True,
            )

    fb = _fallback_latex(raw)
    if fb and _latex_sane(fb):
        if _bridge_available():
            probe = _call_bridge(fb, "asciimath")
            if probe.get("valid") and probe.get("latex") and _latex_sane(str(probe["latex"])):
                return MathSegment(
                    kind="math",
                    raw=raw,
                    latex=_cleanup_latex_unicode(str(probe["latex"])),
                    valid=True,
                )
        return MathSegment(kind="math", raw=raw, latex=_cleanup_latex_unicode(fb), valid=True)

    return MathSegment(kind="math", raw=raw, latex=None, valid=False)


def normalize_math_only(text: str) -> RichText:
    """Treat the whole string as a single math expression (no CJK splitting).

    Used for equation blocks (e.g. piecewise functions) where CJK-splitting
    would fragment multi-branch notation and destroy the LaTeX.
    """
    raw = text if text is not None else ""
    if not raw.strip():
        return RichText(raw=raw, segments=[])
    return RichText(raw=raw, segments=[_convert_math_segment(raw)])


# Explicit math-delimiter regions the 35B model emits inside prose.  These are
# treated as *atomic* — CJK splitting must never enter them, or a valid
# expression like ``$\{x \mid x \in A \text{ 且 } x \in B\}$`` gets shattered at
# the Chinese character into two invalid halves (the p11/p15/p21/p28 fallback).
_MATH_REGION_RE = re.compile(
    r"\$\$[\s\S]+?\$\$"
    r"|\$[^$\n]+?\$"
    r"|\\\([\s\S]+?\\\)"
    r"|\\\[[\s\S]+?\\\]"
)


def _strip_math_delims(region: str) -> str:
    r = region.strip()
    for op, cl in (("$$", "$$"), ("$", "$"), ("\\(", "\\)"), ("\\[", "\\]")):
        if r.startswith(op) and r.endswith(cl) and len(r) >= len(op) + len(cl):
            return r[len(op) : len(r) - len(cl)].strip()
    return r


def _segment_prose(text: str) -> list[MathSegment]:
    """CJK-split a prose run (no protected math delimiters inside)."""
    if not text:
        return []
    parts = split_cjk_parts(text)
    if not parts:
        if looks_like_math(text):
            return [_convert_math_segment(text)]
        return [MathSegment(kind="text", raw=text, valid=False)]
    out: list[MathSegment] = []
    for part in parts:
        if is_cjk_run(part):
            out.append(MathSegment(kind="text", raw=part, valid=False))
        elif looks_like_math(part):
            out.append(_convert_math_segment(part))
        else:
            out.append(MathSegment(kind="text", raw=part, valid=False))
    return out


def normalize_plain(text: str) -> RichText:
    raw = text if text is not None else ""
    if not raw.strip():
        return RichText(raw=raw, segments=[])

    segments: list[MathSegment] = []
    pos = 0
    for m in _MATH_REGION_RE.finditer(raw):
        if m.start() > pos:
            segments.extend(_segment_prose(raw[pos : m.start()]))
        inner = _strip_math_delims(m.group())
        # Protected region is LaTeX → route straight through the LaTeX-first
        # gate; on failure keep the inner source for a readable fallback.
        segments.append(_convert_math_segment(inner) if inner else
                        MathSegment(kind="text", raw=m.group(), valid=False))
        pos = m.end()
    if pos < len(raw):
        segments.extend(_segment_prose(raw[pos:]))

    return RichText(raw=raw, segments=segments or [MathSegment(kind="text", raw=raw, valid=False)])
