"""Edition pattern accessors for adapters (TOC / chapter / figure / solution).

Engines keep compiled defaults matching ``_base``; prefer ``patterns_for(edition_id)``
when an edition is known.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Pattern

from app.services.editions.bundle import SEED_BASE, load_bundle


@dataclass(frozen=True)
class EditionPatterns:
    chapter: Pattern[str]
    lesson: Pattern[str]
    fig_label: Pattern[str]
    fig_ref: Pattern[str]
    solution_lead: Pattern[str]
    toc_trailing_page: Pattern[str]
    chapter_toc: Pattern[str]
    toc_section: Pattern[str]
    toc_summary: Pattern[str]
    toc_titles: tuple[str, ...]


@lru_cache(maxsize=16)
def patterns_for(edition_id: str | None = None) -> EditionPatterns:
    eid = (edition_id or SEED_BASE).strip() or SEED_BASE
    try:
        bundle = load_bundle(eid)
        p = bundle.config.patterns
    except (FileNotFoundError, ValueError):
        bundle = load_bundle(SEED_BASE)
        p = bundle.config.patterns
    return EditionPatterns(
        chapter=re.compile(p.chapter),
        lesson=re.compile(p.lesson),
        fig_label=re.compile(p.fig_label),
        fig_ref=re.compile(p.fig_ref),
        solution_lead=re.compile(p.solution_lead),
        toc_trailing_page=re.compile(p.toc_trailing_page),
        chapter_toc=re.compile(p.chapter_toc),
        toc_section=re.compile(p.toc_section),
        toc_summary=re.compile(p.toc_summary),
        toc_titles=tuple(p.toc_titles),
    )


def clear_pattern_cache() -> None:
    patterns_for.cache_clear()
