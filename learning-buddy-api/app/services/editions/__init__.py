"""Edition services package — shared read-only config for MinerU & 35B lines."""
from app.services.editions.assembler import assemble_prompt
from app.services.editions.bundle import (
    SEED_BASE,
    SEED_HUJIAO,
    clear_bundle_cache,
    load_bundle,
    list_seed_edition_ids,
    theme_css_path,
)
from app.services.editions.slots import get_slot, slot_for_refine_mode

__all__ = [
    "SEED_BASE",
    "SEED_HUJIAO",
    "assemble_prompt",
    "clear_bundle_cache",
    "get_slot",
    "list_seed_edition_ids",
    "load_bundle",
    "slot_for_refine_mode",
    "theme_css_path",
]
