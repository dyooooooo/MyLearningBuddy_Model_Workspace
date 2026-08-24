#!/usr/bin/env python3
"""Enrich thin improvement-point claims from Copilot session effective_changes.

One-shot migration helper (Cursor/admin). Does not create a Studio migrate UI.

Usage (on host with buddy-api DB reachable via docker exec):

  python3 tools/enrich-ip-from-session.py \\
    --session e76d566110884eae \\
    --copilot-db buddy-copilot/data/copilot.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import urllib.error
import urllib.request


def _load_ecs(db: Path, session_id: str) -> list[dict]:
    con = sqlite3.connect(str(db))
    row = con.execute(
        "SELECT payload FROM sessions WHERE id=?", (session_id,)
    ).fetchone()
    con.close()
    if not row:
        raise SystemExit(f"session not found: {session_id}")
    pl = json.loads(row[0] or "{}")
    return [c for c in (pl.get("effective_changes") or []) if isinstance(c, dict)]


def _user_near(db: Path, session_id: str, confirmed_at: str) -> str:
    """Best-effort: last user message before confirmed_at (ISO or empty)."""
    con = sqlite3.connect(str(db))
    rows = con.execute(
        "SELECT content, created_at FROM messages WHERE session_id=? AND role='user' "
        "ORDER BY created_at DESC LIMIT 40",
        (session_id,),
    ).fetchall()
    con.close()
    # confirmed_at is ISO; messages are unix float — just take most recent focus text
    for content, _ts in rows:
        try:
            obj = json.loads(content or "{}")
            text = str(obj.get("text") or content or "").strip()
        except json.JSONDecodeError:
            text = str(content or "").strip()
        if "【聚焦目标】" in text or text:
            # Prefer focus instructions
            if "【聚焦目标】" in text:
                return text[:2000]
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument(
        "--copilot-db",
        default=str(
            Path(__file__).resolve().parents[1]
            / "buddy-copilot"
            / "data"
            / "copilot.db"
        ),
    )
    ap.add_argument(
        "--api",
        default="http://127.0.0.1:8100",
        help="buddy-api base URL",
    )
    ap.add_argument("--token", default="change-me-admin")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ecs = _load_ecs(Path(args.copilot_db), args.session)
    # index by summary for pipeline-ish entries with paths/detail
    by_sum: dict[str, list[dict]] = {}
    for c in ecs:
        s = str(c.get("summary") or "").strip()
        if not s:
            continue
        by_sum.setdefault(s, []).append(c)

    req = urllib.request.Request(
        f"{args.api.rstrip('/')}/api/v1/corpus/improvement-points?limit=1000",
        headers={"Authorization": f"Bearer {args.token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        # try via gateway
        print(f"direct api failed {e}; try gateway proxy", file=sys.stderr)
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/v1/studio/proxy/improvement-points?limit=1000",
            headers={"x-studio-token": args.token},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())

    items = data.get("items") or []
    # Prefer SQL update inside buddy-api container for reliability
    updates: list[dict] = []
    for it in items:
        if str(it.get("source_session_id") or "") != args.session:
            continue
        claim = it.get("claim") if isinstance(it.get("claim"), dict) else {}
        summary = str(claim.get("summary") or "").strip()
        if not summary:
            continue
        # already rich?
        if claim.get("paths") and (claim.get("detail") or claim.get("mode")):
            continue
        cands = by_sum.get(summary) or []
        if not cands:
            continue
        # pick richest candidate
        cands = sorted(
            cands,
            key=lambda c: (
                len(c.get("paths") or []),
                len(str(c.get("detail") or "")),
            ),
            reverse=True,
        )
        ec = cands[0]
        user_in = ""
        if ec.get("authority") != "pipeline" and not str(ec.get("mode") or "").startswith(
            "fs_"
        ):
            user_in = _user_near(Path(args.copilot_db), args.session, str(ec.get("confirmed_at") or ""))
        patch = {
            "id": it["id"],
            "paths": list(ec.get("paths") or [])[:40],
            "detail": str(ec.get("detail") or "")[:2000],
            "mode": str(ec.get("mode") or ""),
            "proposer": str(ec.get("proposer") or "system"),
            "authority_label": str(ec.get("authority_label") or ""),
            "user_input": user_in,
            "summary": summary,
        }
        updates.append(patch)

    print(f"session ecs={len(ecs)} ips={len(items)} to_enrich={len(updates)}")
    if args.dry_run:
        for u in updates[:5]:
            print(json.dumps(u, ensure_ascii=False)[:400])
        print("dry-run done")
        return

    # Apply via docker exec into postgres using buddy-api python
    script = r"""
import json, sys
from app.db.session import get_session_factory
from app.db.models import CorpusImprovementPoint
from app.util.json_util import dumps, loads

patches = json.loads(sys.stdin.read())
n = 0
SessionLocal = get_session_factory()
s = SessionLocal()
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
        for k in ("paths", "detail", "mode", "proposer", "authority_label", "user_input"):
            if p.get(k) not in (None, "", []):
                claim[k] = p[k]
                concrete[k] = p[k]
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
    import subprocess

    proc = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "buddy-api",
            "python",
            "-c",
            script,
        ],
        input=json.dumps(updates, ensure_ascii=False).encode(),
        capture_output=True,
    )
    sys.stdout.write(proc.stdout.decode())
    sys.stderr.write(proc.stderr.decode())
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()
