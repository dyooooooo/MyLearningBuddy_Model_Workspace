#!/usr/bin/env python3
"""Exhaustive page audit from MinIO debug dump."""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.clients.minio_client import get_object_bytes

JOB = "7b33d74bc3d042a0b9836116f8cc3bdf"
DEBUG = f"import35b/{JOB}/assets/35b/debug"
BP_KEY = f"import35b/{JOB}/assets/35b/blueprints/pages.json"

# ── patterns ──────────────────────────────────────────────────────────────
TAG = re.compile(r"<[^>]+>")
INLINE = re.compile(r'data-latex="([^"]*)"')
DISPLAY = re.compile(r'math--display" data-latex="([^"]*)"')
FALLBACK = re.compile(r'math--fallback[^>]*>([^<]*)<')
FIG_ITEM = re.compile(r"lb-fig-item")
TABLE_WRAP = re.compile(r"lb-table-wrap")
TOC = re.compile(r"lb-toc")
EQ_BLOCK = re.compile(r"lb-equation")
RUNNING_HD = re.compile(r"lb-running-hd")

BARE_MATH = re.compile(
    r"[∈∉∩∪√⊆⊇⊂⊃≤≥≠∞∅±×÷·→←↔⇒⇔∀∃∑∏∫αβγθπλμσφω̅²³]"
)
BAD_LEQ = re.compile(r"(?<![\\a-zA-Z])le\s+q\s+s\s+l\s+a\s+n\s+t(?![a-zA-Z])", re.I)
BAD_LEQ_SPACED = re.compile(r"\\le\s+q\s+s\s+l\s+a\s+n\s+t", re.I)
SPACED_PARENS = re.compile(r"\(\s+[^)]+\s+\)")
GARBAGE_LATEX = re.compile(
    r"\\frac\{i\}\{2\}|\\mathrm\s*\{\s*H\s*\}|\\pi\s*\\pi|"
    r"\\tilde\s*\{\\cal|textsc\s*\{\s*i\s*n\s*f|"
    r"\\operatorname\*\s*\{\s*lig\s*\}|\\pounds|\\Chi\b|"
    r"\\langle\s*\(|\\rangle\s*\)"
)
EN_CAPTION = re.compile(r"^\s*(Figure|Fig\.?|Table|Chapter|Photo)\b", re.I)
CAPTION_MARKER = re.compile(r"^\s*(图|表|Figure|Fig\.?|Table)\s*[\d０-９]", re.I)


@dataclass
class Issue:
    severity: str  # error | warn | info
    code: str
    detail: str


@dataclass
class PageAudit:
    page_no: int
    status: str = "ok"
    truncated: bool = False
    raw_blocks: int = 0
    bp_blocks: int = 0
    raw_kinds: dict = field(default_factory=dict)
    bp_kinds: dict = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    def add(self, severity: str, code: str, detail: str) -> None:
        self.issues.append(Issue(severity, code, detail))


def load_json(key: str) -> Any | None:
    try:
        return json.loads(get_object_bytes(key))
    except Exception:
        return None


def load_text(key: str) -> str:
    try:
        return get_object_bytes(key).decode("utf-8", errors="replace")
    except Exception:
        return ""


def text_payload(block: dict) -> str:
    c = block.get("content") or {}
    k = block.get("kind")
    if k in ("text", "running"):
        t = c.get("text")
        if isinstance(t, dict):
            return "\n".join(str(p) for p in (t.get("paragraphs") or []))
        return str(t or "")
    if k == "table":
        rows = (c.get("table") or {}).get("rows") or []
        return "\n".join(" | ".join(str(x) for x in r) for r in rows)
    if k == "figure":
        return str((c.get("figure") or {}).get("caption") or "")
    if k == "equation":
        e = c.get("equation") or {}
        return str(e.get("latex") or e.get("fallback_text") or "")
    return ""


def paragraphs(block: dict) -> list[str]:
    c = block.get("content") or {}
    if block.get("kind") not in ("text", "running"):
        return []
    t = c.get("text")
    if isinstance(t, dict):
        return [str(p) for p in (t.get("paragraphs") or [])]
    if t:
        return [str(t)]
    return []


