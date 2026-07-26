"""Canonical kp_id aliases and human-readable labels for A⁺ display."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.rag.corpus.pipeline import kp_label, kp_label_en
from app.schemas.common import LabelI18n

# Legacy / chunk kp_ids from RAG hits → syllabus kp_id
KP_CANONICAL_ALIASES: dict[str, str] = {
    "hs_trig_induction_and_definition": "math.hs.trigonometry.induction",
    "hs_trig_identity": "math.hs.trigonometry.induction",
    "set_complement_interval": "math.hs.sets.complement",
    "hs_math_power_function": "math.hs.functions.power",
    "hs_math_piecewise_functions": "math.hs.functions.piecewise",
    "hs_inequality_rational": "math.hs.inequality.rational",
}


def canonical_kp_id(kp_id: str) -> str:
    if not kp_id:
        return kp_id
    return KP_CANONICAL_ALIASES.get(kp_id, kp_id)


def _humanize_kp_id(kp_id: str) -> str:
    """English-style fallback from dotted kp_id."""
    s = kp_id
    if s.startswith("math.hs."):
        s = s[len("math.hs.") :]
    parts = [p.replace("_", " ").title() for p in s.replace("_", ".").split(".") if p]
    if not parts:
        return kp_id
    return " · ".join(parts[-2:] if len(parts) >= 2 else parts)


def resolve_kp_label(kp_id: str, session: Session | None = None, *, locale: str = "zh") -> str:
    pair = resolve_kp_label_pair(kp_id, session=session)
    return pair.en if locale == "en" else pair.zh


def resolve_kp_label_pair(kp_id: str, session: Session | None = None) -> LabelI18n:
    cid = canonical_kp_id(kp_id)
    zh = kp_label(cid)
    if not zh or zh == cid:
        if session is not None:
            from app.db.models import CorpusNode

            node = session.get(CorpusNode, cid)
            if node and node.label and node.label != cid:
                zh = node.label
        if not zh or zh == cid:
            human = _humanize_kp_id(cid)
            zh = human if human != cid else kp_id

    en = kp_label_en(cid)
    if not en or en == cid:
        en = _humanize_kp_id(cid)
    return LabelI18n(zh=zh, en=en)
