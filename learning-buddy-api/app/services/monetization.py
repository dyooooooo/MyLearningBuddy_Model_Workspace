from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Student

logger = logging.getLogger(__name__)

TIER_TRIAL = "trial"
TIER_FREE = "free"
TIER_PAID = "paid"
TIER_B2B = "b2b"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_trial_started(student: Student) -> None:
    if student.tier not in ("", TIER_TRIAL) and student.trial_started_at:
        return
    if not student.trial_started_at:
        student.trial_started_at = _now().isoformat()
        student.trial_expires_at = (_now() + timedelta(days=settings.trial_days)).isoformat()
        student.tier = TIER_TRIAL


def effective_tier(student: Student) -> str:
    ensure_trial_started(student)
    if student.tier == TIER_PAID or student.tier == TIER_B2B:
        return student.tier
    if student.tier == TIER_TRIAL and student.trial_expires_at:
        try:
            exp = datetime.fromisoformat(student.trial_expires_at.replace("Z", "+00:00"))
            if _now() < exp:
                return TIER_TRIAL
        except ValueError:
            return TIER_TRIAL
    return TIER_FREE


def tier_features(tier: str) -> set[str]:
    if tier in (TIER_TRIAL, TIER_PAID, TIER_B2B):
        free = set(settings.tier_free_features.split(","))
        paid = set(settings.tier_paid_features.split(","))
        b2b = set(settings.tier_b2b_features.split(","))
        out = free | paid
        if tier == TIER_B2B:
            out |= b2b
        return {x.strip() for x in out if x.strip()}
    return {x.strip() for x in settings.tier_free_features.split(",") if x.strip()}


def has_feature(student: Student, feature: str) -> bool:
    return feature in tier_features(effective_tier(student))


def require_feature(session: Session, student_id: str, feature: str) -> bool:
    st = session.get(Student, student_id)
    if st is None:
        return False
    return has_feature(st, feature)