def strip_math(s: str) -> str:
    return re.sub(r"\$[^$]+\$", "", s)


def audit_latex_in_text(a: PageAudit, block_id: str, text: str, where: str) -> None:
    for i, para in enumerate(text.split("\n") if "\n" in text else [text]):
        plain = strip_math(para)
        if BARE_MATH.search(plain):
            a.add("warn", "BARE_UNICODE_MATH", f"{where} {block_id} ¶{i}: bare math unicode outside $...$")
        if BAD_LEQ.search(para) or BAD_LEQ_SPACED.search(para):
            a.add("error", "BAD_LEQ_TOKEN", f"{where} {block_id} ¶{i}: spaced 'le q s l a n t' (broken ≤)")
        if SPACED_PARENS.search(para) and "$" in para:
            a.add("warn", "SPACED_INLINE_MATH", f"{where} {block_id} ¶{i}: spaced parens in math context")


def audit_latex_string(a: PageAudit, block_id: str, latex: str, where: str) -> None:
    if not latex.strip():
        a.add("error", "EMPTY_EQUATION", f"{where} {block_id}: empty equation latex")
        return
    if BAD_LEQ.search(latex) or BAD_LEQ_SPACED.search(latex):
        a.add("error", "BAD_LEQ_TOKEN", f"{where} {block_id}: spaced le q s l a n t in latex")
    if GARBAGE_LATEX.search(latex):
        a.add("error", "GARBAGE_LATEX", f"{where} {block_id}: OCR/MFR garbage: {latex[:100]}...")
    if len(latex) > 400:
        a.add("warn", "LONG_EQUATION", f"{where} {block_id}: latex len={len(latex)}")


def audit_blocks(a: PageAudit, blocks: list[dict], where: str) -> None:
    for b in blocks:
        bid = b.get("id", "?")
        kind = b.get("kind")
        txt = text_payload(b)
        if kind == "text" and not txt.strip():
            a.add("error", "EMPTY_TEXT_BLOCK", f"{where} {bid}: kind=text but no content")
        if kind == "figure":
            cap = (b.get("content") or {}).get("figure", {}) or {}
            caption = cap.get("caption") or ""
            if len(caption) > 120:
                a.add("error", "CAPTION_TOO_LONG", f"{where} {bid}: caption len={len(caption)} (>120 schema bound)")
            elif len(caption) > 60 and not CAPTION_MARKER.match(caption):
                a.add("warn", "LONG_FIGURE_CAPTION", f"{where} {bid}: body-like caption len={len(caption)}")
            if EN_CAPTION.match(caption) and not caption.startswith("图"):
                a.add("warn", "ENGLISH_CAPTION", f"{where} {bid}: {caption[:60]}")
            if not cap.get("caption") and not b.get("content", {}).get("asset_key"):
                a.add("warn", "FIGURE_NO_ASSET", f"{where} {bid}: figure without asset_key")
        if kind == "table":
            rows = ((b.get("content") or {}).get("table") or {}).get("rows") or []
            if not rows:
                a.add("error", "EMPTY_TABLE", f"{where} {bid}: table with no rows")
        if kind == "equation":
            e = (b.get("content") or {}).get("equation") or {}
            audit_latex_string(a, bid, e.get("latex") or "", where)
            fb = e.get("fallback_text") or ""
            if fb and not (e.get("latex") or "").strip():
                a.add("warn", "EQUATION_FALLBACK_ONLY", f"{where} {bid}: only fallback_text")
        for para in paragraphs(b):
            audit_latex_in_text(a, bid, para, where)


def html_plain(html: str) -> str:
    t = TAG.sub(" ", html)
    return re.sub(r"\s+", " ", t).strip()


