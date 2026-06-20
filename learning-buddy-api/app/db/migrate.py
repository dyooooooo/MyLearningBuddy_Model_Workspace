from __future__ import annotations

from sqlalchemy import Engine, text

from app.config import settings


def apply_pilot_migrations(engine: Engine) -> None:
    """Add M2 columns/tables on existing PostgreSQL pilot DB (create_all is non-destructive)."""
    if not settings.use_postgres:
        return
    statements = [
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS tier VARCHAR(32) DEFAULT 'trial'",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS trial_started_at VARCHAR(64) DEFAULT ''",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS trial_expires_at VARCHAR(64) DEFAULT ''",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS org_id VARCHAR(64) DEFAULT ''",
        "ALTER TABLE question_entities ADD COLUMN IF NOT EXISTS eval_confidence DOUBLE PRECISION",
        "ALTER TABLE question_entities ADD COLUMN IF NOT EXISTS eval_json TEXT DEFAULT '{}'",
        "ALTER TABLE question_entities ADD COLUMN IF NOT EXISTS dwell_ms INTEGER",
        "ALTER TABLE mastery_views ADD COLUMN IF NOT EXISTS fluency DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE mastery_views ADD COLUMN IF NOT EXISTS fluency_ratio DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE mastery_views ADD COLUMN IF NOT EXISTS wrong_rate DOUBLE PRECISION DEFAULT 0",
    ]
    with engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))
