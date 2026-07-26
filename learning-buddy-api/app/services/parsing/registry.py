"""Engine registry — single source of truth for parse engine IDs and capabilities.

All engine names used in schemas, validators, and UI come from here so that
adding a new engine requires changes in one place only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# NOTE: The 35B layout line (``vision_layout_35b``) and the legacy
# ``vision_ocr`` engine were removed from this MinerU registry. 35B now runs as
# a fully isolated pipeline (app.import_35b.*) with its own API/queue/tables.
# This registry serves ONLY the MinerU line.
EngineId = Literal["mineru_cloud"]


@dataclass(frozen=True)
class EngineSpec:
    id: str
    label_zh: str
    supports_page_limit: bool = False


ENGINES: dict[str, EngineSpec] = {
    "mineru_cloud": EngineSpec(
        id="mineru_cloud",
        label_zh="MinerU 精准解析",
        # Cloud API supports file.page_ranges (e.g. "1-N"); see mineru.net docs.
        supports_page_limit=True,
    ),
}

VALID_ENGINE_IDS: frozenset[str] = frozenset(ENGINES)

__all__ = ["EngineSpec", "ENGINES", "VALID_ENGINE_IDS", "EngineId"]
