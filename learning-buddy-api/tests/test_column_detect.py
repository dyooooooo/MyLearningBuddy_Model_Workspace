"""Tests for bbox column detection (conservative main-edge approach)."""
import pytest

from app.services.document_ir import DocumentBlock
from app.services.ir.column_detect import detect_columns
from app.services.ir.layout_enrich import enrich_blocks


def test_detect_left_aside_and_main():
    # Cantor-style: narrow left portrait + wide main body
    boxes = [
        [161, 302, 260, 412],  # figure left
        [160, 420, 255, 480],  # caption left
        [320, 120, 880, 180],  # title main (wide-ish)
        [329, 200, 909, 280],  # paragraph main
        [329, 300, 907, 500],  # more main
    ]
    layout = detect_columns(boxes)
    assert layout.column_count == 2
    assert layout.zone_for_bbox([161, 302, 260, 412]) == "aside_left"
    assert layout.zone_for_bbox([329, 200, 909, 280]) == "main"


def test_short_exercise_lines_stay_in_main():
    """Short 练习/list lines share main x0 — must NOT become aside_left."""
    boxes = [
        [105, 100, 216, 120],  # 练习1.1(1) short heading
        [131, 140, 391, 160],  # short list item
        [131, 170, 294, 190],  # short math line
        [87, 220, 668, 300],  # wide body
        [89, 320, 668, 420],  # wide body
        [89, 450, 668, 550],
    ]
    layout = detect_columns(boxes)
    assert layout.zone_for_bbox([105, 100, 216, 120]) == "main"
    assert layout.zone_for_bbox([131, 140, 391, 160]) == "main"
    assert layout.zone_for_bbox([87, 220, 668, 300]) == "main"
    assert layout.column_count == 1


def test_proposition_examples_stay_in_main():
    boxes = [
        [107, 200, 236, 230],  # 1 命题
        [129, 250, 371, 270],  # short example
        [131, 280, 250, 300],
        [87, 120, 668, 190],  # wide intro
        [87, 350, 668, 500],
    ]
    layout = detect_columns(boxes)
    assert layout.zone_for_bbox([107, 200, 236, 230]) == "main"
    assert layout.zone_for_bbox([129, 250, 371, 270]) == "main"


def test_detect_main_and_right_aside():
    boxes = [
        [80, 100, 620, 200],
        [80, 220, 600, 400],
        [80, 420, 610, 600],
        [720, 300, 900, 500],  # tall right note
        [730, 520, 880, 700],
    ]
    layout = detect_columns(boxes)
    assert layout.zone_for_bbox([720, 300, 900, 500]) == "aside_right"
    assert layout.zone_for_bbox([80, 100, 620, 200]) == "main"


def test_enrich_assigns_cantor_figure_left():
    blocks = [
        DocumentBlock(
            id="fig",
            type="figure",
            page_no=9,
            order=1,
            bbox=[161, 302, 260, 412],
            caption="康托(G. Cantor，1845—1918)，德国数学家，集合论创始人.",
            asset_url="/x.jpg",
            meta={"mineru_type": "image"},
        ),
        DocumentBlock(
            id="t1",
            type="title",
            page_no=9,
            order=2,
            bbox=[329, 100, 800, 160],
            text="1.1 集合初步",
            meta={"mineru_type": "title"},
        ),
        DocumentBlock(
            id="p1",
            type="paragraph",
            page_no=9,
            order=3,
            bbox=[329, 200, 909, 400],
            text="我们经常需要把满足一定要求的对象放在一起.",
            meta={"mineru_type": "text"},
        ),
        DocumentBlock(
            id="pn",
            type="paragraph",
            page_no=9,
            order=4,
            bbox=[40, 950, 70, 980],
            text="2",
            meta={"mineru_type": "page_number", "pedagogy_role": "page_number"},
        ),
    ]
    out = enrich_blocks(blocks)
    by_id = {b.id: b for b in out}
    assert by_id["fig"].meta.get("layout_zone") == "aside_left"
    assert by_id["t1"].meta.get("layout_zone") == "main"
    assert by_id["p1"].meta.get("layout_zone") == "main"
    assert by_id["pn"].meta.get("layout_zone") == "running"
    assert by_id["pn"].meta.get("chrome_slot") == "aside_left"


def test_enrich_exercise_page_no_false_aside():
    blocks = [
        DocumentBlock(
            id="h",
            type="heading",
            page_no=10,
            order=1,
            bbox=[105, 100, 216, 120],
            text="练习1.1(1)",
            meta={"mineru_type": "title"},
        ),
        DocumentBlock(
            id="l1",
            type="list",
            page_no=10,
            order=2,
            bbox=[131, 140, 391, 160],
            text="（1）上海市现有各区的名称；",
            meta={"mineru_type": "list"},
        ),
        DocumentBlock(
            id="p1",
            type="paragraph",
            page_no=10,
            order=3,
            bbox=[87, 220, 668, 400],
            text="不含有任何元素的集合称为空集，记作∅．引进空集是有必要的．",
            meta={"mineru_type": "text"},
        ),
        DocumentBlock(
            id="p2",
            type="paragraph",
            page_no=10,
            order=4,
            bbox=[89, 420, 668, 600],
            text="解（1）6的正整数倍可表示为6n，其中n是正整数．",
            meta={"mineru_type": "text"},
        ),
    ]
    out = enrich_blocks(blocks)
    zones = {b.id: b.meta.get("layout_zone") for b in out}
    assert zones["h"] == "main"
    assert zones["l1"] == "main"
    assert zones["p1"] == "main"


def test_tip_boxes_left_of_indented_main():
    """p25-style: tip column at x≈130, body at x≈330, wide exercises at x≈150."""
    boxes = [
        [132, 200, 287, 270],  # tip
        [134, 280, 287, 320],  # tip
        [132, 340, 287, 450],  # tip tall
        [329, 200, 909, 250],  # body
        [329, 260, 909, 320],
        [327, 400, 909, 510],
        [146, 600, 690, 620],  # wide exercise — must stay main
        [149, 640, 670, 660],
    ]
    layout = detect_columns(boxes)
    assert layout.zone_for_bbox([132, 200, 287, 270]) == "aside_left"
    assert layout.zone_for_bbox([329, 200, 909, 250]) == "main"
    assert layout.zone_for_bbox([146, 600, 690, 620]) == "main"


def test_short_right_margin_note():
    boxes = [
        [89, 100, 667, 200],
        [89, 220, 667, 400],
        [90, 420, 667, 600],
        [712, 300, 863, 328],  # ∞ note
    ]
    layout = detect_columns(boxes)
    assert layout.zone_for_bbox([712, 300, 863, 328]) == "aside_right"
    assert layout.zone_for_bbox([89, 100, 667, 200]) == "main"


def test_orphan_left_without_same_height_companion_stays_main():
    """Left strip alone at top (no main beside it) is not a protruding aside."""
    boxes = [
        [50, 40, 180, 90],  # orphan left
        [300, 400, 900, 520],
        [300, 540, 900, 700],
    ]
    layout = detect_columns(boxes)
    assert layout.column_count == 1
    assert layout.zone_for_bbox([50, 40, 180, 90]) == "main"


def test_aside_top_pct_maps_y0():
    from app.services.ir.column_detect import aside_gap_em, aside_top_pct

    assert aside_top_pct([100, 300, 200, 400], 100.0, 900.0) == 25.0
    assert aside_top_pct([100, 100, 200, 150], 100.0, 900.0) == 0.0
    assert aside_gap_em(300, 100) == pytest.approx(9.6)
    assert aside_gap_em(100, 100) == 0.0
