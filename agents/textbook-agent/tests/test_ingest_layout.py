from __future__ import annotations

import json
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1] / "workspace" / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
import ir_bands  # noqa: E402
import ir_lib  # noqa: E402
from app.workspace import ensure_workspace  # noqa: E402
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
                   children=[_block("image_caption", 0, [[_span("text", "图1　能级图")]],
                                   bbox=[80, 505, 220, 522])]),
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
    assert out["source"]["schema"] == ir_lib.IR_SCHEMA

    # 收口块（kb-close-*）是派生的合并行，按源块取
    by_type = {b["type"]: b for b in out["blocks"] if not ir_lib.is_close_block(b)}
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
    assert fig["captions"] == [{
        "type": "image_caption",
        "text": "图1　能级图",
        "bbox": [80.0, 505.0, 220.0, 522.0],
    }]
    assert fig["kind"] == "figure"

    assert out["chrome"]["headers"] == ["第1章"]
    assert out["chrome"]["footnotes"] == [{"order": 9, "text": "① 页注原文"}]


def test_child_footnote_and_two_captions_keep_boxes(tmp_path: Path) -> None:
    page = {
        "page_idx": 0,
        "page_size": [595, 841],
        "para_blocks": [
            _block(
                "image", 0, [[_span("text", "", image="cantor.jpg")]],
                bbox=[95, 253, 155, 346],
                children=[
                    _block("image_body", 0, [[_span("text", "")]], bbox=[95, 253, 155, 346]),
                    _block("image_footnote", 1, [[_span("text", "康托脚注")]],
                           bbox=[67, 349, 183, 397]),
                ],
            ),
            _block(
                "image", 1, [[_span("text", "", image="pair.jpg")]],
                bbox=[247, 476, 351, 507],
                children=[
                    _block("image_caption", 0, [[_span("text", "(a,b)")]],
                           bbox=[282, 513, 315, 526]),
                    _block("image_caption", 1, [[_span("text", "图1-1-1")]],
                           bbox=[199, 531, 241, 544]),
                ],
            ),
        ],
        "discarded_blocks": [],
    }
    out = ir_lib.ingest_page(_book(tmp_path, [page]), 1, box_prefixes=[])
    by_asset = {b["asset"]: b for b in out["blocks"] if b.get("asset")}
    cantor = by_asset["cantor.jpg"]
    assert cantor["caption"] == "康托脚注"
    assert cantor["captions"] == [{
        "type": "image_footnote",
        "text": "康托脚注",
        "bbox": [67.0, 349.0, 183.0, 397.0],
    }]
    pair = by_asset["pair.jpg"]
    assert pair["caption"] == "(a,b)\n图1-1-1"
    assert [c["text"] for c in pair["captions"]] == ["(a,b)", "图1-1-1"]
    assert pair["captions"][1]["bbox"] == [199.0, 531.0, 241.0, 544.0]


def test_cjk_spans_join_without_spaces_in_ir() -> None:
    lines = [[_span("text", "处于"), _span("inline_equation", "2p"), _span("text", "轨道上")]]
    structured, text = ir_lib.render_lines([{"spans": spans} for spans in lines])
    assert text == "处于$2p$轨道上"
    assert structured[0]["spans"][1]["t"] == "eq"
    assert structured[0]["spans"][1]["c"] == "2p"


def test_render_lines_keeps_line_and_span_bbox() -> None:
    structured, text = ir_lib.render_lines([{
        "bbox": [10, 20, 80, 36],
        "spans": [{
            "type": "text",
            "content": "主编",
            "bbox": [12, 20, 60, 36],
            "cross_page": True,
        }],
    }])
    assert text == "主编"
    assert structured[0]["bbox"] == [10.0, 20.0, 80.0, 36.0]
    assert structured[0]["spans"][0]["bbox"] == [12.0, 20.0, 60.0, 36.0]
    assert structured[0]["spans"][0]["cross_page"] is True


def test_ingest_moves_cross_page_line_to_next_page(tmp_path: Path) -> None:
    """MinerU 把下一页的字拼进上一页：摄取时字和框都回到下一页空块。"""
    keep = "那是1961年7月的一天，采用常规法培育"
    moved = "出来的早稻常规品种正在勾头散籽，激动得几乎要喊出声来！"
    next_box = [63.0, 320.0, 441.0, 399.0]
    prev_box = [153.0, 667.0, 530.0, 704.0]
    page1 = {
        "page_idx": 0,
        "page_size": [595, 841],
        "para_blocks": [{
            "type": "text",
            "index": 0,
            "bbox": prev_box,
            "lines": [
                {
                    "bbox": prev_box,
                    "spans": [{"type": "text", "content": keep, "bbox": prev_box}],
                },
                {
                    "bbox": next_box,
                    "spans": [{
                        "type": "text",
                        "content": moved,
                        "bbox": next_box,
                        "cross_page": True,
                    }],
                },
            ],
        }],
        "discarded_blocks": [],
    }
    page2 = {
        "page_idx": 1,
        "page_size": [595, 841],
        "para_blocks": [
            {
                "type": "text",
                "index": 0,
                "bbox": next_box,
                "lines": [],
                "lines_deleted": True,
            },
            _block("text", 1, [[_span("text", "这是一株奇特的稻禾")]],
                   bbox=[63.0, 404.0, 441.0, 525.0]),
        ],
        "discarded_blocks": [],
    }
    book = _book(tmp_path, [page1, page2])
    a = ir_lib.ingest_page(book, 1, box_prefixes=[])
    b = ir_lib.ingest_page(book, 2, box_prefixes=[])
    last = a["blocks"][-1]
    assert last["text"] == keep
    assert last["bbox"] == prev_box
    assert last.get("cross_page") is None
    assert moved not in last["text"]
    assert all(
        not (span.get("bbox") == next_box)
        for row in last.get("lines") or []
        for span in (row.get("spans") or [])
    )
    filled = b["blocks"][0]
    assert filled["text"] == moved
    assert filled["bbox"] == next_box
    assert filled.get("lines_deleted") is None
    assert filled.get("cross_page") is None
    assert "这是一株奇特的稻禾" in b["blocks"][1]["text"]


def test_line_sticking_out_of_block_stays_on_this_page(tmp_path: Path) -> None:
    """行框比块框大出一圈、没有 cross_page 标记：字留在本页，不贴到下一页。"""
    page1 = {
        "page_idx": 0,
        "page_size": [595, 841],
        "para_blocks": [{
            "type": "title",
            "index": 0,
            "bbox": [389, 84, 523, 140],
            "lines": [{
                "bbox": [380, 78, 531, 146],
                "spans": [{
                    "type": "text",
                    "content": "第 1 章",
                    "bbox": [380, 78, 531, 146],
                }],
            }],
        }],
        "discarded_blocks": [],
    }
    page2 = {
        "page_idx": 1,
        "page_size": [595, 841],
        "para_blocks": [
            _block("title", 0, [[_span("text", "物质的分类")]],
                   bbox=[79.0, 121.0, 262.0, 197.0]),
        ],
        "discarded_blocks": [],
    }
    book = _book(tmp_path, [page1, page2])
    a = ir_lib.ingest_page(book, 1, box_prefixes=[])
    b = ir_lib.ingest_page(book, 2, box_prefixes=[])
    assert a["blocks"][0]["text"] == "第 1 章"
    assert all("第 1 章" not in str(blk.get("text") or "") for blk in b["blocks"])


