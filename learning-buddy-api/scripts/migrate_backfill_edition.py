#!/usr/bin/env python3
"""Hard-break migration: backfill edition_id on existing import jobs.

Corpus jobs: meta.textbook_profile / parse_config → edition_id column + meta.
35B jobs: profile_id already present; ensure non-empty (default seed only if empty).

Usage (inside buddy-api container or with PYTHONPATH):

  PYTHONPATH=/app python3 scripts/migrate_backfill_edition.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SEED = "hujiao_math_hs_v1"


def _loads(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main() -> int:
    from app.db.models import CorpusImportJob, Import35bJob
    from app.db.session import get_session_factory

    SessionLocal = get_session_factory()
    session = SessionLocal()
    updated_corpus = 0
    updated_35b = 0
    try:
        for job in session.query(CorpusImportJob).all():
            meta = _loads(job.meta_json)
            eid = (
                (getattr(job, "edition_id", None) or "").strip()
                or str(meta.get("edition_id") or "").strip()
                or str(meta.get("textbook_profile") or "").strip()
            )
            pc = meta.get("parse_config")
            if not eid and isinstance(pc, dict):
                eid = str(pc.get("edition_id") or pc.get("textbook_profile") or "").strip()
            if not eid:
                eid = SEED
            changed = False
            if getattr(job, "edition_id", "") != eid:
                job.edition_id = eid
                changed = True
            if meta.get("edition_id") != eid or meta.get("textbook_profile") != eid:
                meta["edition_id"] = eid
                meta["textbook_profile"] = eid
                if isinstance(pc, dict):
                    pc = dict(pc)
                    pc["edition_id"] = eid
                    pc["textbook_profile"] = eid
                    meta["parse_config"] = pc
                else:
                    meta["parse_config"] = {
                        "engine": meta.get("parse_engine") or "mineru_cloud",
                        "edition_id": eid,
                        "textbook_profile": eid,
                        "page_limit": meta.get("page_limit"),
                    }
                job.meta_json = json.dumps(meta, ensure_ascii=False)
                changed = True
            if changed:
                updated_corpus += 1

        for job in session.query(Import35bJob).all():
            pid = (job.profile_id or "").strip()
            if not pid:
                job.profile_id = SEED
                updated_35b += 1

        session.commit()
    finally:
        session.close()

    print(f"backfill done: corpus={updated_corpus} import35b={updated_35b} seed={SEED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
