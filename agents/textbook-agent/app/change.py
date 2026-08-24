"""Record a workspace change: generation bump, checkpoint, event."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def after_write(kind: str, note: str = "", **extra: Any) -> int:
    from app import bus, checkpoints, config_store

    gen = config_store.bump_generation()
    try:
        checkpoints.snapshot(kind=kind, note=note or kind)
    except Exception:
        logger.warning("checkpoint after %s failed", kind, exc_info=True)
    event = kind if kind in bus.EVENT_TYPES else "config"
    bus.publish(event, generation=gen, note=note or kind, **extra)
    return gen
