import json
from app.clients.minio_client import get_object_bytes

PREFIX = "import35b/7b33d74bc3d042a0b9836116f8cc3bdf/assets/35b/debug"
PAGES = list(range(1,31))

try:
    from app.import_35b.vision_layout import blueprint_from_dict
    from app.import_35b.render import blueprint_to_html, html_to_plain_text
    HAS_RENDER = True
except Exception as e:
    HAS_RENDER = False
    IMPORT_ERR = str(e)

def load(k):
    b=get_object_bytes(k)
    return json.loads(b) if b else None

results = []
for pn in PAGES:
    base = f"{PREFIX}/p{pn:04d}"
    raw = load(f"{base}/raw_model.json")
    bp = load(f"{base}/blueprint.json")
    dbg = load(f"{base}/debug.json")
    entry = {"page_no": pn}
    if raw is None and bp is None:
        entry["note"] = "no dump"
        results.append(entry)
        continue
    entry["raw_eq_blueprint"] = raw == bp
    if raw and bp:
        # shallow diff hints
        rb, bb = raw.get("blocks",[]), bp.get("blocks",[])
        entry["block_count_raw"] = len(rb)
        entry["block_count_bp"] = len(bb)
        if len(rb)==len(bb):
            kind_diff = sum(1 for a,b in zip(rb,bb) if (a.get("kind") or a.get("type")) != (b.get("kind") or b.get("type")))
            entry["kind_mismatch_blocks"] = kind_diff
    if HAS_RENDER and bp:
        try:
            model = blueprint_from_dict(bp)
            html = blueprint_to_html(model, page_no=pn)
            plain = html_to_plain_text(html)
            entry["bp_plain_len"] = len(plain.strip())
            entry["bp_plain_preview"] = plain.strip()[:160]
        except Exception as ex:
            entry["render_error"] = str(ex)[:200]
    # debug dump page.html if exists
    html_b = get_object_bytes(f"{base}/page.html")
    if html_b and HAS_RENDER:
        plain2 = html_to_plain_text(html_b.decode())
        entry["debug_html_plain_len"] = len(plain2.strip())
    results.append(entry)

print(json.dumps({"has_render": HAS_RENDER, "import_err": IMPORT_ERR if not HAS_RENDER else None, "pages": results}, ensure_ascii=False, indent=2))