def test_ingest_creates_block_when_next_page_has_no_shell(tmp_path: Path) -> None:
    keep = "本页最后一行"
    moved = "下一页开头那截"
    next_box = [60.0, 58.0, 440.0, 95.0]
    prev_box = [150.0, 680.0, 530.0, 710.0]
    page1 = {
        "page_idx": 0,
        "page_size": [595, 841],
        "para_blocks": [{
            "type": "text",
            "index": 0,
            "bbox": prev_box,
            "lines": [
                {
                    "bbox": prev_box,
                    "spans": [{"type": "text", "content": keep, "bbox": prev_box}],
                },
                {
                    "bbox": next_box,
                    "spans": [{
                        "type": "text",
                        "content": moved,
                        "bbox": next_box,
                        "cross_page": True,
                    }],
                },
            ],
        }],
        "discarded_blocks": [],
    }
    page2 = {
        "page_idx": 1,
        "page_size": [595, 841],
        "para_blocks": [
            _block("text", 0, [[_span("text", "另一段正文")]],
                   bbox=[60.0, 100.0, 440.0, 160.0]),
        ],
        "discarded_blocks": [],
    }
    book = _book(tmp_path, [page1, page2])
    a = ir_lib.ingest_page(book, 1, box_prefixes=[])
    b = ir_lib.ingest_page(book, 2, box_prefixes=[])
    assert a["blocks"][0]["text"] == keep
    extras = [blk for blk in b["blocks"] if moved in str(blk.get("text") or "")]
    assert len(extras) == 1
    assert extras[0]["bbox"] == next_box


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


def test_numbered_exercise_title_is_not_box(tmp_path: Path) -> None:
    """「习题3.1」只是栏目名加编号，不能因为以「习题」开头就标成侧栏。"""
    page = {
        "page_idx": 0,
        "page_size": [595, 841],
        "para_blocks": [
            _block("title", 0, [[_span("text", "习题3.1")]],
                   bbox=[113, 384, 177, 403], level=2),
            _block("title", 1, [[_span("text", "思考与讨论：")]],
                   bbox=[60, 500, 160, 520], level=2),
            _block("title", 2, [[_span("text", "拓展延伸")]],
                   bbox=[60, 600, 130, 620], level=2),
        ],
        "discarded_blocks": [],
    }
    book = _book(tmp_path, [page])
    out = ir_lib.ingest_page(
        book, 1, box_prefixes=["习题", "思考与讨论", "拓展延伸"],
    )
    by_text = {b["text"]: b for b in out["blocks"]}
    assert by_text["习题3.1"]["kind"] != "box"
    assert by_text["习题3.1"]["type"] == "title"
    assert by_text["思考与讨论："]["kind"] == "box"
    assert by_text["思考与讨论："]["box_kind"] == "思考与讨论"
    assert by_text["拓展延伸"]["kind"] == "box"


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
        {"type": "image", "page_idx": 0, "image_caption": ["图X　来自v1的题注"],
         "bbox": [60, 100, 300, 300]},
        {"type": "table", "page_idx": 0, "table_body": "<table><tr><td>1</td></tr></table>",
         "bbox": [60, 400, 540, 600]},
    ]
    (book / "mineru" / "demo_content_list.json").write_text(
        json.dumps(v1, ensure_ascii=False), encoding="utf-8"
    )
    out = ir_lib.ingest_page(book, 1, box_prefixes=[])
    fig = out["blocks"][0]
    assert fig["caption"] == "图X　来自v1的题注"
    assert "captions" not in fig
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
    assert fix["ok"] and fix["remapped"] == 0 and fix["cleared"] == 0
    written = tmp_path / "irbook" / "ir" / "pages" / "0001.json"
    data = json.loads(written.read_text(encoding="utf-8"))
    assert data["blocks"][0]["text"] == "工具出稿"
    assert not (tmp_path / "irbook" / "ir" / "fonts.json").exists()
    # pages/ 与 mid/ 不被创建
    assert not (tmp_path / "irbook" / "pages").exists()
    assert not (tmp_path / "irbook" / "mid").exists()
    # 再跑一遍 → 幂等，无 remap
    again = ingest_layout("irbook", root=str(tmp_path), apply="fix")
    assert again["ok"] and again["remapped"] == 0
    assert json.loads(written.read_text(encoding="utf-8")) == data


def test_ingest_layout_fix_clears_leftover_pages_keeps_notes(tmp_path: Path) -> None:
    """apply=fix 先删 ir/pages 再写；残页清掉，注解 ir/notes 留下。"""
    _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [_block("text", 0, [[_span("text", "正本")]], bbox=[60, 100, 540, 140])],
            "discarded_blocks": [],
        }],
    )
    first = ingest_layout("irbook", root=str(tmp_path), apply="fix")
    assert first["ok"] and first["cleared"] == 0
    pages = tmp_path / "irbook" / "ir" / "pages"
    leftover = pages / "0099.json"
    leftover.write_text('{"page": 99, "stale": true}\n', encoding="utf-8")
    notes = tmp_path / "irbook" / "ir" / "notes"
    notes.mkdir(parents=True)
    note = notes / "0001.yaml"
    note.write_text("- id: n1\n  op: delete\n  block: kb-keep\n", encoding="utf-8")

    again = ingest_layout("irbook", root=str(tmp_path), apply="fix")
    assert again["ok"] and again["cleared"] == 2
    assert not leftover.exists()
    assert (pages / "0001.json").is_file()
    assert json.loads((pages / "0001.json").read_text(encoding="utf-8"))["blocks"][0]["text"] == "正本"
    assert note.read_text(encoding="utf-8") == "- id: n1\n  op: delete\n  block: kb-keep\n"


def test_ingest_layout_single_page_does_not_clear_others(tmp_path: Path) -> None:
    pages_data = [
        {
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [_block("text", 0, [[_span("text", "第一页")]], bbox=[60, 100, 540, 140])],
            "discarded_blocks": [],
        },
        {
            "page_idx": 1,
            "page_size": [600, 800],
            "para_blocks": [_block("text", 0, [[_span("text", "第二页")]], bbox=[60, 100, 540, 140])],
            "discarded_blocks": [],
        },
    ]
    _book(tmp_path, pages_data)
    ingest_layout("irbook", root=str(tmp_path), apply="fix")
    pages = tmp_path / "irbook" / "ir" / "pages"
    page2 = json.loads((pages / "0002.json").read_text(encoding="utf-8"))
    out = ingest_layout("irbook", page_no=1, root=str(tmp_path), apply="fix")
    assert out["ok"] and out["pages"] == 1 and out["cleared"] == 1
    assert json.loads((pages / "0002.json").read_text(encoding="utf-8")) == page2
    assert json.loads((pages / "0001.json").read_text(encoding="utf-8"))["blocks"][0]["text"] == "第一页"


