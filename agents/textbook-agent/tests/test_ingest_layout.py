from __future__ import annotations

import json
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1] / "workspace" / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
import ir_lib  # noqa: E402
from ingest_layout import ingest_layout  # noqa: E402

ROOT_FILES = ("mineru/layout.json", "mineru/demo_model.json", "mineru/demo_content_list.json")


def _span(kind: str, content: str = "", image: str = "", score: float | None = None) -> dict:
    span = {"type": kind, "content": content}
    if image:
        span["image_path"] = f"images/{image}"
    if score is not None:
        span["score"] = score
    return span


def _block(typ: str, index: int, lines: list[list[dict]], bbox: list[float] | None = None,
           level: int | None = None, children: list[dict] | None = None) -> dict:
    block = {
        "type": typ,
        "index": index,
        "lines": [{"spans": spans} for spans in lines],
    }
    if bbox:
        block["bbox"] = bbox
    if level is not None:
        block["level"] = level
    if children is not None:
        block["blocks"] = children
    return block


def _book(tmp_path: Path, pages: list[dict]) -> Path:
    book = tmp_path / "irbook"
    mineru = book / "mineru"
    mineru.mkdir(parents=True)
    layout = {
        "pdf_info": pages,
        "_version_name": "9.9.9-test",
    }
    (mineru / "layout.json").write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
    (mineru / "demo_model.json").write_text("[]", encoding="utf-8")
    (mineru / "demo_content_list.json").write_text("[]", encoding="utf-8")
    (book / "meta.json").write_text(
        json.dumps({"id": "irbook", "status": "ready", "page_count": len(pages)}),
        encoding="utf-8",
    )
    return book


def test_page_ingest_full_shape(tmp_path: Path) -> None:
    page = {
        "page_idx": 0,
        "page_size": [600, 800],
        "para_blocks": [
            _block("text", 0, [[_span("text", "原子中的能级按能量排列", score=0.98)]],
                   bbox=[60, 100, 540, 140]),
            _block("title", 1, [[_span("text", "2. 电子云", score=0.99)]],
                   bbox=[60, 150, 200, 170], level=2),
            _block("interline_equation", 2, [[_span("interline_equation", image="eq1.jpg")]],
                   bbox=[100, 200, 500, 260]),
            _block("image", 3, [[_span("text", "", image="f1.jpg")]],
                   bbox=[60, 300, 300, 500],
                   children=[_block("image_caption", 0, [[_span("text", "图1　能级图")]])]),
        ],
        "discarded_blocks": [
            _block("page_header", 0, [[_span("text", "第1章")]]),
            _block("page_footnote", 9, [[_span("text", "① 页注原文", score=0.9)]]),
        ],
    }
    book = _book(tmp_path, [page])
    out = ir_lib.ingest_page(book, 1, box_prefixes=["聚焦"])
    assert out["page"] == 1 and out["page_size"] == [600, 800]
    assert out["source"]["mineru"] == "9.9.9-test"

    by_type = {b["type"]: b for b in out["blocks"]}
    text = by_type["text"]
    assert text["text"] == "原子中的能级按能量排列"
    assert text["score"] == 0.98
    assert text["width_frac"] == round(480 / 600, 3)

    title = by_type["title"]
    assert title["level"] == 2

    eq = by_type["interline_equation"]
    assert eq["asset"] == "eq1.jpg"
    assert "![](images/eq1.jpg)" in eq["text"]

    fig = by_type["image"]
    assert fig["caption"] == "图1　能级图"
    assert fig["kind"] == "figure"

    assert out["chrome"]["headers"] == ["第1章"]
    assert out["chrome"]["footnotes"] == [{"order": 9, "text": "① 页注原文"}]


def test_cjk_spans_join_without_spaces_in_ir() -> None:
    lines = [[_span("text", "处于"), _span("inline_equation", "2p"), _span("text", "轨道上")]]
    structured, text = ir_lib.render_lines([{"spans": spans} for spans in lines])
    assert text == "处于$2p$轨道上"
    assert structured[0][1] == {"t": "eq", "c": "2p"}


