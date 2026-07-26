"""Load textbook layout profiles — delegates to Edition Bundle loader.

Back-compat shim: existing callers keep using load_textbook_profile(profile_id).
New code should prefer app.services.editions.bundle.load_bundle.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from app.services.editions.bundle import SEED_HUJIAO, load_bundle

_DEFAULT_PROFILE_ID = SEED_HUJIAO


@lru_cache(maxsize=16)
def load_textbook_profile(profile_id: str = _DEFAULT_PROFILE_ID) -> dict[str, Any]:
    bundle = load_bundle(profile_id)
    data = bundle.profile_compat_dict()
    patterns = data.get("pedagogy_patterns") or {}
    data["_pedagogy_res"] = {k: re.compile(v) for k, v in patterns.items()}
    fig = data.get("figure_group") or {}
    cap = fig.get("caption_pattern")
    if cap:
        data["_fig_caption_re"] = re.compile(cap)
    # Attach compiled geometry helpers for engines that prefer named params.
    data["_edition_bundle"] = True
    return data


def default_profile_id() -> str:
    return SEED_HUJIAO


def clear_profile_cache() -> None:
    load_textbook_profile.cache_clear()
