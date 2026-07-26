#!/usr/bin/env python3
"""Backfill print/handwriting segments and eval_json *_rich fields."""
from __future__ import annotations

import argparse
import logging

from app.db.session import get_session_factory
from app.db.models import QuestionEntity, Student
from app.math.enrich import enrich_eval_with_rich
from app.math.normalize import normalize_plain
from app.math.segments import segments_json_dumps
from app.services.eval_util import parse_eval_blob
from app.util.json_util import dumps

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def backfill_questions(
    *,
    student_id: str | None,
    homework_id: str | None,
    dry_run: bool,
) -> int:
    updated = 0
    factory = get_session_factory()
    with factory() as session:
        q = session.query(QuestionEntity)
        if student_id:
            q = q.filter(QuestionEntity.student_id == student_id)
        if homework_id:
            q = q.filter(QuestionEntity.homework_id == homework_id)
        rows = q.all()
        for row in rows:
            changed = False
            print_rich = normalize_plain(row.print_text or "")
            hw_rich = normalize_plain(row.handwriting or "")
            if row.print_segments_json != segments_json_dumps(print_rich):
                row.print_text = print_rich.raw
                row.print_segments_json = segments_json_dumps(print_rich)
                changed = True
            if row.handwriting_segments_json != segments_json_dumps(hw_rich):
                row.handwriting = hw_rich.raw
                row.handwriting_segments_json = segments_json_dumps(hw_rich)
                changed = True

            ev = parse_eval_blob(row.eval_json)
            if ev:
                enriched = enrich_eval_with_rich(ev)
                if enriched != ev:
                    row.eval_json = dumps(enriched)
                    changed = True

            if changed:
                updated += 1
                if not dry_run:
                    session.add(row)

        if not dry_run:
            session.commit()
    logger.info("backfill updated %d questions (dry_run=%s)", updated, dry_run)
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill math segments for questions")
    parser.add_argument("--student", help="Filter by student_id (e.g. bowen)")
    parser.add_argument("--homework-id", help="Filter by homework_id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    student_id = args.student
    if student_id:
        factory = get_session_factory()
        with factory() as session:
            st = session.get(Student, student_id)
            if st is None:
                raise SystemExit(f"Student not found: {student_id}")

    backfill_questions(
        student_id=student_id,
        homework_id=args.homework_id,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