def audit_html(a: PageAudit, html: str, bp: dict) -> None:
    if not html.strip():
        a.add("error", "EMPTY_HTML", "page.html is empty")
        return
    plain = html_plain(html)
    bp_chars = sum(len(text_payload(b)) for b in bp.get("blocks") or [])
    if bp_chars > 80 and len(plain) < bp_chars * 0.35:
        a.add(
            "error",
            "HTML_TEXT_LOSS",
            f"html plain len={len(plain)} vs blueprint chars={bp_chars} (>{65}% loss)",
        )
    main = re.search(r'<section class="lb-main"[^>]*>(.*?)</section>', html, re.S)
    main_body = main.group(1) if main else ""
    if bp_chars > 200 and not main_body.strip():
        a.add("error", "EMPTY_MAIN_ZONE", "lb-main section is empty but blueprint has content")
    # running header bloated
    m = re.search(r'<header class="lb-running-hd">(.*?)</header>', html, re.S)
    if m:
        hd = TAG.sub(" ", m.group(1))
        hd = re.sub(r"\s+", " ", hd).strip()
        if len(hd) > 120:
            a.add("error", "RUNNING_HEADER_BLOATED", f"running header len={len(hd)}: {hd[:80]}...")
    # blueprint table but no html table
    bp_tables = sum(1 for b in bp.get("blocks") or [] if b.get("kind") == "table")
    if bp_tables and not TABLE_WRAP.search(html):
        a.add("error", "TABLE_NOT_RENDERED", f"blueprint has {bp_tables} table(s) but HTML has no lb-table-wrap")
    # blueprint text paras vs html paras
    exp_paras = sum(len(paragraphs(b)) for b in bp.get("blocks") or [] if b.get("kind") in ("text", "running"))
    html_paras = html.count("<p>")
    bp_figs_only = sum(1 for b in bp.get("blocks") or [] if b.get("kind") == "figure") and exp_paras <= 2
    if exp_paras >= 3 and html_paras == 0 and bp_tables == 0 and not bp_figs_only:
        a.add("error", "NO_PARAGRAPHS_RENDERED", f"expected ~{exp_paras} paragraphs, html has 0 <p>")
    elif exp_paras >= 5 and html_paras < exp_paras * 0.4:
        a.add("warn", "PARAGRAPH_COUNT_LOW", f"expected {exp_paras} paras, html has {html_paras} <p>")
    # latex in html
    for i, lx in enumerate(INLINE.findall(html)):
        if BAD_LEQ.search(lx) or BAD_LEQ_SPACED.search(lx):
            a.add("error", "HTML_BAD_LEQ", f"inline math #{i}: {lx[:80]}")
        if GARBAGE_LATEX.search(lx):
            a.add("error", "HTML_GARBAGE_INLINE", f"inline math #{i}: {lx[:80]}")
    for i, lx in enumerate(DISPLAY.findall(html)):
        if BAD_LEQ.search(lx) or BAD_LEQ_SPACED.search(lx):
            a.add("error", "HTML_BAD_LEQ", f"display math #{i}: {lx[:80]}")
        if GARBAGE_LATEX.search(lx):
            a.add("error", "HTML_GARBAGE_DISPLAY", f"display math #{i}: {lx[:80]}")
    for fb in FALLBACK.findall(html):
        a.add("error", "HTML_MATH_FALLBACK", f"fallback span: {fb[:80]}")
    # all-figure page
    bp_figs = sum(1 for b in bp.get("blocks") or [] if b.get("kind") == "figure")
    bp_text = sum(1 for b in bp.get("blocks") or [] if b.get("kind") == "text")
    if bp_figs >= 3 and bp_text == 0:
        a.add("error", "ALL_FIGURE_PAGE", f"{bp_figs} figures, 0 text blocks")
    # kind coverage in html
    if bp_figs and len(FIG_ITEM.findall(html)) < bp_figs:
        a.add("warn", "FIGURE_RENDER_MISS", f"blueprint {bp_figs} figs, html {len(FIG_ITEM.findall(html))}")


