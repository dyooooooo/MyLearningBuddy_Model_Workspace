#!/usr/bin/env python3
import json, re, sys
from collections import Counter

from app.clients.minio_client import get_object_bytes

JOB = "7b33d74bc3d042a0b9836116f8cc3bdf"
DEBUG = f"import35b/{JOB}/assets/35b/debug"
BP_KEY = f"import35b/{JOB}/assets/35b/blueprints/pages.json"

manifest = json.loads(get_object_bytes(f"{DEBUG}/manifest.json"))
all_bp = json.loads(get_object_bytes(BP_KEY))

TAG = re.compile(r"<[^>]+>")
INLINE = re.compile(r'data-latex="([^"]*)"')
FIG = re.compile(r"lb-fig-item")
FALLBACK = re.compile(r"math--fallback")
BAD_LATEX = re.compile(r"le\s*q\s*s\s*l\s*a\s*n\s*t", re.I)


def text_plain(block):
    c = block.get("content") or {}
    k = block.get("kind")
    if k in ("text", "running"):
        t = c.get("text")
        if isinstance(t, dict):
            return "\n".join(t.get("paragraphs") or [])
        return str(t or "")
    if k == "table":
        rows = (c.get("table") or {}).get("rows") or []
        return "\n".join(" | ".join(r) for r in rows)
    if k == "figure":
        return (c.get("figure") or {}).get("caption") or ""
    if k == "equation":
        e = c.get("equation") or {}
        return e.get("latex") or e.get("fallback_text") or ""
    return ""


def stats(blocks):
    kinds = Counter(b.get("kind") for b in blocks)
    empty_text = sum(
        1 for b in blocks if b.get("kind") == "text" and not text_plain(b).strip()
    )
    long_fig = sum(
        1 for b in blocks if b.get("kind") == "figure" and len(text_plain(b)) > 40
    )
    chars = sum(len(text_plain(b)) for b in blocks)
    return {
        "kinds": dict(kinds),
        "blocks": len(blocks),
        "text_empty": empty_text,
        "long_fig_caption": long_fig,
        "chars": chars,
    }


def raw_stats(raw):
    if not raw:
        return None
    blocks = raw.get("blocks") or []
    kinds = Counter(b.get("kind") for b in blocks if isinstance(b, dict))
    empty = 0
    for b in blocks:
        if not isinstance(b, dict) or b.get("kind") != "text":
            continue
        t = (b.get("content") or {}).get("text")
        par = t.get("paragraphs") if isinstance(t, dict) else None
        if not (par and "".join(str(p) for p in par).strip()):
            empty += 1
    return {"blocks": len(blocks), "kinds": dict(kinds), "text_empty": empty}


def html_stats(html):
    if not html:
        return {
            "plain_len": 0,
            "figs": 0,
            "paras": 0,
            "inline_math": 0,
            "bad_latex": 0,
            "fallback": 0,
        }
    plain = TAG.sub(" ", html)
    plain = re.sub(r"\s+", " ", plain).strip()
    ltx = INLINE.findall(html)
    return {
        "plain_len": len(plain),
        "figs": len(FIG.findall(html)),
        "paras": html.count("<p>"),
        "inline_math": len(ltx),
        "bad_latex": sum(1 for x in ltx if BAD_LATEX.search(x)),
        "fallback": len(FALLBACK.findall(html)),
    }


rows = []
for m in sorted(manifest, key=lambda x: x["page_no"]):
    p = m["page_no"]
    pdir = f"{DEBUG}/p{p:04d}"
    try:
        raw = json.loads(get_object_bytes(f"{pdir}/raw_model.json"))
    except Exception:
        raw = None
    try:
        html = get_object_bytes(f"{pdir}/page.html").decode("utf-8", errors="replace")
    except Exception:
        html = ""
    bp = next((x for x in all_bp if x["page_no"] == p), None)
    bp_s = stats(bp["blocks"]) if bp else {}
    raw_s = raw_stats(raw)
    html_s = html_stats(html)
    flags = []
    if m.get("status") == "blank_detected":
        flags.append("BLANK")
    if m.get("truncated"):
        flags.append("TRUNCATED")
    if bp_s.get("text_empty"):
        flags.append(f"TEXT_EMPTY x{bp_s['text_empty']}")
    if bp_s.get("long_fig_caption"):
        flags.append(f"LONG_FIG_CAP x{bp_s['long_fig_caption']}")
    if (
        html_s["figs"]
        and bp_s.get("kinds", {}).get("text", 0) == 0
        and bp_s.get("kinds", {}).get("figure", 0)
    ):
        flags.append("ALL_FIGURE_PAGE")
    if html_s["bad_latex"]:
        flags.append(f"LATEX_BAD x{html_s['bad_latex']}")
    if html_s["fallback"]:
        flags.append(f"MATH_FALLBACK x{html_s['fallback']}")
    if raw_s and bp_s and raw_s.get("kinds") != bp_s.get("kinds"):
        flags.append("RAW!=BP kinds")
    rows.append(
        {
            "page": p,
            "status": m.get("status"),
            "raw": raw_s,
            "blueprint": bp_s,
            "html": html_s,
            "flags": flags or ["ok"],
        }
    )

out_path = "/tmp/import35b_page_compare.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({"job": JOB, "pages": rows}, f, ensure_ascii=False, indent=2)

print(json.dumps({"written": out_path, "pages": len(rows)}, ensure_ascii=False))
print(f"\n{'pg':>3} {'st':>6} {'raw':>7} {'bp':>6} {'txt∅':>4} {'fig':>3} {'html_p':>6} flags")
for r in rows:
    raw = r["raw"] or {}
    bp = r["blueprint"]
    print(
        f"{r['page']:>3} {(r['status'] or '')[:6]:>6} "
        f"{str(raw.get('blocks','-')):>7} {str(bp.get('blocks','-')):>6} "
        f"{bp.get('text_empty',0):>4} {bp.get('kinds',{}).get('figure',0):>3} "
        f"{r['html']['paras']:>6} {'; '.join(r['flags'])}"
    )