def test_ingest_layout_scan_does_not_clear_pages(tmp_path: Path) -> None:
    _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [_block("text", 0, [[_span("text", "只扫")]], bbox=[60, 100, 540, 140])],
            "discarded_blocks": [],
        }],
    )
    ingest_layout("irbook", root=str(tmp_path), apply="fix")
    pages = tmp_path / "irbook" / "ir" / "pages"
    leftover = pages / "0099.json"
    leftover.write_text('{"page": 99}\n', encoding="utf-8")
    scan = ingest_layout("irbook", root=str(tmp_path), apply="scan")
    assert scan["ok"] and scan.get("cleared", 0) == 0
    assert leftover.exists()


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
    spans = ir_lib.ir_spans(out["blocks"][0]["lines"])
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
    assert "kb-ir-figrow" in out
    assert "images/a.jpg" in out and "图A" in out
    assert "images/b.jpg" in out
    assert "width:" in out
    assert "kb-ir-eq" in out
    assert "$$\nE=mc^2\n$$" in out
    assert out.rstrip().endswith("① 页注")


def _toc_book(tmp_path: Path) -> Path:
    toc_text = "绪言 …… 1\n\n第1章 化学研究的天地 …… 5\n1.1 物质的分类 …… 7\n1.1.1 交叉分类法 …… 8"
    pages = [
        {"page_idx": 0, "page_size": [600, 800],
         "para_blocks": [_block("text", 0, [[_span("text", "封面")]])],
         "discarded_blocks": []},
        {"page_idx": 1, "page_size": [600, 800],
         "para_blocks": [_block("title", 0, [[_span("text", "目 录")]], level=1),
                         _block("text", 1, [[_span("text", toc_text)]])],
         "discarded_blocks": [_block("page_number", 9, [[_span("text", "iii")]])]},
        {"page_idx": 2, "page_size": [600, 800],
         "para_blocks": [_block("text", 0, [[_span("text", "绪言正文")]])],
         "discarded_blocks": [_block("page_number", 9, [[_span("text", "1")]])]},
        {"page_idx": 3, "page_size": [600, 800],
         "para_blocks": [_block("title", 0, [[_span("text", "第1章 化学研究的天地")]], level=1),
                         _block("text", 1, [[_span("text", "正文")]])],
         "discarded_blocks": [_block("page_number", 9, [[_span("text", "5")]])]},
    ]
    return _book(tmp_path, pages)


def test_ir_toc_parse_and_page_map(tmp_path: Path) -> None:
    book = _toc_book(tmp_path)
    from ir_toc import build_toc

    toc = build_toc(book)
    assert toc["toc_pages"] == [2]
    titles = [(e["level"], e["title"], e["pdf_page"]) for e in toc["entries"]]
    assert titles == [(1, "第1章 化学研究的天地", 4)]
    assert toc["entries"][0]["label"] == "第1章 化学研究的天地"


def test_ir_toc_slash_and_chapter_glue(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [
            {"page_idx": 0, "page_size": [600, 800], "para_blocks": [_block("text", 0, [[_span("text", "封面")]])],
             "discarded_blocks": []},
            {"page_idx": 1, "page_size": [600, 800],
             "para_blocks": [_block("title", 0, [[_span("text", "目录")]])],
             "discarded_blocks": [_block("page_number", 9, [[_span("text", "iv")]])]},
            {"page_idx": 2, "page_size": [600, 800],
             "para_blocks": [_block("text", 0, [[_span("text", "绪言")]])],
             "discarded_blocks": [_block("page_number", 9, [[_span("text", "1")]])]},
        ],
    )
    ir_dir = book / "ir" / "pages"
    ir_dir.mkdir(parents=True)
    (ir_dir / "0002.json").write_text(json.dumps({
        "page": 2,
        "blocks": [
            {"type": "title", "kind": "body", "text": "目录"},
            {"type": "title", "kind": "body", "text": "第 章 1"},
            {"type": "text", "kind": "body", "text": "第 1 节 获得纯种微生物是发酵工程的基础 / 2"},
        ],
        "chrome": {"page_numbers": ["iv"]},
    }, ensure_ascii=False), encoding="utf-8")
    (ir_dir / "0003.json").write_text(json.dumps({
        "page": 3,
        "blocks": [
            {"type": "title", "kind": "body", "level": 1, "text": "第1章 发酵工程"},
            {"type": "title", "kind": "body", "level": 2, "text": "第 1 节 获得纯种微生物是发酵工程的基础"},
        ],
        "chrome": {"page_numbers": [{"text": "1"}]},
    }, ensure_ascii=False), encoding="utf-8")
    from ir_toc import build_toc, ir_toc

    toc = build_toc(book)
    titles = [(e["level"], e["title"], e["print_page"], e["pdf_page"]) for e in toc["entries"]]
    assert titles == [
        (1, "第1章 发酵工程", "1", 3),
        (2, "第 1 节 获得纯种微生物是发酵工程的基础", "1", 3),
    ]
    assert all(e.get("label") == e["title"] for e in toc["entries"])
    assert (book / "toc.json").exists() is False
    ir_toc("irbook", root=str(tmp_path), apply="fix")
    assert (book / "ir" / "toc.json").is_file()
    assert not (book / "toc.json").exists()


def test_ir_toc_tool_scan_and_fix(tmp_path: Path) -> None:
    _toc_book(tmp_path)
    from ir_toc import ir_toc

    scan = ir_toc("irbook", root=str(tmp_path), apply="scan")
    assert scan["ok"] and scan["entries"] == 1 and scan["reference"] == "missing"
    assert not (tmp_path / "irbook" / "ir" / "toc.json").exists()

    fix = ir_toc("irbook", root=str(tmp_path), apply="fix")
    assert fix["entries"] == 1
    data = json.loads((tmp_path / "irbook" / "ir" / "toc.json").read_text(encoding="utf-8"))
    assert data["entries"][0]["title"] == "第1章 化学研究的天地"
    assert data["reviewed"] is False


def _blank_pages(count: int) -> list[dict]:
    return [
        {"page_idx": i, "page_size": [600, 800],
         "para_blocks": [_block("text", 0, [[_span("text", "正文")]])],
         "discarded_blocks": []}
        for i in range(count)
    ]


