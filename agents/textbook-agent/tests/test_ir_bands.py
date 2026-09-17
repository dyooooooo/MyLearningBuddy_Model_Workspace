from __future__ import annotations

import json
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1] / "workspace" / "tools"
_ROOT = Path(__file__).resolve().parents[3]
# 实验线原型已退役，当时手工核过的四页 v3 金样搬到了这里
_GOLDENS = Path(__file__).resolve().parent / "goldens" / "bands"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import ir_bands  # noqa: E402
from ir_lib import ingest_page  # noqa: E402

BOOKS = _ROOT / "data" / "agent-home" / "textbooks"
SPECS = [
    {
        "stem": "bio-0009",
        "book": "0d100cbe267f45df91551d55b851c41c",
        "page": 9,
        "page_idx": 8,
    },
    {
        "stem": "chem-0008",
        "book": "1efec97d2afa4b1195bc1268e352e1f7",
        "page": 8,
        "page_idx": 7,
    },
    {
        "stem": "chem-0013",
        "book": "1efec97d2afa4b1195bc1268e352e1f7",
        "page": 13,
        "page_idx": 12,
    },
    {
        "stem": "math-0011",
        "book": "66fdd0558d97406bac2b7840c9547165",
        "page": 11,
        "page_idx": 10,
    },
]
TRACK_TOL = 1.0


def _mineru_page(book_id: str, page_idx: int) -> dict:
    layout = json.loads((BOOKS / book_id / "mineru" / "layout.json").read_text())
    for page in layout["pdf_info"]:
        if int(page["page_idx"]) == page_idx:
            return page
    raise FileNotFoundError(f"{book_id} page_idx {page_idx}")


def _members(cell: dict) -> list[int]:
    out: list[int] = []
    for item in cell.get("members") or []:
        if item is None:
            continue
        idx = int(item)
        if idx not in out:
            out.append(idx)
    return out


def slot_map(doc: dict) -> dict[int, str]:
    out: dict[int, str] = {}
    for row in doc.get("rows") or []:
        for cell in row.get("cells") or []:
            slot = str(cell.get("slot") or "")
            if not slot or cell.get("blank"):
                continue
            for idx in _members(cell):
                out[idx] = slot
    return out


def track_delta(a: list | None, b: list | None) -> float | None:
    if not a or not b:
        return None if a == b else 999.0
    return max(abs(float(a[0]) - float(b[0])), abs(float(a[1]) - float(b[1])))


def test_four_page_goldens() -> None:
    gold_dir = _GOLDENS
    assert gold_dir.is_dir(), f"四页金样丢了：{gold_dir}"
    checked = 0
    for spec in SPECS:
        gold_path = gold_dir / f"{spec['stem']}.json"
        assert gold_path.is_file(), f"缺金样 {gold_path.name}"
        if not (BOOKS / spec["book"] / "mineru" / "layout.json").is_file():
            continue  # 这本书不在这台机器上，跳过而不是假绿
        checked += 1
        gold = json.loads(gold_path.read_text())
        page = _mineru_page(spec["book"], spec["page_idx"])
        algo = ir_bands.assemble_page(page, spec["book"], spec["page"])
        assert algo.get("format") == ir_bands.FORMAT
        at = algo.get("tracks") or {}
        gt = gold.get("tracks") or {}
        for name in ("frame", "main", "aside"):
            dlt = track_delta(at.get(name), gt.get(name))
            if dlt is None:
                continue
            assert dlt <= TRACK_TOL, f"{spec['stem']} {name} {dlt}"
        assert (at.get("aside_side") or None) == (gt.get("aside_side") or None)
        grows, arows = gold.get("rows") or [], algo.get("rows") or []
        assert len(grows) == len(arows), spec["stem"]
        for grow, arow in zip(grows, arows):
            assert grow.get("kind") == arow.get("kind")
        assert slot_map(algo) == slot_map(gold)
        a_types = [u.get("type") for u in algo.get("units") or []]
        g_types = [u.get("type") for u in gold.get("units") or []]
        assert a_types == g_types, spec["stem"]
    assert checked == len(SPECS), f"只比了 {checked}/{len(SPECS)} 页，缺书的 layout.json"


def test_folio_side_left_and_right() -> None:
    assert ir_bands.folio_side([[10, 800, 30, 820]], ["page_number"], 600) == "verso"
    assert ir_bands.folio_side([[520, 800, 540, 820]], ["page_number"], 600) == "recto"
    assert ir_bands.folio_side([[10, 800, 30, 820]], ["header"], 600) == ""


def test_split_marks_sub_and_plain() -> None:
    assert ir_bands.split_marks("2p<sub>x</sub>") == [
        {"t": "text", "c": "2p"},
        {"t": "sub", "c": "x"},
    ]
    assert ir_bands.split_marks("普通字") == [{"t": "text", "c": "普通字"}]


def test_ingest_writes_tree(tmp_path: Path) -> None:
    book = tmp_path / "bandbook"
    mineru = book / "mineru"
    mineru.mkdir(parents=True)
    page = {
        "page_idx": 0,
        "page_size": [600, 800],
        "para_blocks": [
            {
                "type": "text",
                "index": 0,
                "bbox": [80, 100, 520, 140],
                "lines": [{"spans": [{"type": "text", "content": "一段正文"}]}],
            }
        ],
        "discarded_blocks": [
            {
                "type": "page_number",
                "index": 1,
                "bbox": [520, 760, 540, 780],
                "lines": [{"spans": [{"type": "text", "content": "3"}]}],
            }
        ],
    }
    (mineru / "layout.json").write_text(
        json.dumps({"pdf_info": [page], "_version_name": "test"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (mineru / "demo_model.json").write_text("[]", encoding="utf-8")
    (mineru / "demo_content_list.json").write_text("[]", encoding="utf-8")
    out = ingest_page(book, 1)
    tree = out["layout"]["tree"]
    assert tree["kind"] == "rows"
    assert tree.get("children")
    assert "bands" not in out["layout"]
    assert "column_count" not in out["layout"]
    assert out["blocks"][0].get("zone") is None
    assert out["layout"].get("folio") == "recto"
    assert any(str(b.get("id") or "").startswith("kb-close-") for b in out["blocks"])
