#!/usr/bin/env python3
"""Fix IP page_no=0 using page_no from user「聚焦目标」in causal_chain / user_input.

Also rewrites locus_key so one dialogue maps to one locus (dlg:…).

  python3 tools/backfill-ip-page-from-dialogue.py --dry-run
  python3 tools/backfill-ip-page-from-dialogue.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys

_PAGE_RE = re.compile(r"page_no\s*=\s*(\d+)", re.I)


def _page_from(obj: dict) -> int:
    cc = obj.get("causal_chain") if isinstance(obj.get("causal_chain"), dict) else {}
    for blob in (
        cc.get("page_no"),
        obj.get("page_no"),
        obj.get("user_input"),
        *((cc.get("user_requests") or []) if isinstance(cc.get("user_requests"), list) else []),
    ):
        if isinstance(blob, int) and blob > 0:
            return blob
        m = _PAGE_RE.search(str(blob or ""))
        if m:
            return int(m.group(1))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    list_script = r"""
import json
from app.db.session import get_session_factory
from app.db.models import CorpusImprovementPoint
from app.util.json_util import loads
s = get_session_factory()()
try:
    out = []
    for r in s.query(CorpusImprovementPoint).all():
        claim = loads(r.claim_json, {}) or {}
        concrete = loads(r.concrete_json, {}) or {}
        out.append({
            "id": r.id,
            "page_no": int(r.page_no or 0),
            "locus_key": r.locus_key or "",
            "category": r.category or "other",
            "claim": claim,
            "concrete": concrete,
        })
    print(json.dumps(out, ensure_ascii=False))
finally:
    s.close()
"""
    proc = subprocess.run(
        ["docker", "exec", "buddy-api", "python", "-c", list_script],
        capture_output=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode())
        raise SystemExit(proc.returncode)
    items = json.loads(proc.stdout.decode())

    patches = []
    for it in items:
        claim = it.get("claim") or {}
        concrete = it.get("concrete") or {}
        old_page = int(it.get("page_no") or 0)
        # ONLY fix wrongly-stored p0. Never overwrite a real page IP.
        if old_page > 0:
            continue
        page = _page_from(claim) or _page_from(concrete)
        if page <= 0:
            continue
        ui = str(claim.get("user_input") or concrete.get("user_input") or "")[:240]
        slot = "dlg:" + hashlib.sha1(f"{page}\n{ui}\n{it['id'][:8]}".encode()).hexdigest()[:10]
        for src in (claim, concrete):
            cc = src.get("causal_chain") if isinstance(src.get("causal_chain"), dict) else {}
            if cc.get("dialogue_slot"):
                slot = str(cc["dialogue_slot"])
                break
        cat = str(it.get("category") or "other")
        locus = f"page:{page}:cat:{cat}:slot:{slot[:120]}"
        cc = dict(claim.get("causal_chain") or {}) if isinstance(claim.get("causal_chain"), dict) else {}
        cc["page_no"] = page
        cc.setdefault("dialogue_slot", slot)
        patches.append(
            {
                "id": it["id"],
                "page_no": page,
                "locus_key": locus,
                "causal_chain": cc,
                "old_page": old_page,
                "old_locus": it.get("locus_key"),
            }
        )
    print(f"candidates={len(patches)}")
    if args.dry_run:
        for p in patches[:8]:
            print(
                json.dumps(
                    {
                        "id": p["id"],
                        "old_page": p["old_page"],
                        "new_page": p["page_no"],
                        "old_locus": p["old_locus"],
                        "new_locus": p["locus_key"],
                    },
                    ensure_ascii=False,
                )
            )
        print("dry-run done")
        return

    apply_script = r"""
import json, sys
from app.db.session import get_session_factory
from app.db.models import CorpusImprovementPoint
from app.util.json_util import dumps, loads
patches = json.loads(sys.stdin.read())
n = 0
s = get_session_factory()()
try:
    for p in patches:
        row = s.get(CorpusImprovementPoint, p["id"])
        if not row:
            continue
        claim = loads(row.claim_json, {}) or {}
        concrete = loads(row.concrete_json, {}) or {}
        if not isinstance(claim, dict):
            claim = {}
        if not isinstance(concrete, dict):
            concrete = {}
        row.page_no = int(p["page_no"])
        row.locus_key = str(p["locus_key"])
        claim["causal_chain"] = p.get("causal_chain") or claim.get("causal_chain") or {}
        concrete["causal_chain"] = claim["causal_chain"]
        concrete["page_no"] = int(p["page_no"])
        claim["page_no"] = int(p["page_no"])
        row.claim_json = dumps(claim)
        row.concrete_json = dumps(concrete)
        s.add(row)
        n += 1
    s.commit()
    print(f"updated {n}")
except Exception:
    s.rollback()
    raise
finally:
    s.close()
"""
    proc = subprocess.run(
        ["docker", "exec", "-i", "buddy-api", "python", "-c", apply_script],
        input=json.dumps(patches, ensure_ascii=False).encode(),
        capture_output=True,
    )
    sys.stdout.write(proc.stdout.decode())
    sys.stderr.write(proc.stderr.decode())
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()