def _write_ir_page(book: Path, page_no: int, blocks: list[dict], chrome: dict | None = None) -> None:
    folder = book / "ir" / "pages"
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "page": page_no,
        "blocks": blocks,
        "chrome": chrome or {"headers": [], "footers": [], "page_numbers": []},
    }
    (folder / f"{page_no:04d}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _txt(text: str, kind: str = "text", block_id: str = "") -> dict:
    row = {"type": kind, "kind": "body", "text": text}
    if block_id:
        row["id"] = block_id
    return row


def test_ir_toc_copyright_page_does_not_start_scan(tmp_path: Path) -> None:
    """版权页「开 本 890×1240 1/16」曾被斜杠规则当成目录，真目录再也扫不到。"""
    book = _book(tmp_path, _blank_pages(10))
    _write_ir_page(book, 3, [
        _txt("主 编 李大潜 王建磐"),
        _txt("开 本 890×1240 1/16"),
        _txt("印 张 6/16"),
    ])
    _write_ir_page(book, 6, [
        _txt("目录", kind="title"),
        _txt("第1章 集合与逻辑\n1.1 集合初步 2\n1.2 常用逻辑用语 13\n内容提要 20"),
    ])
    _write_ir_page(book, 9, [_txt("1.1 集合初步", kind="title")], chrome={"page_numbers": [{"text": "2"}]})
    from ir_toc import build_toc

    toc = build_toc(book)
    assert toc["toc_pages"] == [6]
    rows = [(e["title"], e["print_page"], e["pdf_page"]) for e in toc["entries"]]
    assert rows == [("1.1 集合初步", "2", 9)]
    assert not any("开" in title for title, _p, _pdf in rows)


def test_ir_toc_space_form_needs_entry_shape(tmp_path: Path) -> None:
    """目录页上的正文行不进目录。正文页的标题块要进，句子形状留给审核删。"""
    book = _book(tmp_path, _blank_pages(8))
    _write_ir_page(book, 4, [
        _txt("目录", kind="title"),
        _txt("1.1 集合初步 2\n复习题 20\n这一段话恰好结尾是 7\n实验室里有 3"),
    ])
    _write_ir_page(book, 8, [
        _txt("1.1 集合初步", kind="title"),
        _txt("实验室里有 3", kind="title"),
    ])
    from ir_toc import build_toc

    titles = [e["title"] for e in build_toc(book)["entries"]]
    assert titles == ["1.1 集合初步", "实验室里有 3"]
    assert "这一段话恰好结尾是" not in titles


def test_ir_toc_skips_derived_close_blocks(tmp_path: Path) -> None:
    """分栏收口块（kb-close-*）与原块同文，一起数会让每条目录出现两遍。"""
    book = _book(tmp_path, _blank_pages(8))
    _write_ir_page(book, 8, [
        _txt("1.1 集合初步", kind="title", block_id="kb-2"),
        _txt("1.1 集合初步", kind="title", block_id="kb-close-0-1"),
        _txt("1.2 常用逻辑用语", kind="title", block_id="kb-3"),
    ])
    from ir_toc import build_toc

    titles = [e["title"] for e in build_toc(book)["entries"]]
    assert titles == ["1.1 集合初步", "1.2 常用逻辑用语"]


def test_ir_toc_keeps_every_body_title(tmp_path: Path) -> None:
    """正文标题块全部留下，包括长练习指令。删练习是审核的事。"""
    book = _book(tmp_path, _blank_pages(8))
    _write_ir_page(book, 3, [_txt("开 本 890×1240 1/16")])
    _write_ir_page(book, 6, [_txt("1 No limits", kind="title")])
    _write_ir_page(book, 7, [
        _txt("1 Look at the photos on pages 6 and 7 and answer", kind="title"),
    ])
    _write_ir_page(book, 8, [_txt("2 Roads to education", kind="title")])
    from ir_toc import build_toc

    toc = build_toc(book)
    assert toc["toc_pages"] == []
    rows = [(e["title"], e["pdf_page"]) for e in toc["entries"]]
    assert ("1 No limits", 6) in rows
    assert ("2 Roads to education", 8) in rows
    assert ("1 Look at the photos on pages 6 and 7 and answer", 7) in rows


def test_ir_toc_title_sits_on_its_own_page(tmp_path: Path) -> None:
    """标题块在哪一页，目录就链到哪一页。不从目录行上的印刷页码去换算。"""
    book = _book(tmp_path, _blank_pages(14))
    _write_ir_page(book, 6, [
        _txt("目录", kind="title"),
        _txt("第一章 运动的描述 1\n第一节 质点 物理模型 2"),
    ])
    _write_ir_page(book, 9, [_txt("第一节 质点 物理模型", kind="title")])
    _write_ir_page(book, 12, [_txt("第二节 位置的变化 位移", kind="title")])
    from ir_toc import build_toc

    toc = build_toc(book)
    by_title = {e["title"]: e["pdf_page"] for e in toc["entries"]}
    assert by_title["第一节 质点 物理模型"] == 9
    assert by_title["第二节 位置的变化 位移"] == 12


def test_apply_polish_decisions_copies_title_and_page() -> None:
    from ir_toc import apply_polish_decisions

    items = [
        {"id": 0, "level": 1, "title": "第1章 集合与逻辑", "pdf_page": 8},
        {"id": 1, "level": 2, "title": "练习1.1(1)", "pdf_page": 10},
        {"id": 2, "level": 1, "title": "1.1 集合初步", "pdf_page": 9},
    ]
    kept = apply_polish_decisions(
        items,
        [
            {"id": 0, "action": "keep", "level": 0, "title": "集合与逻辑", "pdf_page": 1},
            {"id": 2, "action": "keep", "level": 9},
            {"id": 99, "action": "drop"},
        ],
    )
    assert [row["title"] for row in kept] == ["第1章 集合与逻辑", "1.1 集合初步"]
    assert kept[0]["pdf_page"] == 8
    assert kept[0]["level"] == 0
    assert kept[1]["level"] == 1
    assert kept[1]["pdf_page"] == 9


def test_ir_toc_apply_review_keeps_chapter_level_zero() -> None:
    from ir_toc import _apply_review_edits

    kept = _apply_review_edits(
        [{"title": "第1章 集合与逻辑", "label": "第1章 集合与逻辑", "level": 0, "pdf_page": 8, "is_chapter": True}],
        [
            {
                "pdf_page": 8,
                "title": "第1章 集合与逻辑",
                "action": "rename",
                "label": "第1章 集合与逻辑",
                "level": 0,
            }
        ],
    )
    assert kept[0]["level"] == 0
    assert kept[0]["is_chapter"] is True


def test_apply_layout_toc_file_preserves_level_zero(tmp_path: Path) -> None:
    from ir_toc import apply_layout_toc_file

    book = _toc_book(tmp_path)
    export = tmp_path / "layout-toc.json"
    export.write_text(
        json.dumps(
            {
                "entries": [
                    {"level": 0, "title": "第1章 集合与逻辑", "pdf_page": 8},
                    {"level": 1, "title": "1.1 集合初步", "pdf_page": 9},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out = apply_layout_toc_file("irbook", export, root=str(tmp_path), source="test_layout")
    assert out["entries"] == 2
    toc = json.loads((book / "ir" / "toc.json").read_text(encoding="utf-8"))
    assert toc["entries"][0]["level"] == 0
    assert toc["entries"][0]["is_chapter"] is True
    assert toc["entries"][1]["level"] == 1


def test_ir_toc_reviewed_file_is_kept(tmp_path: Path) -> None:
    from ir_toc import _apply_review_edits, ir_toc

    kept = _apply_review_edits(
        [
            {"title": "1 集合", "label": "1 集合", "level": 2, "pdf_page": 9, "is_chapter": False},
            {"title": "练习1.1(1)", "label": "练习1.1(1)", "level": 2, "pdf_page": 10, "is_chapter": False},
        ],
        [
            {"pdf_page": 9, "title": "1 集合", "action": "keep"},
            {"pdf_page": 10, "title": "练习1.1(1)", "action": "drop"},
        ],
    )
    assert [row["title"] for row in kept] == ["1 集合"]

    book = _toc_book(tmp_path)
    ir_toc("irbook", root=str(tmp_path), apply="fix")
    path = book / "ir" / "toc.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["reviewed"] = True
    data["entries"] = [{"title": "已审", "label": "已审", "level": 0, "pdf_page": 4}]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    again = ir_toc("irbook", root=str(tmp_path), apply="fix")
    assert again["skipped"] is True
    assert json.loads(path.read_text(encoding="utf-8"))["entries"][0]["title"] == "已审"
    ir_toc("irbook", root=str(tmp_path), apply="fix", force=True)
    rewritten = json.loads(path.read_text(encoding="utf-8"))
    assert rewritten["reviewed"] is False
    assert rewritten["entries"][0]["title"] == "第1章 化学研究的天地"


def _notes_book(tmp_path: Path) -> Path:
    return _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [
                _block("text", 0, [[_span("text", "原文有错别字的段落")]], bbox=[60, 100, 540, 140]),
                _block("title", 1, [[_span("text", "原标题")]], bbox=[60, 150, 300, 170], level=2),
                _block("text", 2, [[_span("text", "要删掉的噪声块")]], bbox=[60, 200, 540, 240]),
            ],
            "discarded_blocks": [],
        }],
    )


def test_apply_notes_replace_retitle_delete(tmp_path: Path) -> None:
    book = _notes_book(tmp_path)
    page = ir_lib.ingest_page(book, 1, box_prefixes=[])
    by_text = {b["text"]: b for b in page["blocks"] if not ir_lib.is_close_block(b)}
    notes = [
        {"id": "n1", "block": by_text["原文有错别字的段落"]["id"], "op": "replace",
         "old": "错别字", "new": "正确"},
        {"id": "n2", "block": by_text["原标题"]["id"], "op": "retitle",
         "old": "原标题", "new": "新标题"},
        {"id": "n3", "block": by_text["要删掉的噪声块"]["id"], "op": "delete"},
    ]
    out, rejected = ir_lib.apply_notes(page, notes)
    assert rejected == []
    texts = [b["text"] for b in out["blocks"]]
    assert "原文有正确的段落" in texts
    assert "新标题" in texts
    assert "要删掉的噪声块" not in texts
    # 输入页不被改动
    assert any(b["text"] == "原文有错别字的段落" for b in page["blocks"])


def test_apply_notes_with_no_notes_is_a_no_op(tmp_path: Path) -> None:
    """没有注解时，读页必须原样返回。

    收口块的 text 在摄取时来自版式器的 piece 文本，比它自己的 span 更全；
    无条件照 span 重拼就会让接口吐出的字跟磁盘上的字不一样（一条注解都没有也不一样）。
    """
    book = _notes_book(tmp_path)
    page = ir_lib.ingest_page(book, 1, box_prefixes=[])
    out, rejected = ir_lib.apply_notes(page, [])
    assert rejected == []
    assert out["blocks"] == page["blocks"]
    assert out.get("layout") == page.get("layout")
    assert "layout" not in page
    assert page["source"]["schema"] == ir_lib.IR_SCHEMA


def test_apply_notes_still_rebuilds_legacy_close_blocks(tmp_path: Path) -> None:
    """老页上的收口块还得跟着源块走。

    新摄取已经不再合并原始块，磁盘上却还躺着几十本没重摄的书，它们的 kb-close-*
    仍会被读页拿去渲染。源块改了字而收口块不跟，接口上那一段就还是错字。
    """
    book = _notes_book(tmp_path)
    page = ir_lib.ingest_page(book, 1, box_prefixes=[])
    assert not any(ir_lib.is_close_block(b) for b in page["blocks"]), "新摄取不该再造收口块"

    src = next(b for b in page["blocks"] if b.get("text") == "原文有错别字的段落")
    legacy = {
        "id": "kb-close-0-0",
        "type": "text",
        "kind": "body",
        "text": src["text"],
        "source_ids": [src["id"]],
        "lines": [[dict(s, b=src["id"]) for s in ir_bands.spans_from_block(src)]],
    }
    page = {**page, "blocks": [*page["blocks"], legacy]}

    out, rejected = ir_lib.apply_notes(
        page,
        [{"id": "n", "block": src["id"], "op": "replace", "old": "错别字", "new": "正确"}],
    )
    assert rejected == []
    new = next(b for b in out["blocks"] if b["id"] == "kb-close-0-0")
    assert "正确" in new["text"], "收口块没跟着源块更新"
    assert "错别字" not in new["text"]


def test_retitle_rebuilds_spans_instead_of_overwriting_each(tmp_path: Path) -> None:
    """标题被切成多段 span 时，retitle 不能把新标题写进每一段。

    旧写法逐段赋值，一个两段的标题会变成「新标题新标题」。HTML 上看不出来是因为
    titleHtml 只读 text；lines 一旦坏掉，后续 replace 就会匹配到重复文字。
    """
    book = _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [
                _block(
                    "title",
                    0,
                    [[_span("text", "第章 "), _span("text", "种群和群落")]],
                    bbox=[60, 100, 400, 130],
                    level=2,
                ),
            ],
            "discarded_blocks": [],
        }],
    )
    page = ir_lib.ingest_page(book, 1, box_prefixes=[])
    title = next(b for b in page["blocks"] if b.get("type") == "title")
    assert sum(1 for s in ir_lib.ir_spans(title["lines"]) if s.get("t") == "text") >= 2, "原料不是多段"

    out, rejected = ir_lib.apply_notes(
        page,
        [{"id": "n", "block": title["id"], "op": "retitle",
          "old": title["text"], "new": "第1章 种群和群落"}],
    )
    assert rejected == []
    for blk in out["blocks"]:
        if blk.get("type") != "title":
            continue
        joined = "".join(s.get("c") or "" for s in ir_lib.ir_spans(blk["lines"]))
        assert joined == "第1章 种群和群落", f"{blk['id']} 的 span 被写重了：{joined!r}"
        assert blk["text"] == "第1章 种群和群落"


