"""Reciprocal Rank Fusion for hybrid retrieval."""
from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


def rrf_fuse(
    ranked_lists: list[list[T]],
    *,
    key_fn,
    k: int = 60,
    limit: int | None = None,
) -> list[T]:
    """Fuse multiple ranked lists via RRF. ``key_fn(item)`` must be hashable."""
    scores: dict[object, float] = {}
    first_item: dict[object, T] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            kid = key_fn(item)
            scores[kid] = scores.get(kid, 0.0) + 1.0 / (k + rank)
            first_item.setdefault(kid, item)
    ordered = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    out = [first_item[x] for x in ordered]
    if limit is not None:
        return out[:limit]
    return out
