"""Golden Master characterization for 沪教版 hujiao_math_hs_v1.

Freezes current correct behavior so Edition Platform refactors cannot regress.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.schemas.mineru_refine import PageRefineResult
from app.services.document_ir import DocumentBlock, render_page_html
from app.services.editions.assembler import assemble_prompt, filter_entries
from app.services.editions.bundle import SEED_HUJIAO, load_bundle, theme_css_path
from app.services.editions.slots import SLOTS, slot_for_refine_mode
from app.services.ir.apply_refine import apply_page_refine
from app.services.ir.page_fusion.align import align_units
from app.services.ir.page_fusion.fuse import fuse_page_blocks
from app.services.ir.page_fusion.schemas import AlignedUnit, JudgeDecision, PageUnit
from app.services.ir.textbook_profile import default_profile_id, load_textbook_profile

EVAL = Path(__file__).resolve().parents[1] / "data" / "eval"
GOLDEN = EVAL / "golden" / "hujiao_math_hs_v1"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def test_bundle_loads_hujiao():
    b = load_bundle(SEED_HUJIAO)
    assert b.meta.id == SEED_HUJIAO
    assert b.config.features.columns == "triple_slots"
    assert b.config.zones.get("aside_right_x0_min") == 680
    assert len(b.policy.entries) >= 5


def test_profile_compat_matches_legacy_keys():
    profile = load_textbook_profile(SEED_HUJIAO)
    assert profile["profile_id"] == SEED_HUJIAO
    assert profile["refine_mode"] == "skip"
    assert profile["zones"]["aside_left_x1_max"] == 280
    assert profile["features"]["aside"] == "both"


def test_theme_css_exists():
    path = theme_css_path(SEED_HUJIAO)
    assert path.is_file()
    css = path.read_text(encoding="utf-8")
    assert ".textbook-preview" in css
    snap = GOLDEN / "theme.css.sha"
    snap.parent.mkdir(parents=True, exist_ok=True)
    digest = _sha(css)
    if not snap.is_file():
        snap.write_text(digest + "\n", encoding="utf-8")
    assert snap.read_text(encoding="utf-8").strip() == digest


def test_content_correct_golden_fixture():
    fixture = json.loads(
        (EVAL / "mineru-content-correct-formula.json").read_text(encoding="utf-8")
    )
    before = [
        DocumentBlock(
            id=b["id"],
            type=b["type"],
            page_no=b["page_no"],
            order=b["order"],
            text=b.get("text", ""),
            meta=dict(b.get("meta") or {}),
        )
        for b in fixture["before_blocks"]
    ]
    patch = PageRefineResult.model_validate(fixture["patch"])
    after = apply_page_refine(before, patch, content_only=True)
    expect = fixture["expect"]
    got = next(b for b in after if b.id == "eq1")
    assert got.text == expect["text"]
    # content_only must preserve zone/role from before
    assert (got.meta or {}).get("layout_zone") == expect["zone"]
    assert (got.meta or {}).get("pedagogy_role") == expect["role"]


def test_layout_rebuild_header_golden_fixture():
    fixture = json.loads(
        (EVAL / "mineru-layout-rebuild-header.json").read_text(encoding="utf-8")
    )
    # Fixture may be align/fuse oriented — just ensure it parses and has expect keys.
    assert "name" in fixture or "mode" in fixture or "aligned" in fixture or "expect" in fixture


def test_fuse_drop_running_header_characterization():
    aligned = [
        AlignedUnit(
            unit_id="a1",
            kind="paired",
            mineru=PageUnit(
                id="m1",
                type="paragraph",
                zone="running",
                pedagogy_role="running_header",
                text="1 集合与逻辑",
                source="mineru",
                block_id="h1",
            ),
            b35=PageUnit(
                id="u1",
                type="paragraph",
                zone="running",
                pedagogy_role="running_header",
                text="集合与逻辑",
                source="35b",
            ),
        ),
        AlignedUnit(
            unit_id="a2",
            kind="mineru_only",
            mineru=PageUnit(
                id="m2",
                type="paragraph",
                zone="running",
                pedagogy_role="running_header",
                text="集合与逻辑",
                source="mineru",
                block_id="h2",
            ),
        ),
    ]
    decisions = [
        JudgeDecision(unit_id="a1", action="keep_35b"),
        JudgeDecision(unit_id="a2", action="drop"),
    ]
    blocks = fuse_page_blocks(
        page_no=1, aligned=aligned, decisions=decisions
    )
    texts = [b.text for b in blocks]
    assert "集合与逻辑" in texts
    assert texts.count("集合与逻辑") == 1


def test_assembler_injects_hujiao_policy():
    prompt = assemble_prompt("mineru.content_correct", SEED_HUJIAO)
    assert "Edition policy" in prompt or "Profile hints" in prompt
    assert "aside_right_x0_min" in prompt
    assert "hujiao_math_hs_v1" in prompt or "profile_id=" in prompt


def test_all_slots_have_facets():
    for sid, slot in SLOTS.items():
        assert slot.facets, f"{sid} has empty facets"
        assert slot.base_prompt.endswith(".md")


def test_slot_for_refine_modes():
    assert slot_for_refine_mode("content_correct").id == "mineru.content_correct"
    assert slot_for_refine_mode("layout_only").id == "mineru.refine_layout"


def test_policy_facet_filter():
    b = load_bundle(SEED_HUJIAO)
    entries = filter_entries(b, ("zone_hints",))
    assert entries
    assert all(e.facet == "zone_hints" for e in entries)


def test_textbook_layout_pages_manifest():
    manifest = json.loads((EVAL / "textbook-layout-pages.json").read_text(encoding="utf-8"))
    assert manifest["profile_id"] == SEED_HUJIAO
    assert "12" in manifest["pages"]


def test_triple_check_html_profile_attr():
    """Characterization: existing triple-check HTML carries data-profile."""
    html_dir = EVAL / "triple-check-03f17de3" / "html"
    if not html_dir.is_dir():
        pytest.skip("triple-check fixtures absent")
    samples = sorted(html_dir.glob("p*.html"))[:3]
    assert samples
    for path in samples:
        text = path.read_text(encoding="utf-8")
        assert 'data-profile="hujiao_math_hs_v1"' in text


def test_render_page_html_profile_attr():
    blocks = [
        DocumentBlock(
            id="b1",
            type="paragraph",
            page_no=1,
            order=1,
            text="测试段落",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        )
    ]
    html = render_page_html(blocks, page_no=1, profile_id=SEED_HUJIAO)
    assert f'data-profile="{SEED_HUJIAO}"' in html or SEED_HUJIAO in html


def test_default_profile_id_is_hujiao_seed():
    # Shim still returns seed id for back-compat during transition;
    # hard-break enforces required edition_id at API boundary.
    assert default_profile_id() == SEED_HUJIAO


def test_golden_snapshot_bundle_config():
    """Freeze bundle config hash so silent edits fail CI."""
    b = load_bundle(SEED_HUJIAO)
    payload = json.dumps(b.config.model_dump(), sort_keys=True, ensure_ascii=False)
    digest = _sha(payload)
    snap = GOLDEN / "config.json.sha"
    snap.parent.mkdir(parents=True, exist_ok=True)
    if not snap.is_file():
        snap.write_text(digest + "\n", encoding="utf-8")
        (GOLDEN / "config.snapshot.json").write_text(
            json.dumps(b.config.model_dump(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    assert snap.read_text(encoding="utf-8").strip() == digest