def test_retitle_new_text_can_carry_subscript(tmp_path: Path) -> None:
    """新标题里带下标标记时，按同一套切法切成 span。"""
    book = _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [
                _block("title", 0, [[_span("text", "H2O")]], bbox=[60, 100, 300, 130], level=2),
            ],
            "discarded_blocks": [],
        }],
    )
    page = ir_lib.ingest_page(book, 1, box_prefixes=[])
    title = next(b for b in page["blocks"] if b.get("type") == "title")
    out, rejected = ir_lib.apply_notes(
        page,
        [{"id": "n", "block": title["id"], "op": "retitle",
          "old": "H2O", "new": "H<sub>2</sub>O"}],
    )
    assert rejected == []
    blk = next(b for b in out["blocks"] if b["id"] == title["id"])
    kinds = [(s.get("t"), s.get("c")) for s in ir_lib.ir_spans(blk["lines"])]
    assert ("sub", "2") in kinds


def test_apply_notes_rejects_unmatched_old(tmp_path: Path) -> None:
    book = _notes_book(tmp_path)
    page = ir_lib.ingest_page(book, 1, box_prefixes=[])
    notes = [{"id": "bad", "block": page["blocks"][0]["id"], "op": "replace",
              "old": "不存在的文字", "new": "x"}]
    out, rejected = ir_lib.apply_notes(page, notes)
    assert len(rejected) == 1 and rejected[0]["reason"] == "old not found"
    assert out["blocks"][0]["text"] == page["blocks"][0]["text"]