def test_inline_image_and_box_and_row(tmp_path: Path) -> None:
    page = {
        "page_idx": 0,
        "page_size": [600, 800],
        "para_blocks": [
            _block("text", 0, [[_span("text", "正文第一句")]], bbox=[60, 100, 540, 120]),
            _block("image", 1, [[_span("text", "", image="icon.jpg")]],
                   bbox=[60, 130, 90, 145]),  # 矮图 + 与文本相邻 → inline_image
            _block("image", 2, [[_span("text", "", image="a.jpg")]],
                   bbox=[60, 300, 180, 420]),
            _block("image", 3, [[_span("text", "", image="b.jpg")]],
                   bbox=[200, 300, 320, 420]),  # 与 a 同 y → 同行
            _block("title", 4, [[_span("text", "拓展延伸")]],
                   bbox=[60, 500, 130, 520], level=2),  # 小宽 title + 词表 → box
        ],
        "discarded_blocks": [],
    }
    book = _book(tmp_path, [page])
    out = ir_lib.ingest_page(book, 1, box_prefixes=["拓展延伸", "聚焦"])
    kinds = {b.get("asset") or b["text"]: b["kind"] for b in out["blocks"]}
    assert kinds["icon.jpg"] == "inline_image"
    assert kinds["a.jpg"] == "figure" and kinds["b.jpg"] == "figure"
    rows = {b.get("row") for b in out["blocks"] if b["type"] == "image" and b["kind"] == "figure"}
    assert rows == {0}
    box = [b for b in out["blocks"] if b["kind"] == "box"]
    assert len(box) == 1 and box[0]["box_kind"] == "拓展延伸"


def test_v1_caption_and_table_fallback(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [
                _block("image", 0, [[_span("text", "", image="x.jpg")]], bbox=[60, 100, 300, 300]),
                _block("table", 1, [[_span("text", "")]], bbox=[60, 400, 540, 600]),
            ],
            "discarded_blocks": [],
        }],
    )
    v1 = [
        {"type": "image", "page_idx": 0, "image_caption": ["图X　来自v1的题注"]},
        {"type": "table", "page_idx": 0, "table_body": "<table><tr><td>1</td></tr></table>"},
    ]
    (book / "mineru" / "demo_content_list.json").write_text(
        json.dumps(v1, ensure_ascii=False), encoding="utf-8"
    )
    out = ir_lib.ingest_page(book, 1, box_prefixes=[])
    fig = out["blocks"][0]
    assert fig["caption"] == "图X　来自v1的题注"
    assert out["blocks"][1]["table_html"] == "<table><tr><td>1</td></tr></table>"


def test_model_latex_join(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [
                _block("interline_equation", 0,
                       [[_span("interline_equation", content="")]],
                       bbox=[240, 400, 360, 440]),
            ],
            "discarded_blocks": [],
        }],
    )
    model = [[
        {"type": "equation", "bbox": [0.4, 0.5, 0.6, 0.55], "latex": "\\Delta E = h\\nu"},
        {"type": "equation", "bbox": [0.0, 0.0, 0.1, 0.1], "latex": "远处的"},
    ]]
    (book / "mineru" / "demo_model.json").write_text(
        json.dumps(model, ensure_ascii=False), encoding="utf-8"
    )
    out = ir_lib.ingest_page(book, 1, box_prefixes=[])
    assert out["blocks"][0]["latex"] == "\\Delta E = h\\nu"


def test_block_id_stable_and_remap(tmp_path: Path) -> None:
    page = {
        "page_idx": 0,
        "page_size": [600, 800],
        "para_blocks": [
            _block("text", 0, [[_span("text", "同一段正文")]], bbox=[60, 100, 540, 140]),
            _block("text", 1, [[_span("text", "另一段")]], bbox=[60, 200, 540, 240]),
        ],
        "discarded_blocks": [],
    }
    book = _book(tmp_path, [page])
    a = ir_lib.ingest_page(book, 1, box_prefixes=[])
    b = ir_lib.ingest_page(book, 1, box_prefixes=[])
    assert [x["id"] for x in a["blocks"]] == [x["id"] for x in b["blocks"]]

    # 块漂移（bbox 移动超过 10px 网格、文本没变）→ remap 指到新 id
    moved = json.loads(json.dumps(page))
    moved["para_blocks"][0]["bbox"] = [80, 120, 560, 160]
    book2 = tmp_path / "irbook2"
    (book2 / "mineru").mkdir(parents=True)
    (book2 / "mineru" / "layout.json").write_text(
        json.dumps({"pdf_info": [moved], "_version_name": "9.9.9-test"}, ensure_ascii=False),
        encoding="utf-8",
    )
    c = ir_lib.ingest_page(book2, 1, box_prefixes=[])
    old_pages = {1: [{"id": blk["id"], "type": blk["type"], "text": blk["text"],
                      "order": blk["order"]} for blk in a["blocks"]]}
    remap = ir_lib.compute_remap(old_pages, {1: c})
    new_ids = {blk["id"] for blk in c["blocks"]}
    assert remap[a["blocks"][0]["id"]] in new_ids
    assert remap[a["blocks"][0]["id"]] != a["blocks"][0]["id"]


