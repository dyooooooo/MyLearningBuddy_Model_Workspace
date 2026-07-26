#!/usr/bin/env python3
"""Build Cursor Repair Pack(s) from import35b feedback rows."""
from __future__ import annotations

import argparse
import sys

from app.db import models  # noqa: F401
from app.db.session import get_session_factory
from app.import_35b import store
from app.import_35b.cursor_pack.builder import build_cursor_pack


def main() -> int:
    ap = argparse.ArgumentParser(description="Build 35B Cursor Repair Pack")
    ap.add_argument("--feedback-id", default="", help="Single feedback UUID")
    ap.add_argument("--job-id", default="", help="Build all open feedback for job")
    ap.add_argument("--all-open", action="store_true", help="With --job-id, all open items")
    args = ap.parse_args()

    factory = get_session_factory()
    ids: list[str] = []
    if args.feedback_id:
        ids = [args.feedback_id]
    elif args.job_id:
        with factory() as session:
            rows = store.list_feedback(
                session, job_id=args.job_id, status="open" if args.all_open else None
            )
            ids = [r.id for r in rows]
    else:
        ap.error("need --feedback-id or --job-id")

    if not ids:
        print("no feedback to pack", file=sys.stderr)
        return 1

    for fid in ids:
        with factory() as session:
            row = store.get_feedback(session, fid)
            if row is None:
                print(f"skip missing feedback {fid}", file=sys.stderr)
                continue
            out = build_cursor_pack(session, row)
            session.commit()
            print(out.get("absolute_path") or out.get("pack_path"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
