import json
from collections import Counter
from app.clients.minio_client import get_object_bytes

PREFIX = "import35b/7b33d74bc3d042a0b9836116f8cc3bdf/assets/35b/debug"
JOB_ID = "de6bd85c-8eef-4126-8953-cfadd80abb55"
BASE = "import35b/7b33d74bc3d042a0b9836116f8cc3bdf/assets/35b"

manifest = json.loads(get_object_bytes(f"{PREFIX}/manifest.json"))
BAD_PAGES_USER = {3,4,5,10,11,12,13,14,16,18,19,21,24}
FIGURE_HIGH = 3

def page_line(p):
    raw, fin = p.get("raw") or {}, p.get("final") or {}
    call = p.get("call") or {}
    return {
        "page_no": p.get("page_no"),
        "status": p.get("status"),
        "raw_blocks": raw.get("blocks"),
        "raw_kinds": raw.get("kinds"),
        "raw_text_empty": raw.get("text_empty"),
        "final_blocks": fin.get("blocks"),
        "final_kinds": fin.get("kinds"),
        "final_text_empty": fin.get("text_empty"),
        "truncated": p.get("truncated"),
        "finish_reason": call.get("finish_reason"),
        "attempt_errors": p.get("attempt_errors") or [],
        "raw_eq_final": raw == fin,
    }

lines = [page_line(p) for p in manifest]

def is_issue(line):
    pn = line["page_no"]
    if pn in BAD_PAGES_USER:
        return True
    if (line.get("raw_text_empty") or 0) > 0 or (line.get("final_text_empty") or 0) > 0:
        return True
    if line.get("truncated"):
        return True
    if line.get("status") != "ok":
        return True
    fk = line.get("final_kinds") or {}
    if (fk.get("figure") or 0) >= FIGURE_HIGH:
        return True
    if not line.get("raw_eq_final"):
        return True
    return False

issue_pages = sorted({l["page_no"] for l in lines if is_issue(l)})

html_to_plain_text = None
for mod in ("app.import_35b.html_render", "app.import_35b.render", "app.import_35b.html"):
    try:
        m = __import__(mod, fromlist=["html_to_plain_text"])
        html_to_plain_text = getattr(m, "html_to_plain_text", None)
        if html_to_plain_text:
            break
    except Exception:
        pass

def flatten_text(val):
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, list):
        parts = []
        for x in val:
            if isinstance(x, str):
                parts.append(x)
            elif isinstance(x, dict):
                parts.append(str(x.get("text") or x.get("content") or x.get("value") or ""))
            else:
                parts.append(str(x))
        return " ".join(p for p in parts if p).strip()
    if isinstance(val, dict):
        return flatten_text(val.get("text") or val.get("content") or val.get("value"))
    return str(val).strip()

def first_text_preview(blocks, n=200):
    for b in blocks or []:
        kind = b.get("kind") or b.get("type")
        if kind in ("text", "running", "caption", "heading"):
            t = flatten_text(b.get("text") if b.get("text") is not None else b.get("content"))
            if t:
                return t[:n]
    return ""

def summarize_blueprint(data):
    if not isinstance(data, dict):
        return {"error": "not dict"}
    blocks = data.get("blocks")
    if blocks is None:
        blocks = (data.get("layout") or {}).get("blocks")
    if not blocks:
        return {"error": "no blocks", "top_keys": list(data.keys())[:12]}
    kinds = Counter((b.get("kind") or b.get("type") or "?") for b in blocks)
    captions = []
    for b in blocks:
        if (b.get("kind") or b.get("type")) == "figure":
            cap = b.get("caption") or b.get("alt") or ""
            if cap:
                captions.append(str(cap)[:120])
    return {
        "block_count": len(blocks),
        "kinds": dict(kinds),
        "first_text": first_text_preview(blocks),
        "figure_captions": captions[:8],
    }

def load_json(key):
    b = get_object_bytes(key)
    return json.loads(b) if b else None

def plain_from_html(html_b):
    if html_to_plain_text:
        return html_to_plain_text(html_b.decode("utf-8", errors="replace"))
    import re
    plain = re.sub(r"<[^>]+>", " ", html_b.decode("utf-8", errors="replace"))
    return re.sub(r"\s+", " ", plain).strip()

detailed = {}
for pn in issue_pages:
    pdir = f"{PREFIX}/p{pn:04d}"
    entry = {}
    for name, fn in [("raw_model", "raw_model.json"), ("blueprint", "blueprint.json")]:
        path = f"{pdir}/{fn}"
        data = load_json(path)
        entry[name] = summarize_blueprint(data) if data else {"missing": path}
        entry[f"{name}_path"] = path
    html_key = f"{BASE}/pages/p{pn:04d}.html"
    html_b = get_object_bytes(html_key)
    if html_b:
        plain = plain_from_html(html_b)
        entry["html"] = {"key": html_key, "plain_len": len(plain), "preview": plain[:200]}
    else:
        entry["html"] = {"missing": html_key}
    detailed[str(pn)] = entry

diff_pages = [l for l in lines if not l["raw_eq_final"]]

report = {
    "job": {
        "id": JOB_ID,
        "status": "done",
        "page_count": 30,
        "label": "高中数学第一册_35b",
        "created_at": "2026-07-03T15:29:59.550891+00:00",
        "debug_prefix": PREFIX,
        "prompt_version": "layout_blueprint_v5",
        "blank_pages": [29],
    },
    "manifest_table": lines,
    "summary_stats": {
        "pages_total": len(lines),
        "raw_ne_final_count": len(diff_pages),
        "raw_ne_final_page_nos": [l["page_no"] for l in diff_pages],
        "text_empty_final": [l["page_no"] for l in lines if (l.get("final_text_empty") or 0) > 0],
        "truncated": [l["page_no"] for l in lines if l.get("truncated")],
        "non_ok": [{"page_no": l["page_no"], "status": l["status"]} for l in lines if l["status"] != "ok"],
        "high_figure_final_gte3": [l["page_no"] for l in lines if (l.get("final_kinds") or {}).get("figure", 0) >= 3],
        "issue_pages_detailed": issue_pages,
    },
    "detailed_problem_pages": detailed,
    "html_to_plain_text_module": html_to_plain_text.__module__ if html_to_plain_text else None,
}

with open("/tmp/import35b_debug_report.json", "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print(json.dumps(report["summary_stats"], ensure_ascii=False, indent=2))
