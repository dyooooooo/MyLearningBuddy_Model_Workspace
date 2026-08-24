"""Tests for the declarative layout decision layer.

Covers the three things the layer promises: edition geometry actually reaches
the detector, layout policy lives in rule data, and pages the geometry is
unsure about get flagged for the vision model.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.services.document_ir import DocumentBlock
from app.services.editions.geometry import ColumnGeometry
from app.services.ir.column_detect import detect_columns
from app.services.ir.layout import (
    BlockInput,
    decide_page,
    extract_page_features,
    load_ruleset,
    validate_ruleset,
    xy_cut,
)
from app.services.ir.layout_enrich import enrich_blocks

HUJIAO = "hujiao_math_hs_v1"


def _page_features(boxes, *, types=None, page_no=1, geometry=None):
    geom = geometry or ColumnGeometry()
    inputs = [
        BlockInput(
            key=f"b{i}",
            bbox=bb,
            type=(types or {}).get(f"b{i}", "paragraph"),
        )
        for i, bb in enumerate(boxes)
    ]
    layout = detect_columns(boxes, geometry=geom)
    baseline = {inp.key: layout.zone_for_bbox(inp.bbox) for inp in inputs}
    return extract_page_features(
        inputs,
        page_no=page_no,
        geometry=geom,
        main_x0=layout.raw_main_x0,
        main_x1=layout.raw_main_x1,
        column_count=layout.column_count,
        column_method=layout.method,
        baseline_zones=baseline,
    )


# --- XY-cut ---------------------------------------------------------------


def test_xy_cut_finds_two_columns():
    boxes = [
        (80, 100, 400, 140),
        (80, 160, 400, 200),
        (80, 220, 400, 260),
        (560, 100, 900, 140),
        (560, 160, 900, 200),
        (560, 220, 900, 260),
    ]
    result = xy_cut(list(boxes))
    assert result.column_count == 2
    assert result.column_of(80, 400) == 0
    assert result.column_of(560, 900) == 1


def test_xy_cut_ignores_sparse_title_page():
    """Two boxes at two X positions are a title page, not two columns."""
    boxes = [(80, 100, 300, 150), (600, 100, 880, 150)]
    assert xy_cut(list(boxes)).column_count == 1


# --- geometry wiring ------------------------------------------------------


def test_edition_geometry_reaches_the_detector():
    """aside_max_width used to be a module constant — the bundle value was inert."""
    boxes = [
        [89, 100, 667, 200],
        [89, 220, 667, 400],
        [90, 420, 667, 600],
        [712, 300, 863, 328],  # 151 wide margin note
    ]
    note = [712, 300, 863, 328]
    wide_enough = detect_columns(boxes, geometry=ColumnGeometry(aside_max_width=320.0))
    too_narrow = detect_columns(boxes, geometry=ColumnGeometry(aside_max_width=100.0))
    assert wide_enough.zone_for_bbox(note) == "aside_right"
    assert too_narrow.zone_for_bbox(note) == "main"


def test_raw_main_edges_are_not_stretched_by_missed_asides():
    boxes = [
        [80, 100, 600, 200],
        [80, 220, 600, 400],
        [80, 420, 600, 600],
        [640, 300, 790, 325],  # short right note the aside gate rejects
    ]
    layout = detect_columns(boxes)
    assert layout.raw_main_x1 == pytest.approx(600.0)
    # The published band swallowed the note; the raw estimate did not.
    assert layout.main_x1 > layout.raw_main_x1


# --- rule data ------------------------------------------------------------


def test_shipped_rules_all_evaluate():
    """A rule that cannot evaluate would silently never match in production."""
    features = _page_features(
        [[80, 100, 600, 200], [80, 220, 600, 400], [640, 300, 790, 325]]
    )
    ruleset = load_ruleset(HUJIAO, 1, features.geometry)
    assert ruleset.rules, "hujiao layout_rules.yaml did not load"
    sample = {**features.as_names(), **features.blocks[0].as_names()}
    assert validate_ruleset(ruleset, sample) == []


def test_rules_interpolate_edition_geometry():
    geom = replace(ColumnGeometry(), aside_max_width=123.0)
    ruleset = load_ruleset(HUJIAO, 1, geom)
    tip_rule = next(r for r in ruleset.rules if r.id == "hujiao.right-margin-tip-rescue")
    assert "123.0" in tip_rule.when


def test_saved_rule_file_takes_effect_without_restart(tmp_path, monkeypatch):
    """Edition rules are bind-mounted; a cached ruleset must not outlive an edit."""
    from app.services.ir.layout import rules as rules_mod

    editions = tmp_path / "editions"
    (editions / "tmpbook").mkdir(parents=True)
    path = editions / "tmpbook" / "layout_rules.yaml"
    monkeypatch.setattr(rules_mod, "_EDITIONS", editions)
    rules_mod.clear_rules_cache()

    path.write_text(
        "rules:\n"
        "  - id: tmp.v1\n"
        "    when: 'true'\n"
        "    zone: main\n",
        encoding="utf-8",
    )
    assert [r.id for r in load_ruleset("tmpbook").rules if r.layer == "edition"] == ["tmp.v1"]

    path.write_text(
        "rules:\n"
        "  - id: tmp.v2\n"
        "    when: 'true'\n"
        "    zone: main\n",
        encoding="utf-8",
    )
    import os

    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 1))
    assert [r.id for r in load_ruleset("tmpbook").rules if r.layer == "edition"] == ["tmp.v2"]
    rules_mod.clear_rules_cache()


def test_rule_layers_are_ordered_base_then_edition():
    ruleset = load_ruleset(HUJIAO, 1, ColumnGeometry())
    layers = {r.id: r.layer for r in ruleset.rules}
    assert layers["base.wide-block-stays-main"] == "base"
    assert layers["hujiao.right-margin-tip-rescue"] == "edition"


# --- decisions ------------------------------------------------------------


def test_wide_block_decision_cites_the_base_rule():
    features = _page_features(
        [[80, 100, 600, 200], [80, 220, 600, 400], [80, 420, 600, 600]]
    )
    decision = decide_page(features, edition_id=HUJIAO)
    verdict = decision.decisions["b0"]
    assert verdict.zone == "main"
    assert verdict.rule_id == "base.wide-block-stays-main"
    assert not decision.escalate


def test_missed_right_margin_note_is_rescued_by_rule():
    features = _page_features(
        [
            [80, 100, 600, 200],
            [80, 220, 600, 400],
            [80, 420, 600, 600],
            [640, 300, 790, 325],
        ]
    )
    assert features.blocks[3].baseline_zone == "main"
    decision = decide_page(features, edition_id=HUJIAO)
    verdict = decision.decisions["b3"]
    assert verdict.zone == "aside_right"
    assert verdict.rule_id == "hujiao.right-margin-tip-rescue"


def _mixed_column_page():
    """Two-column at the top, single-column at the bottom (real p25 shape).

    The page-wide ``main_x0`` lands on the bottom section's left edge, so a
    margin note beside the *top* section is not ``left_of_main`` at all.
    """
    return [
        [329, 98, 907, 139],  # top body, right column
        [329, 173, 907, 214],
        [134, 235, 285, 275],  # margin note beside the top body
        [371, 247, 566, 267],  # short body lines — none is wide on its own
        [371, 273, 601, 292],
        [147, 524, 569, 542],  # bottom section: single column, starts far left
        [147, 546, 690, 565],
        [149, 860, 556, 879],
        [149, 883, 670, 902],
    ]


def test_band_main_ignores_a_distant_section():
    features = _page_features(_mixed_column_page())
    note = features.blocks[2]
    assert note.left_of_main is True  # page-wide edge happens to agree here
    assert note.has_band_main
    # Band main is the *top* body column, not the bottom section's left edge.
    assert note.band_main_x0 == pytest.approx(329.0)
    assert note.left_of_band_main


def test_body_line_in_a_single_column_page_is_not_a_margin_note():
    """Regression: a short body line must not be dragged into the margin."""
    features = _page_features(
        [
            [87, 100, 665, 160],  # wide body above
            [90, 170, 181, 190],  # short trailing line, same column
            [87, 200, 665, 260],  # wide body below
            [315, 280, 438, 320],
        ]
    )
    line = features.blocks[1]
    assert line.band_main_x0 == pytest.approx(87.0)
    assert not line.left_of_band_main
    decision = decide_page(features, edition_id=HUJIAO)
    assert decision.decisions["b1"].zone == "main"


def test_hanging_heading_is_not_a_margin_note():
    """「练习1.1(2)」 hangs into the left margin but belongs to the body flow."""
    features = _page_features(
        _mixed_column_page() + [[126, 252, 233, 269]],
        types={"b9": "heading"},
    )
    decision = decide_page(features, edition_id=HUJIAO)
    assert decision.decisions["b9"].zone == "main"
    # The paragraph note on the same page is still rescued.
    assert decision.decisions["b2"].zone == "aside_left"


def test_chapter_opener_title_stays_in_main():
    """p30-style opener: sparse page, decorative title must not become an aside."""
    features = _page_features(
        [
            [120, 200, 300, 280],  # decorative title, narrow
            [400, 210, 860, 270],  # single wide companion
        ],
        types={"b0": "title"},
    )
    decision = decide_page(features, edition_id=HUJIAO)
    assert decision.decisions["b0"].zone == "main"


def test_low_confidence_page_is_flagged_for_escalation():
    """An untuned textbook leaves margin notes in main — escalate, don't guess."""
    features = _page_features(
        [
            [100, 300, 270, 330],  # lone margin strip the aside gate rejects
            [400, 290, 900, 460],  # the only wide companion
        ]
    )
    assert features.blocks[0].baseline_zone == "main"
    # Base layer only: no edition rules, which is the state of a fresh textbook.
    decision = decide_page(features, ruleset=load_ruleset(None, 0, features.geometry))
    assert decision.escalate
    assert decision.low_confidence_keys == ["b0"]
    assert decision.decisions["b0"].evidence["reason"] == "main_but_margin_like"