def test_render_applies_notes(tmp_path: Path) -> None:
    book = _notes_book(tmp_path)
    page = ir_lib.ingest_page(book, 1, box_prefixes=[])
    notes = [{"id": "n1", "block": page["blocks"][0]["id"], "op": "replace",
              "old": "错别字", "new": "正确"}]
    md = ir_lib.render_page_md(page, notes=notes)
    assert "原文有正确的段落" in md


def test_ir_note_tool_reads_yaml(tmp_path: Path) -> None:
    _notes_book(tmp_path)
    notes_dir = tmp_path / "irbook" / "ir" / "notes"
    notes_dir.mkdir(parents=True)
    page = ir_lib.ingest_page  # noqa: F841  (工具内部自取)
    (notes_dir / "0001.yaml").write_text(
        "- id: n1\n  block: PLACEHOLDER\n  op: replace\n  old: 错别字\n  new: 正确\n",
        encoding="utf-8",
    )
    from ir_notes import ir_note

    book = tmp_path / "irbook"
    blk_id = ir_lib.ingest_page(book, 1, box_prefixes=[])["blocks"][0]["id"]
    (notes_dir / "0001.yaml").write_text(
        f"- id: n1\n  block: {blk_id}\n  op: replace\n  old: 错别字\n  new: 正确\n",
        encoding="utf-8",
    )
    out = ir_note("irbook", 1, root=str(tmp_path), apply="fix")
    assert out["ok"] and out["notes"] == 1 and out["rejected"] == []
    assert "原文有正确的段落" in out["markdown"]


def test_eq_span_latex_not_shifted_by_image_only_equation(tmp_path: Path) -> None:
    """图片型行内公式不占 eq 槽位，后续 latex 不错位（审查 #2）。"""
    book = _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [
                _block("text", 0, [[
                    {"type": "text", "content": "能量", "bbox": [60, 100, 100, 120]},
                    {"type": "inline_equation", "content": "", "bbox": [100, 100, 140, 130]},
                    {"type": "inline_equation", "content": "x^2", "bbox": [140, 100, 200, 125]},
                ]], bbox=[60, 100, 540, 140]),
            ],
            "discarded_blocks": [],
        }],
    )
    model = [[
        {"type": "inline_formula", "bbox": [0.23, 0.125, 0.33, 0.156], "latex": "x^{2}"},
    ]]
    (book / "mineru" / "demo_model.json").write_text(
        json.dumps(model, ensure_ascii=False), encoding="utf-8"
    )
    out = ir_lib.ingest_page(book, 1, box_prefixes=[])
    spans = ir_lib.ir_spans(out["blocks"][0]["lines"])
    eq = [s for s in spans if s.get("t") == "eq"]
    assert len(eq) == 1
    assert eq[0]["latex"] == "x^{2}"


def test_compute_remap_rejects_ambiguous_cross_page_text(tmp_path: Path) -> None:
    """重复例题跨页同文：不产生跨页错配（审查 #6）。"""
    page = {
        "page_idx": 0,
        "page_size": [600, 800],
        "para_blocks": [_block("text", 0, [[_span("text", "同一道例题")]], bbox=[60, 100, 540, 140])],
        "discarded_blocks": [],
    }
    book = _book(tmp_path, [page, json.loads(json.dumps(page)) | {"page_idx": 1}])
    a = ir_lib.ingest_page(book, 1, box_prefixes=[])
    b = ir_lib.ingest_page(book, 2, box_prefixes=[])
    old_pages = {
        1: [{"id": a["blocks"][0]["id"], "type": "text", "text": a["blocks"][0]["text"], "order": 0}],
    }
    moved = json.loads(json.dumps(page))
    moved["para_blocks"][0]["bbox"] = [70, 110, 550, 150]  # 超出 10px 网格 → 新 id
    book2 = tmp_path / "irbook2"
    (book2 / "mineru").mkdir(parents=True)
    (book2 / "mineru" / "layout.json").write_text(
        json.dumps({"pdf_info": [moved, json.loads(json.dumps(moved)) | {"page_idx": 1}]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    new_pages = {1: ir_lib.ingest_page(book2, 1, box_prefixes=[]),
                 2: ir_lib.ingest_page(book2, 2, box_prefixes=[])}
    remap = ir_lib.compute_remap(old_pages, new_pages)
    # 同文在第 1 页就有新块 → 页内精确配；绝不指到第 2 页
    assert remap.get(old_pages[1][0]["id"]) == new_pages[1]["blocks"][0]["id"]


def test_apply_notes_replace_span_first_rejects_dollar_boundary(tmp_path: Path) -> None:
    """old 跨 $ 边界时拒绝，不让 text 与 lines 分叉（审查 #5）。"""
    book = _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [
                _block("text", 0, [[_span("text", "处于"), _span("inline_equation", "2p"),
                                    _span("text", "轨道上")]], bbox=[60, 100, 540, 140]),
            ],
            "discarded_blocks": [],
        }],
    )
    page = ir_lib.ingest_page(book, 1, box_prefixes=[])
    blk = page["blocks"][0]
    notes = [{"id": "n", "block": blk["id"], "op": "replace", "old": "处于$2p$轨道", "new": "X"}]
    out, rejected = ir_lib.apply_notes(page, notes)
    assert len(rejected) == 1 and rejected[0]["reason"] == "old not found"
    assert out["blocks"][0]["text"] == page["blocks"][0]["text"]


