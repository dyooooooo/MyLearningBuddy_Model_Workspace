#!/usr/bin/env python3
import json, re
from app.clients.minio_client import get_object_bytes

JOB = "7b33d74bc3d042a0b9836116f8cc3bdf"
DEBUG = f"import35b/{JOB}/assets/35b/debug"

BARE = re.compile(r"[∈∉∩∪√⊆⊇⊂⊃≤≥≠∞∅±×÷·→←↔⇒⇔∀∃∑∏∫αβγθπλμσφω]")
BAD_LEQ = re.compile(r"(?<![\\a-zA-Z])le\s+q\s+s\s+l\s+a\s+n\s+t", re.I)
GARBAGE = re.compile(
    r"\\frac\{i\}\{2\}|\\mathrm\{\s*H\s*\}|\\pi\s*\\pi|\\tilde\{\\cal|"
    r"\\pounds|\\Chi\b|\\langle\s*\(|\\operatorname\*\s*\{\s*lig|"
    r"\\sharp\s+\\left\\{|cases x=4"
)
INLINE = re.compile(r'data-latex="([^"]*)"')
FALLBACK = re.compile(r"math--fallback")
EN_CAP = re.compile(r"Chapter \d|Figure|Photo", re.I)


def paras(b):
    c = b.get("content") or {}
    if b["kind"] not in ("text", "running"):
        return []
    t = c.get("text")
    return (t.get("paragraphs") if isinstance(t, dict) else None) or []


manifest = json.loads(get_object_bytes(f"{DEBUG}/manifest.json"))
lines = []
lines.append(f"Job: {JOB}")
lines.append("")

for m in sorted(manifest, key=lambda x: x["page_no"]):
    p = m["page_no"]
    pdir = f"{DEBUG}/p{p:04d}"
    raw = None
    try:
        raw = json.loads(get_object_bytes(f"{pdir}/raw_model.json"))
    except Exception:
        pass
    bp = json.loads(get_object_bytes(f"{pdir}/blueprint.json"))
    html = get_object_bytes(f"{pdir}/page.html").decode()
    issues = []

    if m.get("status") == "blank_detected":
        issues.append("空白页检测")
    if m.get("truncated"):
        issues.append("JSON截断(max_tokens)")
    for e in m.get("attempt_errors") or []:
        issues.append(f"重试: {str(e.get('error', ''))[:100]}")

    kinds = {}
    for b in bp["blocks"]:
        kinds[b["kind"]] = kinds.get(b["kind"], 0) + 1

    for b in bp["blocks"]:
        bid = b["id"]
        k = b["kind"]
        c = b.get("content") or {}
        if k == "text" and not paras(b):
            issues.append(f"{bid}: 空text块")
        if k == "figure":
            cap = (c.get("figure") or {}).get("caption") or ""
            if len(cap) > 60 and not re.match(r"^\s*(图|表|Figure)", cap):
                issues.append(f"{bid}: 长figure caption({len(cap)}字)")
            if EN_CAP.search(cap):
                issues.append(f'{bid}: 英文caption「{cap[:50]}」')
        if k == "equation":
            lx = (c.get("equation") or {}).get("latex") or ""
            if GARBAGE.search(lx):
                issues.append(f"{bid}: 公式乱码「{lx[:80]}」")
            if not lx.strip():
                issues.append(f"{bid}: 空equation")
        for i, para in enumerate(paras(b)):
            plain = re.sub(r"\$[^$]+\$", "", para)
            if BAD_LEQ.search(para):
                issues.append(f"{bid}¶{i}: blueprint仍含 le q s l a n t")
            if BARE.search(plain):
                issues.append(f"{bid}¶{i}: 裸Unicode数学(未包$)")

    if "<section class=\"lb-main\"" in html:
        main = re.search(r'<section class="lb-main"[^>]*>(.*?)</section>', html, re.S).group(1).strip()
        text_chars = sum(len("".join(paras(b))) for b in bp["blocks"] if b["kind"] in ("text", "table"))
        if not main and text_chars > 100:
            issues.append("HTML: main区空")
    if "lb-table-wrap" not in html and any(b["kind"] == "table" for b in bp["blocks"]):
        issues.append("HTML: 有table块但未渲染表格")
    hd = re.search(r'<header class="lb-running-hd">(.*?)</header>', html, re.S)
    if hd and len(re.sub(r"\s+", " ", hd.group(1)).strip()) > 120:
        issues.append("HTML: running header塞入大段正文")
    for i, lx in enumerate(INLINE.findall(html)):
        if GARBAGE.search(lx):
            issues.append(f"HTML inline#{i}乱码: {lx[:70]}")
    for i, lx in enumerate(re.findall(r'math--display" data-latex="([^"]*)"', html)):
        if GARBAGE.search(lx):
            issues.append(f"HTML display#{i}乱码: {lx[:70]}")
    if FALLBACK.search(html):
        issues.append("HTML: MathJax fallback(公式渲染失败)")

    if raw:
        rk = {}
        for b in raw.get("blocks") or []:
            if isinstance(b, dict):
                rk[b.get("kind")] = rk.get(b.get("kind"), 0) + 1
        if rk != kinds:
            issues.append(f"raw≠bp kinds: {rk} -> {kinds}")

    rawf = m.get("raw") or {}
    if rawf.get("text_empty"):
        issues.append(f"raw text_empty={rawf['text_empty']}")

    sev = "ok"
    if any(
        x.startswith(("HTML: main", "空text", "空白", "JSON截断", "HTML: 有table", "HTML: MathJax"))
        for x in issues
    ):
        sev = "error"
    elif issues:
        sev = "warn"

    lines.append(f"### p{p:02d} [{sev}] blueprint={kinds}")
    if issues:
        for iss in issues:
            lines.append(f"  - {iss}")
    else:
        lines.append("  - 无检出错误")
    lines.append("")

text = "\n".join(lines)
open("/tmp/import35b_detailed_report.md", "w", encoding="utf-8").write(text)
print(text)
