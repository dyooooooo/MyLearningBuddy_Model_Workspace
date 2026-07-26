#!/usr/bin/env python3
"""CLI wrapper for main-line structure-merge (not a special deepen path).

Prefer HTTP:
  POST /api/v1/admin/corpus/import/jobs/{job_id}/structure-merge
  {"purge": true}

This script only calls ``rerun_import_structure_merge`` — same as the API / worker.
"""
from __future__ import annotations

import argparse
import time

from app.db.session import get_session_factory
from app.services.corpus_import_incremental import rerun_import_structure_merge
from app.util.json_util import loads


def main() -> None:
    ap = argparse.ArgumentParser(description="Re-run import structure merge + TOC deepen")
    ap.add_argument("job_id")
    ap.add_argument("--no-purge", action="store_true", help="Skip purge of stale TOC nodes")
    args = ap.parse_args()

    db = get_session_factory()()
    try:
        t0 = time.perf_counter()
        print(
            "rerun via main pipeline: rerun_import_structure_merge",
            "purge=",
            not args.no_purge,
            flush=True,
        )
        job = rerun_import_structure_merge(
            db, args.job_id, purge=not args.no_purge
        )
        db.commit()
        meta = loads(job.meta_json, {}) or {}
        deepen = meta.get("toc_deepen") or {}
        print(
            "done status=",
            job.status,
            "structure_status=",
            meta.get("structure_status"),
            "deepened=",
            len(deepen.get("deepened_parents") or []),
            "review=",
            len(deepen.get("needs_structure_review") or []),
            "leaf_count=",
            deepen.get("leaf_count"),
            f"elapsed={time.perf_counter() - t0:.1f}s",
            flush=True,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