def test_blocks_without_bbox_drag_the_page_into_escalation():
    features = _page_features([[80, 100, 600, 200], None, None])
    decision = decide_page(features, edition_id=HUJIAO)
    assert decision.escalate
    assert decision.decisions["b1"].rule_id == "base.no-bbox-is-uncertain"


# --- end to end through enrich -------------------------------------------


def test_enrich_records_rule_provenance():
    blocks = [
        DocumentBlock(
            id="p1",
            type="paragraph",
            page_no=5,
            order=1,
            bbox=[80, 100, 600, 200],
            text="正文一",
            meta={"mineru_type": "text"},
        ),
        DocumentBlock(
            id="p2",
            type="paragraph",
            page_no=5,
            order=2,
            bbox=[80, 220, 600, 400],
            text="正文二",
            meta={"mineru_type": "text"},
        ),
        DocumentBlock(
            id="p3",
            type="paragraph",
            page_no=5,
            order=3,
            bbox=[80, 420, 600, 600],
            text="正文三",
            meta={"mineru_type": "text"},
        ),
        DocumentBlock(
            id="tip",
            type="paragraph",
            page_no=5,
            order=4,
            bbox=[640, 300, 790, 325],
            text="提示",
            meta={"mineru_type": "text"},
        ),
    ]
    out = {b.id: b for b in enrich_blocks(blocks, profile_id=HUJIAO)}
    tip = out["tip"]
    assert tip.meta["layout_zone"] == "aside_right"
    assert tip.meta["layout_rule_id"] == "hujiao.right-margin-tip-rescue"
    assert tip.meta["layout_rule_layer"] == "edition"
    assert tip.meta["layout_confidence_score"] == pytest.approx(0.75)
    assert out["p1"].meta["layout_rule_id"] == "base.wide-block-stays-main"