def test_ir_diff_tool_imports_from_own_file(tmp_path: Path) -> None:
    from ir_diff import ir_diff

    _notes_book(tmp_path)
    out = ir_diff("irbook", page_no=1, root=str(tmp_path))
    assert out["ok"] and out["pages"] == 1
    assert "similarity" in out["report"][0] or out["report"][0].get("old") == "missing"


def test_v2_level_conflict_keeps_layout_level(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [
                _block("title", 0, [[_span("text", "电子云")]], bbox=[60, 150, 200, 170], level=2),
            ],
            "discarded_blocks": [],
        }],
    )
    v2 = [[{
        "type": "title",
        "content": {"title_content": [{"content": "电子云"}], "level": 1},
        "bbox": [60, 150, 200, 170],
    }]]
    (book / "mineru" / "demo_content_list_v2.json").write_text(
        json.dumps(v2, ensure_ascii=False), encoding="utf-8"
    )
    out = ir_lib.ingest_page(book, 1, box_prefixes=[])
    title = out["blocks"][0]
    assert title["level"] == 2
    assert title["level_conflict"] == 1


def test_center_distance_pairs_crossed_boxes(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [
                _block("image", 0, [[_span("text", "", image="left.jpg")]], bbox=[60, 100, 200, 300]),
                _block("image", 1, [[_span("text", "", image="right.jpg")]], bbox=[320, 100, 540, 300]),
            ],
            "discarded_blocks": [],
        }],
    )
    v1 = [
        {"type": "image", "page_idx": 0, "image_caption": ["右题注"], "bbox": [320, 100, 540, 300]},
        {"type": "image", "page_idx": 0, "image_caption": ["左题注"], "bbox": [60, 100, 200, 300]},
    ]
    (book / "mineru" / "demo_content_list.json").write_text(
        json.dumps(v1, ensure_ascii=False), encoding="utf-8"
    )
    out = ir_lib.ingest_page(book, 1, box_prefixes=[])
    by_asset = {b["asset"]: b["caption"] for b in out["blocks"]}
    assert by_asset["left.jpg"] == "左题注"
    assert by_asset["right.jpg"] == "右题注"


def test_load_or_ingest_page_skips_rewrite_when_hash_matches(tmp_path: Path) -> None:
    _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [_block("text", 0, [[_span("text", "缓存页")]], bbox=[60, 100, 540, 140])],
            "discarded_blocks": [],
        }],
    )
    book = tmp_path / "irbook"
    first, wrote = ir_lib.load_or_ingest_page(book, 1)
    assert wrote is True
    path = ir_lib.page_json_path(book, 1)
    raw = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    second, wrote2 = ir_lib.load_or_ingest_page(book, 1)
    assert wrote2 is False
    assert path.read_bytes() == raw
    assert path.stat().st_mtime_ns == mtime
    assert second["blocks"][0]["text"] == first["blocks"][0]["text"]


def test_inline_image_merges_into_neighbor_text(tmp_path: Path) -> None:
    page = {
        "page_idx": 0,
        "page_size": [600, 800],
        "para_blocks": [
            _block("text", 0, [[_span("text", "正文第一句")]], bbox=[60, 100, 540, 120]),
            _block("image", 1, [[_span("text", "", image="icon.jpg")]], bbox=[60, 130, 90, 145]),
        ],
        "discarded_blocks": [],
    }
    book = _book(tmp_path, [page])
    md = ir_lib.render_page_md(ir_lib.ingest_page(book, 1, box_prefixes=[]))
    assert "正文第一句 ![图](images/icon.jpg)" in md


