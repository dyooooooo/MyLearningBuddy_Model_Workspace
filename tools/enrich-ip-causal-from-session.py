#!/usr/bin/env python3
"""Backfill causal_chain (user → AI → guard) onto pipeline/fs_violation IPs.

Usage:
  python3 tools/enrich-ip-causal-from-session.py --session e76d566110884eae --dry-run
  python3 tools/enrich-ip-causal-from-session.py --session e76d566110884eae
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "buddy-copilot"))
from app.causal_trace import trace_fs_violation  # noqa: E402


def _load_messages(db: Path, session_id: str) -> list[dict]:
    con = sqlite3.connect(str(db))
    rows = con.execute(
        "SELECT seq, role, content FROM messages WHERE session_id=? ORDER BY seq",
        (session_id,),
    ).fetchall()
    con.close()
    out = []
    for seq, role, content in rows:
        try:
            obj = json.loads(content or "{}")
        except json.JSONDecodeError:
            obj = {"text": content}
        out.append({"seq": seq, "role": role, "content": obj})
    return out


def _load_admin_token() -> str:
    env = ROOT / "learning-buddy-c2" / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            if line.startswith("ADMIN_TOKEN="):
                return line.split("=", 1)[1].strip()
    return "change-me-admin"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument(
        "--copilot-db",
        default=str(ROOT / "buddy-copilot" / "data" / "copilot.db"),
    )
    ap.add_argument("--token", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="overwrite existing causal_chain")
    args = ap.parse_args()
    token = args.token or _load_admin_token()

    messages = _load_messages(Path(args.copilot_db), args.session)

    # Fetch IPs via docker exec (admin path inside container)
    list_script = r"""
import json
from app.db.session import get_session_factory
from app.db.models import CorpusImprovementPoint
from app.util.json_util import loads
s = get_session_factory()()
try:
    rows = s.query(CorpusImprovementPoint).all()
    items = []
    for r in rows:
        claim = loads(r.claim_json, {}) or {}
        concrete = loads(r.concrete_json, {}) or {}
        items.append({
            "id": r.id,
            "source_session_id": r.source_session_id or "",
            "mode": claim.get("mode") or concrete.get("mode") or "",
            "origin": claim.get("origin") or concrete.get("origin") or "",
            "authority": r.authority or "",
            "summary": (claim.get("summary") or "")[:200],
            "paths": claim.get("paths") or concrete.get("paths") or [],
            "detail": claim.get("detail") or concrete.get("detail") or "",
            "has_chain": bool(claim.get("causal_chain") or concrete.get("causal_chain")),
            "has_user": bool(claim.get("user_input") or concrete.get("user_input")),
        })
    print(json.dumps(items, ensure_ascii=False))
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

    updates = []
    for it in items:
        if str(it.get("source_session_id") or "") != args.session:
            continue
        mode = str(it.get("mode") or "")
        auth = str(it.get("authority") or "")
        summary = str(it.get("summary") or "")
        is_guard = (
            mode == "fs_violation"
            or ("越权" in summary and "回滚" in summary)
            or (auth == "pipeline" and "回滚" in summary)
        )
        if not is_guard:
            continue
        if it.get("has_chain") and not args.force:
            continue
        chain = trace_fs_violation(
            messages,
            paths=list(it.get("paths") or []),
            detail=str(it.get("detail") or ""),
        )
        if not chain.get("user_input") and not chain.get("ai_summary"):
            continue
        updates.append(
            {
                "id": it["id"],
                "user_input": str(chain.get("user_input") or "")[:2000],
                "causal_chain": {
                    "user_requests": list(chain.get("user_requests") or [])[:3],
                    "ai_summary": str(chain.get("ai_summary") or "")[:1500],
                    "ai_tools": list(chain.get("ai_tools") or [])[:12],
                    "narrative": str(chain.get("narrative") or "")[:2500],
                    "guard_seq": int(chain.get("guard_seq") or 0),
                    "page_no": int(chain.get("page_no") or 0),
                    "block_id": str(chain.get("block_id") or ""),
                    "job_id": str(chain.get("job_id") or ""),
                    "dialogue_slot": str(chain.get("dialogue_slot") or ""),
                    "user_input_display": str(chain.get("user_input_display") or "")[
                        :2000
                    ],
                },
            }
        )

    print(f"session_msgs={len(messages)} ips={len(items)} to_enrich={len(updates)}")
    if args.dry_run:
        for u in updates[:5]:
            print(
                json.dumps(
                    {
                        "id": u["id"],
                        "user_input": (u["user_input"] or "")[:120],
                        "ai": (u["causal_chain"].get("ai_summary") or "")[:120],
                        "guard_seq": u["causal_chain"].get("guard_seq"),
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
        if p.get("user_input"):
            claim["user_input"] = p["user_input"]
            concrete["user_input"] = p["user_input"]
        if p.get("causal_chain"):
            claim["causal_chain"] = p["causal_chain"]
            concrete["causal_chain"] = p["causal_chain"]
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
        input=json.dumps(updates, ensure_ascii=False).encode(),
        capture_output=True,
    )
    sys.stdout.write(proc.stdout.decode())
    sys.stderr.write(proc.stderr.decode())
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()