def audit_page(meta: dict) -> PageAudit:
    p = meta["page_no"]
    pdir = f"{DEBUG}/p{p:04d}"
    a = PageAudit(page_no=p, status=meta.get("status") or "ok")
    a.truncated = bool(meta.get("truncated"))

    raw = load_json(f"{pdir}/raw_model.json")
    bp = load_json(f"{pdir}/blueprint.json")
    html = load_text(f"{pdir}/page.html")

    if a.status == "blank_detected":
        a.add("info", "BLANK_PAGE", "PDF blank detection triggered")
    if a.truncated:
        a.add("error", "JSON_TRUNCATED", f"finish_reason=length max_tokens={meta.get('call', {}).get('max_tokens')}")
    if meta.get("attempt_errors"):
        for e in meta["attempt_errors"]:
            a.add("warn", "BLUEPRINT_RETRY", f"attempt {e.get('attempt')}: {str(e.get('error',''))[:120]}")

    if raw:
        blocks = raw.get("blocks") or []
        a.raw_blocks = len(blocks)
        a.raw_kinds = dict(Counter(b.get("kind") for b in blocks if isinstance(b, dict)))
        audit_blocks(a, blocks, "raw")
    else:
        a.add("warn", "NO_RAW_MODEL", "raw_model.json missing")

    if bp:
        blocks = bp.get("blocks") or []
        a.bp_blocks = len(blocks)
        a.bp_kinds = dict(Counter(b.get("kind") for b in blocks))
        audit_blocks(a, blocks, "bp")
        audit_html(a, html, bp)
        if raw and a.raw_kinds != a.bp_kinds:
            a.add("info", "RAW_BP_KIND_DIFF", f"raw={a.raw_kinds} bp={a.bp_kinds}")
    else:
        a.add("error", "NO_BLUEPRINT", "blueprint.json missing")

    # dedupe issues same code+detail
    seen = set()
    uniq = []
    for iss in a.issues:
        k = (iss.code, iss.detail)
        if k not in seen:
            seen.add(k)
            uniq.append(iss)
    a.issues = uniq
    return a


def main() -> None:
    manifest = load_json(f"{DEBUG}/manifest.json") or []
    audits = [audit_page(m) for m in sorted(manifest, key=lambda x: x["page_no"])]

    summary: dict[str, int] = Counter()
    for au in audits:
        for iss in au.issues:
            if iss.severity in ("error", "warn"):
                summary[iss.code] += 1

    report = {
        "job": JOB,
        "page_count": 30,
        "issue_counts_by_code": dict(sorted(summary.items(), key=lambda x: -x[1])),
        "page_audits": [
            {
                "page_no": au.page_no,
                "status": au.status,
                "truncated": au.truncated,
                "raw": {"blocks": au.raw_blocks, "kinds": au.raw_kinds},
                "blueprint": {"blocks": au.bp_blocks, "kinds": au.bp_kinds},
                "issues": [
                    {"severity": i.severity, "code": i.code, "detail": i.detail}
                    for i in au.issues
                ],
            }
            for au in audits
        ],
    }
    out = "/tmp/import35b_full_audit.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({"written": out, "issue_codes": report["issue_counts_by_code"]}, ensure_ascii=False))

    err_pages = [au for au in audits if any(i.severity == "error" for i in au.issues)]
    warn_pages = [au for au in audits if any(i.severity == "warn" for i in au.issues) and au not in err_pages]
    print(f"\nPages with ERRORS: {len(err_pages)} -> {[a.page_no for a in err_pages]}")
    print(f"Pages with WARN only: {len(warn_pages)} -> {[a.page_no for a in warn_pages]}")

    for au in audits:
        errs = [i for i in au.issues if i.severity == "error"]
        warns = [i for i in au.issues if i.severity == "warn"]
        if not errs and not warns:
            continue
        print(f"\n{'='*60}\nPAGE {au.page_no}  status={au.status}  raw={au.raw_kinds}  bp={au.bp_kinds}")
        for i in errs:
            print(f"  [ERROR] {i.code}: {i.detail}")
        for i in warns:
            print(f"  [WARN]  {i.code}: {i.detail}")


if __name__ == "__main__":
    main()
