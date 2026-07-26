"""Unit tests for Edition Bundle loader / assembler (no LLM)."""
from __future__ import annotations

import pytest

from app.services.editions.assembler import assemble_prompt, filter_entries, render_policy_markdown
from app.services.editions.bundle import (
    SEED_BASE,
    SEED_HUJIAO,
    clear_bundle_cache,
    list_seed_edition_ids,
    load_bundle,
)
from app.services.editions.slots import get_slot


def setup_function():
    clear_bundle_cache()


def test_list_seeds():
    ids = list_seed_edition_ids()
    assert SEED_HUJIAO in ids
    assert SEED_BASE in ids


def test_load_base():
    b = load_bundle(SEED_BASE)
    assert b.meta.id == SEED_BASE
    assert b.config.features.columns == "single"


def test_hard_break_empty_edition_id():
    with pytest.raises(ValueError, match="edition_id"):
        load_bundle("")


def test_unknown_edition():
    with pytest.raises(FileNotFoundError):
        load_bundle("does_not_exist_xyz")


def test_assemble_all_mineru_slots():
    for sid in (
        "mineru.refine_layout",
        "mineru.refine_full",
        "mineru.rerecognize",
        "mineru.content_correct",
        "mineru.layout_rebuild",
        "mineru.unit_judge",
    ):
        text = assemble_prompt(sid, SEED_HUJIAO)
        assert isinstance(text, str)
        assert len(text) > 50


def test_render_policy_empty():
    assert render_policy_markdown([], edition_id="x") == ""


def test_get_slot_unknown():
    with pytest.raises(KeyError):
        get_slot("no.such.slot")


def test_profile_compat_dict_has_geometry():
    b = load_bundle(SEED_HUJIAO)
    d = b.profile_compat_dict()
    assert "geometry" in d
    assert d["geometry"]["wide_min"] == 380.0