def test_ingest_layout_star_runs_all_layout_books(tmp_path: Path) -> None:
    _book(tmp_path, [{
        "page_idx": 0, "page_size": [600, 800],
        "para_blocks": [_block("text", 0, [[_span("text", "甲书")]], bbox=[60, 100, 540, 140])],
        "discarded_blocks": [],
    }])
    other = tmp_path / "other"
    mineru = other / "mineru"
    mineru.mkdir(parents=True)
    (mineru / "layout.json").write_text(
        json.dumps({
            "pdf_info": [{
                "page_idx": 0, "page_size": [600, 800],
                "para_blocks": [_block("text", 0, [[_span("text", "乙书")]], bbox=[60, 100, 540, 140])],
                "discarded_blocks": [],
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (other / "meta.json").write_text(json.dumps({"id": "other", "page_count": 1}), encoding="utf-8")
    out = ingest_layout("*", root=str(tmp_path), apply="scan")
    assert out["ok"] and out["books"] == 2 and out["pages"] == 2


def test_search_md_reports_book_page_block(tmp_path: Path) -> None:
    from search_md import search_md

    book = _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [_block("text", 0, [[_span("text", "可检索的中间表示")]], bbox=[60, 100, 540, 140])],
            "discarded_blocks": [],
        }],
    )
    ir_lib.save_page_json(book, ir_lib.ingest_page(book, 1, box_prefixes=[]))
    hits = search_md(str(tmp_path), "中间表示")
    assert hits
    assert hits[0]["path"].startswith("irbook:1#")


def test_ir_routes_out_of_range_page_is_404(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app import surfaces

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    surfaces.reset_for_tests()
    ensure_workspace()
    book = tmp_path / "home" / "textbooks" / "irb"
    (book / "mineru").mkdir(parents=True)
    (book / "mineru" / "layout.json").write_text(
        json.dumps({"pdf_info": [{"page_idx": 0, "page_size": [600, 800],
                                  "para_blocks": [_block("text", 0, [[_span("text", "唯一一页")]])],
                                  "discarded_blocks": []}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (book / "mineru" / "demo_model.json").write_text("[]", encoding="utf-8")
    (book / "mineru" / "demo_content_list.json").write_text("[]", encoding="utf-8")
    (book / "meta.json").write_text(
        json.dumps({"id": "irb", "status": "ready", "page_count": 1}), encoding="utf-8"
    )
    from app.workspace_module import load

    load("ingest_layout").ingest_layout("irb", root=str(tmp_path / "home"), apply="fix")
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    assert client.get("/v1/ir/irb/pages/1/markdown", headers=headers).status_code == 200
    assert client.get("/v1/ir/irb/pages/99/markdown", headers=headers).status_code == 404
    assert client.get("/v1/ir/irb/pages/99/blocks", headers=headers).status_code == 404


def test_v1_caption_not_reused_when_child_already_has_it(tmp_path: Path) -> None:
    book = _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [595, 841],
            "para_blocks": [
                _block("image", 0, [[_span("text", "", image="deco.jpg")]],
                       bbox=[402, 463, 421, 481]),
                _block("image", 1, [[_span("text", "", image="flow.jpg")]],
                       bbox=[85, 526, 367, 638],
                       children=[_block("image_caption", 0,
                                       [[_span("text", "图 1-13 酱油制作工艺流程图")]])]),
            ],
            "discarded_blocks": [],
        }],
    )
    v1 = [{
        "type": "image", "page_idx": 0,
        "image_caption": ["图 1-13 酱油制作工艺流程图"],
        "bbox": [142, 625, 616, 758],
    }]
    (book / "mineru" / "demo_content_list.json").write_text(
        json.dumps(v1, ensure_ascii=False), encoding="utf-8"
    )
    out = ir_lib.ingest_page(book, 1, box_prefixes=[])
    by_asset = {b.get("asset"): b.get("caption") for b in out["blocks"]}
    assert by_asset["flow.jpg"] == "图 1-13 酱油制作工艺流程图"
    assert not by_asset.get("deco.jpg")


def test_wide_short_image_stays_figure(tmp_path: Path) -> None:
    page = {
        "page_idx": 0,
        "page_size": [600, 800],
        "para_blocks": [
            _block("text", 0, [[_span("text", "邻段")]], bbox=[60, 100, 540, 120]),
            _block("image", 1, [[_span("text", "", image="wide.jpg")]],
                   bbox=[60, 130, 200, 175]),  # 高 5.6%，宽 23% → 不是句中图
        ],
        "discarded_blocks": [],
    }
    book = _book(tmp_path, [page])
    out = ir_lib.ingest_page(book, 1, box_prefixes=[])
    fig = [b for b in out["blocks"] if b.get("asset") == "wide.jpg"][0]
    assert fig["kind"] == "figure"


def test_row_clusters_against_any_member(tmp_path: Path) -> None:
    page = {
        "page_idx": 0,
        "page_size": [600, 800],
        "para_blocks": [
            _block("image", 0, [[_span("text", "", image="a.jpg")]], bbox=[40, 100, 80, 180]),
            _block("image", 1, [[_span("text", "", image="b.jpg")]], bbox=[100, 120, 160, 200]),
            _block("image", 2, [[_span("text", "", image="c.jpg")]], bbox=[180, 150, 250, 230]),
        ],
        "discarded_blocks": [],
    }
    book = _book(tmp_path, [page])
    out = ir_lib.ingest_page(book, 1, box_prefixes=[])
    rows = [b.get("row") for b in out["blocks"] if b["kind"] == "figure"]
    assert rows == [0, 0, 0]


def test_box_colon_not_duplicated_in_markdown(tmp_path: Path) -> None:
    page = {
        "page_idx": 0,
        "page_size": [600, 800],
        "para_blocks": [
            _block("title", 0, [[_span("text", "思考与讨论：")]],
                   bbox=[400, 100, 460, 120], level=2),
        ],
        "discarded_blocks": [],
    }
    book = _book(tmp_path, [page])
    page_ir = ir_lib.ingest_page(book, 1, box_prefixes=["思考与讨论"])
    assert page_ir["blocks"][0]["kind"] == "box"
    md = ir_lib.render_page_md(page_ir)
    assert md.count("思考与讨论") == 1


def test_load_or_ingest_rewrites_old_schema(tmp_path: Path) -> None:
    _book(
        tmp_path,
        [{
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [_block("text", 0, [[_span("text", "旧版")]], bbox=[60, 100, 540, 140])],
            "discarded_blocks": [],
        }],
    )
    book = tmp_path / "irbook"
    first, wrote = ir_lib.load_or_ingest_page(book, 1)
    assert wrote is True
    first["source"]["schema"] = 1
    ir_lib.save_page_json(book, first)
    again, wrote2 = ir_lib.load_or_ingest_page(book, 1)
    assert wrote2 is True
    assert again["source"]["schema"] == ir_lib.IR_SCHEMA


def test_attach_ocr_lines_preface_indent_and_height() -> None:
    """前言那种：段框 52，光学首行 85、后行 57 → first_indent≈28，行高取中位。"""
    page_size = [595.0, 841.0]
    bb = [52.0, 216.0, 530.0, 386.0]
    structured = [{
        "bbox": [52.0, 216.0, 530.0, 386.0],
        "spans": [{"t": "text", "c": "数学应该是绝大多数人一生中学得最多的一门功课。"}],
    }]
    model_page = [
        {"type": "ocr_text", "bbox": [85.1 / 595, 218.7 / 841, 526.6 / 595, 230.4 / 841], "score": 1.0},
        {"type": "ocr_text", "bbox": [57.7 / 595, 239.7 / 841, 525.4 / 595, 251.5 / 841], "score": 1.0},
        {"type": "ocr_text", "bbox": [57.1 / 595, 262.4 / 841, 526.6 / 595, 274.2 / 841], "score": 1.0},
    ]
    lines, indent, lh = ir_lib.attach_ocr_lines(structured, model_page, bb, page_size)
    assert len(lines) == 3
    assert lines[0]["spans"][0]["c"].startswith("数学应该是")
    assert lines[1]["spans"] == [] and lines[2]["spans"] == []
    assert indent is not None and 26 <= indent <= 30
    assert lh is not None and 11 <= lh <= 13


def test_attach_ocr_lines_same_row_formula_is_one_line() -> None:
    """同一视觉行上的题干和公式收成一簇，写不出假缩进。"""
    page_size = [595.0, 841.0]
    bb = [221.0, 327.0, 351.0, 343.0]
    structured = [{"bbox": bb, "spans": [{"t": "text", "c": "(4) 不等式"}]}]
    model_page = [
        {"type": "ocr_text", "bbox": [223.1 / 595, 328 / 841, 280 / 595, 342 / 841], "score": 1.0},
        {"type": "ocr_text", "bbox": [339.1 / 595, 328 / 841, 350 / 595, 342 / 841], "score": 1.0},
    ]
    lines, indent, lh = ir_lib.attach_ocr_lines(structured, model_page, bb, page_size)
    assert len(lines) == 1
    assert indent is None
    assert lh is not None
    assert lines[0]["bbox"][0] < 230


def test_ingest_page_writes_first_indent(tmp_path: Path) -> None:
    page = {
        "page_idx": 0,
        "page_size": [595, 841],
        "para_blocks": [
            _block("title", 0, [[_span("text", "前言")]], bbox=[242, 134, 341, 161], level=1),
            _block(
                "text",
                1,
                [[_span("text", "数学应该是绝大多数人一生中学得最多的一门功课。")]],
                bbox=[52, 216, 530, 386],
            ),
        ],
        "discarded_blocks": [],
    }
    book = _book(tmp_path, [page])
    model = [[
        {"type": "ocr_text", "bbox": [0.143, 0.260, 0.885, 0.274], "score": 1.0},
        {"type": "ocr_text", "bbox": [0.097, 0.285, 0.883, 0.299], "score": 1.0},
        {"type": "ocr_text", "bbox": [0.097, 0.312, 0.885, 0.326], "score": 1.0},
    ]]
    out = ir_lib.ingest_page(book, 1, model=model)
    body = [b for b in out["blocks"] if b["type"] == "text"][0]
    assert len(body["lines"]) == 3
    assert body.get("first_indent", 0) >= 20
    assert body.get("line_height", 0) > 8
    title = [b for b in out["blocks"] if b["type"] == "title"][0]
    assert title.get("first_indent") is None