def test_ingest_layout_tool_scan_then_fix(tmp_path: Path) -> None:
    page = {
        "page_idx": 0,
        "page_size": [600, 800],
        "para_blocks": [_block("text", 0, [[_span("text", "工具出稿")]], bbox=[60, 100, 540, 140])],
        "discarded_blocks": [_block("page_footnote", 5, [[_span("text", "① 注")]])],
    }
    _book(tmp_path, [page])
    scan = ingest_layout("irbook", root=str(tmp_path), apply="scan")
    assert scan["ok"] and scan["pages"] == 1 and scan["footnotes"] == 1
    assert not (tmp_path / "irbook" / "ir").exists()

    fix = ingest_layout("irbook", root=str(tmp_path), apply="fix")
    assert fix["ok"] and fix["remapped"] == 0
    written = tmp_path / "irbook" / "ir" / "pages" / "0001.json"
    data = json.loads(written.read_text(encoding="utf-8"))
    assert data["blocks"][0]["text"] == "工具出稿"
    # pages/ 与 mid/ 不被创建
    assert not (tmp_path / "irbook" / "pages").exists()
    assert not (tmp_path / "irbook" / "mid").exists()
    # 再跑一遍 → 幂等，无 remap
    again = ingest_layout("irbook", root=str(tmp_path), apply="fix")
    assert again["ok"] and again["remapped"] == 0
    assert json.loads(written.read_text(encoding="utf-8")) == data


def test_ingest_layout_reingest_writes_remap(tmp_path: Path) -> None:
    page = {
        "page_idx": 0,
        "page_size": [600, 800],
        "para_blocks": [_block("text", 0, [[_span("text", "原文不变")]], bbox=[60, 100, 540, 140])],
        "discarded_blocks": [],
    }
    book = _book(tmp_path, [page])
    ingest_layout("irbook", root=str(tmp_path), apply="fix")
    # 模拟 MinerU 重解析：文本相同、bbox 漂移 → 块 id 变化
    moved = json.loads(json.dumps(page))
    moved["para_blocks"][0]["bbox"] = [70, 110, 550, 150]
    (book / "mineru" / "layout.json").write_text(
        json.dumps({"pdf_info": [moved], "_version_name": "9.9.9-test"}, ensure_ascii=False),
        encoding="utf-8",
    )
    out = ingest_layout("irbook", root=str(tmp_path), apply="fix")
    assert out["remapped"] == 1
    remap = json.loads((book / "ir" / "remap.json").read_text(encoding="utf-8"))
    assert len(remap) == 1


def test_span_level_latex_join(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [
                _block("text", 0, [[
                    {"type": "text", "content": "处于", "bbox": [60, 100, 90, 120]},
                    {"type": "inline_equation", "content": "2p", "bbox": [90, 100, 150, 125]},
                    {"type": "text", "content": "轨道上", "bbox": [150, 100, 220, 120]},
                ]], bbox=[60, 100, 540, 140]),
            ],
            "discarded_blocks": [],
        }],
    )
    model = [[
        {"type": "inline_formula", "bbox": [0.15, 0.125, 0.25, 0.156],
         "latex": "2\\mathrm{p}"},
    ]]
    (book / "mineru" / "demo_model.json").write_text(
        json.dumps(model, ensure_ascii=False), encoding="utf-8"
    )
    out = ir_lib.ingest_page(book, 1, box_prefixes=[])
    spans = [s for row in out["blocks"][0]["lines"] for s in row]
    eq = [s for s in spans if s.get("t") == "eq"][0]
    assert eq["latex"] == "2\\mathrm{p}"


def test_render_page_md(tmp_path: Path) -> None:
    page = {
        "page_idx": 0,
        "page_size": [600, 800],
        "para_blocks": [
            _block("text", 0, [[_span("text", "处于"), _span("inline_equation", "2p"),
                                _span("text", "轨道上")]], bbox=[60, 100, 540, 140]),
            _block("title", 1, [[_span("text", "拓展延伸")]], bbox=[60, 150, 130, 170], level=2),
            _block("text", 2, [[_span("text", "栏目正文")]], bbox=[60, 180, 540, 220]),
            _block("image", 3, [[_span("text", "", image="a.jpg")]], bbox=[60, 300, 180, 420],
                   children=[_block("image_caption", 0, [[_span("text", "图A")]])]),
            _block("image", 4, [[_span("text", "", image="b.jpg")]], bbox=[200, 300, 320, 420]),
            _block("interline_equation", 5, [[_span("interline_equation", content="E=mc^2")]],
                   bbox=[100, 500, 500, 540]),
        ],
        "discarded_blocks": [
            _block("page_footnote", 9, [[_span("text", "① 页注")]]),
        ],
    }
    book = _book(tmp_path, [page])
    out = ir_lib.render_page_md(ir_lib.ingest_page(book, 1, box_prefixes=["拓展延伸"]))
    assert "处于$2p$轨道上" in out
    assert "**【拓展延伸】**" in out
    assert "![图A](images/a.jpg)" in out and "*图A*" in out
    assert "![插图](images/b.jpg)" in out  # 同行图连续输出
    assert "$$\nE=mc^2\n$$" in out
    assert out.rstrip().endswith("① 页注")
