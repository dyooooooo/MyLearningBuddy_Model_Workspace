"""Enrich PagePart with print_rich / handwriting_rich via buddy-api math normalize."""

from __future__ import annotations

import logging

from app.buddy_client import normalize_page_parts
from app.schemas import PagePart

logger = logging.getLogger(__name__)


async def enrich_page_parts(parts: list[PagePart]) -> list[PagePart]:
    if not parts:
        return parts
    try:
        enriched = await normalize_page_parts(parts)
        return enriched
    except Exception:
        logger.exception("math enrich failed; returning parts without rich")
        return parts
