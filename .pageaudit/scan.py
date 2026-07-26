import json, re
from pathlib import Path
D = Path(__file__).resolve().parent
BP = json.load(open(D/"pages.json"))
BARE = re.compile(r"[∈∉∩∪√⊆⊇⊂⊃≤≥≠∞∅±×÷·→←↔⇒⇔∀∃∑∏∫αβγθπλμσφω̅²³]")

def cap(b):
    return ((b.get("content") or {}).get("figure") or {}).get("caption") or ""

print(f"{'pg':>3} {'blocks':>6} {'fig':>4} {'txt':>4} | flags")
for p in BP:
    pno=p["page_no"]; blocks=p["blocks"]
    figs=[b for b in blocks if b["kind"]=="figure"]
    txts=[b for b in blocks if b["kind"] in("text","running")]
    flags=[]
    # text stuffed into figure caption
    stuffed=[b for b in figs if len(cap(b))>40]
    if stuffed: flags.append(f"TEXT_IN_FIG_CAPTION x{len(stuffed)}(maxlen={max(len(cap(b)) for b in stuffed)})")
    # english hallucinated caption on chinese book
    eng=[b for b in figs if cap(b) and re.fullmatch(r"[\x00-\x7f ]+", cap(b))]
    if eng: flags.append(f"ENGLISH_CAPTION x{len(eng)}")
    # all-figure page with real text content
    if figs and not txts and any(len(cap(b))>20 for b in figs):
        flags.append("PAGE_ALL_FIGURE_BUT_HAS_TEXT")
    # bare unicode math in any text paragraph OR caption (should be $...$)
    bu=0
    for b in blocks:
        if b["kind"] in("text","running"):
            for para in ((b.get("content") or {}).get("text") or {}).get("paragraphs") or []:
                if BARE.search(re.sub(r"\$[^$]+\$","",para)): bu+=1
        if b["kind"]=="figure":
            if BARE.search(re.sub(r"\$[^$]+\$","",cap(b))): bu+=1
    if bu: flags.append(f"BARE_UNICODE x{bu}")
    # blank
    if len(blocks)==1 and blocks[0]["id"].endswith("blank"): flags.append("BLANK_PAGE")
    print(f"{pno:>3} {len(blocks):>6} {len(figs):>4} {len(txts):>4} | {'; '.join(flags) if flags else 'ok'}")
