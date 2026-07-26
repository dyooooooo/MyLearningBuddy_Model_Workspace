from __future__ import annotations

from typing import Any

from app.math.normalize import normalize_plain
from app.schemas.common import RichText


def _rich_dict(rich: RichText) -> dict[str, Any]:
    return rich.model_dump()


def enrich_eval_with_rich(ev: dict[str, Any]) -> dict[str, Any]:
    """Add *_rich fields alongside existing string eval fields."""
    out = dict(ev)

    ref = str(out.get("reference_answer") or "").strip()
    if ref:
        out["reference_answer_rich"] = _rich_dict(normalize_plain(ref))

    steps = out.get("solution_steps") or []
    if isinstance(steps, list):
        out["solution_steps_rich"] = [
            _rich_dict(normalize_plain(str(s))) for s in steps if str(s).strip()
        ]

    reasoning = out.get("reasoning_steps") or []
    if isinstance(reasoning, list):
        out["reasoning_steps_rich"] = [
            _rich_dict(normalize_plain(str(s))) for s in reasoning if str(s).strip()
        ]

    match_notes = str(out.get("match_notes") or "").strip()
    if match_notes:
        out["match_notes_rich"] = _rich_dict(normalize_plain(match_notes))

    return out
