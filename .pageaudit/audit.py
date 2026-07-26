import json, re, sys
from pathlib import Path

D = Path(__file__).resolve().parent
BP = json.load(open(D/"pages.json"))

BARE_UNI = re.compile(r"[∈∉∩∪√⊆⊇⊂⊃≤≥≠∞∅±×÷·→←↔∀∃∑∏∫αβγθπλμσφω]")
INLINE_RE = re.compile(r'math--inline" data-latex="([^"]*)"')
DISPLAY_RE = re.compile(r'math--display" data-latex="([^"]*)"')
FALLBACK_RE = re.compile(r'math--fallback[^>]*>([^<]*)<')
TAG_RE = re.compile(r"<[^>]+>")

def html_text(h):
    t = TAG_RE.sub(" ", h)
    return re.sub(r"\s+", " ", t).strip()

def block_content(b):
    c = b.get("content") or {}
    k = b["kind"]
    if k in ("text","running"):
        t = c.get("text") or {}
        return t.get("paragraphs") or []
    if k == "table":
        t = c.get("table") or {}
        return {"rows": t.get("rows"), "caption": t.get("caption")}
    if k == "equation":
        e = c.get("equation") or {}
        return {"latex": e.get("latex"), "fallback": e.get("fallback_text")}
    if k == "figure":
        f = c.get("figure") or {}
        return {"caption": f.get("caption")}
    return None

def audit_page(pno):
    p = next(x for x in BP if x["page_no"]==pno)
    html = (D/f"html/p{pno:04d}.html").read_text(encoding="utf-8") if (D/f"html/p{pno:04d}.html").is_file() else ""
    out = []
    out.append(f"===== PAGE {pno} | blocks={len(p['blocks'])} =====")
    for b in p["blocks"]:
        cc = block_content(b)
        out.append(f"[{b['kind']}/{b.get('role')}] id={b['id']} bbox={[round(x) for x in b['bbox']]}")
        if isinstance(cc, list):
            for para in cc:
                flag = " <<BARE_UNICODE" if BARE_UNI.search(re.sub(r'\$[^$]+\$','',para)) else ""
                out.append(f"    ¶ {para}{flag}")
        elif isinstance(cc, dict):
            out.append(f"    {json.dumps(cc, ensure_ascii=False)}")
    # HTML side
    inl = INLINE_RE.findall(html); dsp = DISPLAY_RE.findall(html); fb = FALLBACK_RE.findall(html)
    out.append(f"--- HTML: inline_math={len(inl)} display_math={len(dsp)} fallback={len(fb)} ---")
    if fb:
        out.append("  FALLBACK spans: " + " || ".join(fb[:12]))
    # ascii 'in' debris in latex
    blob = " ".join(inl+dsp)
    scrub = blob
    for cmd in ("\\in","\\infty","\\int","\\sin","\\cos","\\lim","\\ln","\\log","\\min","\\max"):
        scrub = scrub.replace(cmd,"")
    if re.search(r"(?<![\\a-zA-Z])in(?![a-zA-Z])", scrub) or re.search(r"(?<![\\a-zA-Z])(nn|uu|!in)(?![a-zA-Z])", scrub):
        out.append("  <<ASCIIMATH_DEBRIS in data-latex")
    # residual bare unicode inside data-latex
    if BARE_UNI.search(blob):
        out.append("  <<BARE_UNICODE_IN_LATEX: " + " ".join(x for x in inl+dsp if BARE_UNI.search(x))[:300])
    out.append("  HTML_TEXT: " + html_text(html)[:600])
    return "\n".join(out)

if __name__=="__main__":
    pages = [int(x) for x in sys.argv[1:]] or list(range(1,31))
    print("\n\n".join(audit_page(p) for p in pages))
