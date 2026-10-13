from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

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
    {
        "stem": "math-0008",
        "book": "66fdd0558d97406bac2b7840c9547165",
        "page": 8,
        "page_idx": 7,
    },
    {
        "stem": "math-0012",
        "book": "66fdd0558d97406bac2b7840c9547165",
        "page": 12,
        "page_idx": 11,
    },
    {
        "stem": "math-0014",
        "book": "66fdd0558d97406bac2b7840c9547165",
        "page": 14,
        "page_idx": 13,
    },
    {
        "stem": "math-0015",
        "book": "66fdd0558d97406bac2b7840c9547165",
        "page": 15,
        "page_idx": 14,
    },
    {
        "stem": "math-0016",
        "book": "66fdd0558d97406bac2b7840c9547165",
        "page": 16,
        "page_idx": 15,
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
    assert gold_dir.is_dir(), f"金样目录丢了：{gold_dir}"
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
        assert "units" not in algo, f"{spec['stem']}：排版不再合并原始块，不该还有收口单元"
    assert checked == len(SPECS), f"只比了 {checked}/{len(SPECS)} 页，缺书的 layout.json"


def _sig_col(
    x0: float, x1: float, members: list | None = None, blank: bool = False
) -> dict:
    return {
        "members": members or [],
        "x0": x0,
        "x1": x1,
        "y0": 0.0,
        "y1": 10.0,
        "blank": blank,
    }


def test_text_blank_same_ratio_can_merge() -> None:
    """上行正文|空白、下行也是正文|空白，宽度差在 10% 页宽内，可以往下长。"""
    page_w = 595.0
    upper = [_sig_col(52, 400, [{"id": 1}]), _sig_col(400, 543, blank=True)]
    lower = [_sig_col(52, 410, [{"id": 2}]), _sig_col(410, 543, blank=True)]
    assert ir_bands._same_band_sig(upper, lower, page_w)
    merged = ir_bands._merge_aligned_bands(upper, lower, 100.0, 220.0)
    assert [c["blank"] for c in merged] == [False, True]
    assert [m["id"] for m in merged[0]["members"]] == [1, 2]
    assert merged[1]["members"] == []
    assert merged[0]["x1"] <= merged[1]["x0"] + ir_bands.SNAP


def test_merged_blank_yields_to_wider_text() -> None:
    """上行字窄、下行字宽，合并后右空白让开，两栏不能叠在一起。"""
    upper = [_sig_col(77, 279, [{"id": 1}]), _sig_col(279, 595, blank=True)]
    lower = [_sig_col(77, 320, [{"id": 2}]), _sig_col(320, 595, blank=True)]
    merged = ir_bands._merge_aligned_bands(upper, lower, 84.0, 121.0)
    assert not merged[0]["blank"] and merged[1]["blank"]
    assert merged[0]["x1"] == 320.0
    assert merged[1]["x0"] == 320.0


def test_blank_text_versus_text_blank_cannot_merge() -> None:
    """上行空|字、下行字|空，结构不同，不能往下长。"""
    page_w = 595.0
    upper = [_sig_col(52, 200, blank=True), _sig_col(200, 543, [{"id": 1}])]
    lower = [_sig_col(52, 400, [{"id": 2}]), _sig_col(400, 543, blank=True)]
    assert not ir_bands._same_band_sig(upper, lower, page_w)


def test_wide_plus_sliver_versus_half_cannot_merge() -> None:
    """上行很宽的字加一条窄空，下行各占一半，比例差超过 10% 页宽，不能并。"""
    page_w = 595.0
    upper = [_sig_col(0, 506, [{"id": 1}]), _sig_col(506, 595, blank=True)]
    lower = [_sig_col(0, 297, [{"id": 2}]), _sig_col(297, 595, blank=True)]
    assert not ir_bands._same_band_sig(upper, lower, page_w)


def test_pad_to_page_keeps_narrow_gap_as_blank() -> None:
    """栏缝再窄也不并进字栏，空白一直留在大块里。"""
    cols = [
        {"members": [{"id": 1, "bbox": [52, 0, 400, 10]}], "x0": 52.0, "x1": 400.0},
        {"members": [{"id": 2, "bbox": [408, 0, 500, 10]}], "x0": 408.0, "x1": 500.0},
    ]
    out = ir_bands._pad_to_page(cols, [0.0, 595.0], 0.0, 10.0)
    blanks = [c for c in out if c.get("blank")]
    contents = [c for c in out if not c.get("blank")]
    assert contents[0]["x1"] == 400.0
    assert any(abs(c["x0"] - 400.0) <= 0.1 and abs(c["x1"] - 408.0) <= 0.1 for c in blanks)
    assert out[-1].get("blank") and out[-1]["x1"] == 595.0


def test_grow_text_region_keeps_mismatched_rows_apart() -> None:
    """标题行是字|空，下一行是字|问号框，不能并成一块，问号不会跑到标题旁边。"""
    title = {
        "id": 1,
        "type": "title",
        "bbox": [52.0, 100.0, 250.0, 130.0],
        "text": "1.1 标题",
    }
    body = {
        "id": 2,
        "type": "text",
        "bbox": [52.0, 140.0, 400.0, 220.0],
        "text": "一段正文",
    }
    qbox = {
        "id": 3,
        "type": "text",
        "bbox": [430.0, 140.0, 520.0, 200.0],
        "text": "？",
    }
    with ir_bands.band_rules_applied():
        grown = ir_bands._grow_text_region(
            [title, body, qbox], 80.0, 300.0, [0.0, 595.0], [52.0, 540.0], 595.0
        )
    content = [sl for sl in grown if not sl.get("gap_blank")]
    assert len(content) >= 2
    q_rows = [
        sl
        for sl in content
        if any(m.get("id") == 3 for col in sl["cols"] for m in (col.get("members") or []))
    ]
    title_rows = [
        sl
        for sl in content
        if any(m.get("id") == 1 for col in sl["cols"] for m in (col.get("members") or []))
    ]
    assert q_rows and title_rows
    assert q_rows[0] is not title_rows[0]


def test_body_cell_keeps_the_blank_it_grew_into() -> None:
    """格子为铺满版面长高之后，多出来的空白要留成空白段，不能凭空没掉。

    以前 nest_body 只按成员出段，格子比字高多少都看不出来，排出来的页面
    上下挤在一起，跟原书对不上。
    """
    cell = {"slot": "main", "bbox": [52.0, 80.0, 400.0, 300.0], "blank": False, "members": [0]}
    member = {
        "id": 0,
        "src_i": 0,
        "type": "text",
        "bbox": [52.0, 140.0, 400.0, 170.0],
        "text": "一段正文",
    }
    parts = ir_bands.nest_body(cell, [member], [], 12.0).get("parts") or []
    roles = [p.get("role") for p in parts]
    assert roles == ["blank", "text", "blank"], roles
    assert parts[0]["bbox"][1] == 80.0 and parts[0]["bbox"][3] == 140.0
    assert parts[2]["bbox"][1] == 170.0 and parts[2]["bbox"][3] == 300.0


def test_prose_is_not_swallowed_as_a_figure_caption() -> None:
    """整句正文不是题注。图只吃矮、窄、压在图宽里、又不成句的那几行。"""
    fig = {"bbox": [100.0, 100.0, 300.0, 200.0]}
    caption = {
        "id": 1,
        "type": "text",
        "bbox": [110.0, 202.0, 290.0, 218.0],
        "text": "图1.2-3 函数的图象",
    }
    prose = {
        "id": 2,
        "type": "text",
        "bbox": [110.0, 202.0, 290.0, 218.0],
        "text": "由图可知，函数在该区间上单调递增。",
    }
    wide = {
        "id": 3,
        "type": "text",
        "bbox": [52.0, 202.0, 543.0, 218.0],
        "text": "旁边一整行",
    }
    assert ir_bands._is_caption_like(caption)
    assert not ir_bands._is_caption_like(prose)
    assert ir_bands._caption_fits_figure(fig["bbox"], caption)
    assert not ir_bands._caption_fits_figure(fig["bbox"], wide)


def test_no_rule_key_is_a_frozen_default() -> None:
    """阈值不许写成默认参数。

    默认参数在 import 时求值就冻死了，apply_band_rules 改全局改不动它，
    于是 band_rules.yaml 里的旋钮变成死旋钮——调了没反应，还得去翻 Python。
    PAIR_GAP / FIG_NEAR_X 就是这么整条失效的，COL_GAP / TIGHT_PACK 半失效。
    """
    import ast

    src = Path(ir_bands.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    keys = set(ir_bands._RULE_KEYS)
    frozen = [
        f"{fn.name}(... = {d.id})  line {fn.lineno}"
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        for d in list(fn.args.defaults) + [k for k in fn.args.kw_defaults if k]
        if isinstance(d, ast.Name) and d.id in keys
    ]
    assert not frozen, "阈值被冻进默认参数，band_rules.yaml 改不动：\n  " + "\n  ".join(frozen)


def test_no_rule_key_is_dead() -> None:
    """每个阈值都得有至少一处真读它的地方。

    光在 YAML 和 _RULE_KEYS 里列着不算生效。PAIR_GAP / FIG_NEAR_X 曾经全库
    只有默认参数那一处引用，等于写了个没接线的旋钮。
    """
    import ast

    tree = ast.parse(Path(ir_bands.__file__).read_text(encoding="utf-8"))
    frozen_defaults = {
        id(d)
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        for d in list(fn.args.defaults) + [k for k in fn.args.kw_defaults if k]
    }
    live: dict[str, int] = {k: 0 for k in ir_bands._RULE_KEYS}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id in live
            and isinstance(node.ctx, ast.Load)
            and id(node) not in frozen_defaults
        ):
            live[node.id] += 1
    dead = sorted(k for k, n in live.items() if n == 0)
    assert not dead, f"这些阈值没有任何生效路径，调了不会有反应：{dead}"


def test_previously_frozen_keys_now_take_effect() -> None:
    """五个曾被冻在默认参数里的阈值，现在必须真能改变产物。

    只要有任意一页能被它改动就算接上了（阈值本身是分场景的）。
    """
    # FIG_NEAR_X 管「相邻两张图算不算同一图段」，四页金样里没有多图页，另补一页
    probe_specs = SPECS + [
        {"stem": "multifig", "book": "13036f17d454435888c0f499e9773b82", "page": 23, "page_idx": 22}
    ]
    pages = []
    for spec in probe_specs:
        if (BOOKS / spec["book"] / "mineru" / "layout.json").is_file():
            pages.append((spec, _mineru_page(spec["book"], spec["page_idx"])))
    assert pages, "四页原料都不在这台机器上"

    def sig(page: dict, spec: dict, rules: dict | None) -> str:
        page = copy.deepcopy(page)
        assembled = ir_bands.assemble_page(page, spec["book"], spec["page"], rules=rules)
        blocks = [
            {"id": f"b{i}", "type": "text", "text": f"t{i}", "bbox": list(b.get("bbox") or [])}
            for i, b in enumerate(page.get("para_blocks") or [])
        ]
        layout = ir_bands.build_layout(assembled, blocks)
        return json.dumps([assembled, layout], sort_keys=True, ensure_ascii=False, default=str)

    probes = {
        "COL_GAP": 400.0,
        "TIGHT_PACK": 300.0,
        "PAIR_GAP": 0.0,
        "FIG_NEAR_X": 9999.0,
        "CONTAIN_FRAC": 0.99,
    }
    for key, val in probes.items():
        moved = any(
            sig(page, spec, {key: val}) != sig(page, spec, None) for spec, page in pages
        )
        assert moved, f"{key} 改了四页都没反应，可能又被冻住或没接线"


def test_explicit_rules_do_not_leak_to_later_pages() -> None:
    """显式传 rules 只能管这一次调用。

    阈值落在模块全局上，以前装进去就不还原：--explain 调一次极端值，同进程后面
    每一页都跟着用那个值，排障看到的版式跟线上不是一回事。
    """
    spec = SPECS[3]
    if not (BOOKS / spec["book"] / "mineru" / "layout.json").is_file():
        pytest.skip("原料不在这台机器上")
    page = _mineru_page(spec["book"], spec["page_idx"])

    def run(rules: dict | None) -> str:
        out = ir_bands.assemble_page(copy.deepcopy(page), spec["book"], spec["page"], rules=rules)
        return json.dumps(out, sort_keys=True, ensure_ascii=False, default=str)

    base = run(None)
    tuned = run({"COL_GAP": 400.0})
    assert tuned != base, "探针值没生效，这条测不出泄漏"
    assert run(None) == base, "上一次的 rules 漏到了后面的页"
    assert ir_bands.COL_GAP == ir_bands.load_band_rules().get("COL_GAP", ir_bands.COL_GAP)


def test_concurrent_pages_do_not_cross_talk() -> None:
    """两个线程同时排版，一个用探针阈值，另一个必须拿到 YAML 的值。

    阈值放在模块全局，没有锁的话一个线程排到一半会被另一个线程改掉阈值。
    实测去锁时 300 次里有 88 次串扰（基线线程拿到探针值最多），加锁后 0 次。
    switchinterval 压到 1e-6 是为了把这个窗口放大到稳定可复现。
    """
    import threading

    spec = SPECS[3]
    if not (BOOKS / spec["book"] / "mineru" / "layout.json").is_file():
        pytest.skip("原料不在这台机器上")
    page = _mineru_page(spec["book"], spec["page_idx"])

    def run(rules: dict | None) -> str:
        out = ir_bands.assemble_page(copy.deepcopy(page), spec["book"], spec["page"], rules=rules)
        return json.dumps(out, sort_keys=True, ensure_ascii=False, default=str)

    base = run(None)
    assert run({"COL_GAP": 400.0}) != base, "探针值不改变版式，这条测不出串扰"
    bad: list[str] = []

    def worker(rules: dict | None, want_same: bool) -> None:
        for _ in range(150):
            same = run(rules) == base
            if same is not want_same:
                bad.append(f"rules={rules} 结果与基线相同={same}（期望{want_same}）")

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [
            threading.Thread(target=worker, args=({"COL_GAP": 400.0}, False)),
            threading.Thread(target=worker, args=(None, True)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=300)
    finally:
        sys.setswitchinterval(old)
    assert not bad, f"并发下阈值互串 {len(bad)} 次：{bad[:3]}"


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


def test_ingest_writes_slots_not_tree(tmp_path: Path) -> None:
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
    assert "layout" not in out
    assert out.get("folio") == "recto"
    assert out["source"]["schema"] == 9
    blk = out["blocks"][0]
    assert blk.get("zone") is None
    assert blk.get("slot") in {"main", "span"}
    assert blk.get("align") is None
    assert blk.get("indent") is None
    assert blk.get("indented") is None
    assert not any(str(b.get("id") or "").startswith("kb-close-") for b in out["blocks"])


def test_attach_slots_aside_main_span() -> None:
    assembled = {
        "tracks": {
            "frame": [0.0, 595.0],
            "main": [52.0, 397.0],
            "aside": [423.0, 535.0],
            "aside_side": "right",
        }
    }
    blocks = [
        {
            "id": "q",
            "type": "text",
            "kind": "body",
            "bbox": [423.0, 491.0, 514.0, 516.0],
            "text": "此处为什么有两种情况？",
        },
        {
            "id": "body",
            "type": "text",
            "kind": "body",
            "bbox": [52.0, 84.0, 396.0, 118.0],
            "text": "正文",
        },
        {
            "id": "wide",
            "type": "text",
            "kind": "body",
            "bbox": [10.0, 200.0, 580.0, 230.0],
            "text": "通栏",
        },
    ]
    ir_bands.attach_slots(assembled, blocks)
    assert blocks[0]["slot"] == "aside"
    assert blocks[1]["slot"] == "main"
    assert blocks[2]["slot"] == "span"


def test_math_page14_question_is_aside() -> None:
    book_id = "66fdd0558d97406bac2b7840c9547165"
    book = BOOKS / book_id
    if not (book / "mineru" / "layout.json").is_file():
        pytest.skip("原料不在这台机器上")
    out = ingest_page(book, 14)
    assert "layout" not in out
    assert out["source"]["schema"] == 9
    hit = next(
        (
            blk
            for blk in out["blocks"]
            if "此处为什么有两种情况" in str(blk.get("text") or "")
        ),
        None,
    )
    assert hit is not None
    assert hit["type"] == "text"
    assert hit["kind"] == "body"
    assert hit["slot"] == "aside"


def test_review_skips_tree_reasons() -> None:
    from ir_review import _page_hits

    page = {
        "page": 14,
        "folio": "recto",
        "page_size": [595, 841],
        "chrome": {"page_numbers": [{"text": "9", "bbox": [520, 800, 540, 820]}]},
        "blocks": [
            {"id": "f1", "type": "image", "kind": "figure", "text": "", "slot": "main"},
            {"id": "f2", "type": "image", "kind": "figure", "text": "", "slot": "main"},
            {"id": "f3", "type": "image", "kind": "figure", "text": "", "slot": "aside"},
            {
                "id": "q",
                "type": "text",
                "kind": "body",
                "slot": "aside",
                "text": "此处为什么有两种情况？",
            },
        ],
    }
    hit = _page_hits(page)
    assert hit is not None
    assert "caption_loose" in hit["reasons"]
    assert "too_many_bands" not in hit["reasons"]
    assert "tree_depth" not in hit["reasons"]
    assert "track_missing" not in hit["reasons"]
    assert hit["aside_blocks"] == 2
    assert hit.get("folio") == "recto"
