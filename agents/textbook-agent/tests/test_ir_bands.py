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
        "CONTAIN_FRAC": 0.05,
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
