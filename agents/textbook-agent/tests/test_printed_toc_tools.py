from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "workspace" / "tools"
sys.path.insert(0, str(TOOLS))

import printed_toc_layout as layout  # noqa: E402
import printed_toc_skeleton as skeleton  # noqa: E402
import printed_toc_tools as tools  # noqa: E402
import printed_toc_agent as agent  # noqa: E402
import printed_toc_decide as decide  # noqa: E402


def test_qwen_vl_prompt_uses_the_server_marker() -> None:
    got = tools.qwen_vl_prompt("右栏灰条", "<__media_test__>", 2)
    assert got == (
        "<|im_start|>user\n<__media_test__>\n<__media_test__>\n右栏灰条<|im_end|>\n"
        "<|im_start|>assistant\n"
    )


def test_image_url_is_stripped_to_raw_base64() -> None:
    part = tools._image_part(b"\xff\xd8\xff")
    url = part["image_url"]["url"]
    _, images = tools._content_parts([part, {"type": "text", "text": "问"}])
    assert url.startswith("data:image/jpeg;base64,")
    assert images == [url.split(",", 1)[1]]
    assert "data:" not in images[0]


def test_pp_zero_does_not_veto_index() -> None:
    got = tools.score_signals(index_lines=8, tail_rows=0, pp_score=0.0, vision_yes=None)
    assert got["pp"] == 0
    assert got["index"] == 2
    assert got["points"] >= 2


def test_vision_failure_does_not_veto() -> None:
    got = tools.score_signals(index_lines=8, tail_rows=6, pp_score=0.9, vision_yes=None)
    assert got["vision"] == 0
    assert got["points"] >= 2
    assert got["disagree"] is False


def test_index_only_and_vision_only_each_suffice() -> None:
    index_only = tools.score_signals(8, 0, 0.0, None)
    vision_only = tools.score_signals(0, 0, 0.0, True)
    weak = tools.score_signals(0, 6, 0.0, None)
    assert index_only["points"] >= 2
    assert vision_only["points"] >= 2
    assert weak["points"] < 2


def test_vision_only_page_needs_full_page_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    kinds = {4: "前言或说明", 5: "目录", 6: None}
    asked: list[int] = []

    def confirm(_book: Path, page: int) -> tuple[str | None, float]:
        asked.append(page)
        return kinds[page], 0.1

    monkeypatch.setattr(tools, "confirm_page", confirm)
    scores = {p: tools.score_signals(0, 0, 0.0, True) for p in (4, 5, 6)}
    scores[7] = tools.score_signals(8, 0, 0.0, True)
    tools._confirm_vision_only(Path("."), scores)
    assert asked == [4, 5, 6]
    assert (scores[4]["vision"], scores[4]["points"], scores[4]["confirm"]) == (0, 0, "前言或说明")
    assert scores[5]["points"] == 2 and scores[6]["points"] == 2
    assert "confirm" not in scores[7]


def test_confirm_page_reads_the_schema_answer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent: list[dict] = []

    def chat(content: list, _tokens: int, schema: dict | None = None, sample: bool = False) -> tuple[dict, float]:
        sent.append({"text": content[0]["text"], "schema": schema})
        return {"kind": "版权页"}, 0.2

    monkeypatch.setattr(tools, "page_image", lambda *_a: b"\xff\xd8\xff")
    monkeypatch.setattr(tools, "_chat", chat)
    monkeypatch.setattr(tools, "_prompt", lambda _name: "第 {page} 页")
    assert tools.confirm_page(tmp_path, 3) == ("版权页", 0.2)
    assert sent[0]["text"] == "第 3 页"
    assert sent[0]["schema"]["properties"]["kind"]["enum"][0] == "目录"


def test_pick_run_caps_at_four() -> None:
    pages = [2, 3, 4, 5, 6, 10]
    assert tools.pick_run(pages, cap=4) == [2, 3, 4, 5]


def test_pick_run_keeps_earlier_tie() -> None:
    assert tools.pick_run([2, 3, 10, 11], cap=4) == [2, 3]


def test_outside_mineru_alone_keeps_text_layer() -> None:
    check = {
        "ok": False, "size_ok": True, "aspect_ok": True,
        "inside_ok": False, "pass_rate": 0.6, "reasons": ["outside_mineru"],
    }
    assert tools.should_fallback(check, readable=True, rotation=0) is False
    check["pass_rate"] = 0.1
    assert tools.should_fallback(check, readable=True, rotation=0) is True
    assert tools.should_fallback(check, readable=False, rotation=0) is True


def test_front_windows_ignore_back_page_pp() -> None:
    scores = {
        5: tools.score_signals(0, 0, 0.0, None),
        17: tools.score_signals(0, 0, 0.9, None),
    }
    collected = {"page_count": 40, "index": {}}
    route, windows = tools._front_windows(collected, scores, 40)
    assert windows[0][0] <= 16
    assert 17 not in windows[0] or 5 in windows[0]


def test_self_check_fails_outside_boxes() -> None:
    lines = [{"x0": 10, "y0": 10, "x1": 20, "y1": 20}]
    got = tools.self_check_lines(
        lines,
        pdf_size=(100.0, 200.0),
        mineru_size=(100.0, 200.0),
        image_size=(200.0, 400.0),
        boxes=[[80.0, 80.0, 90.0, 90.0]],
    )
    assert got["ok"] is False
    assert "outside_mineru" in got["reasons"]
    assert got["pass_rate"] == 0.0


def test_self_check_size_mismatch() -> None:
    lines = [{"x0": 10, "y0": 10, "x1": 20, "y1": 20}]
    got = tools.self_check_lines(
        lines,
        pdf_size=(100.0, 200.0),
        mineru_size=(400.0, 600.0),
        image_size=(100.0, 200.0),
        boxes=[[0.0, 0.0, 100.0, 200.0]],
    )
    assert got["ok"] is False
    assert "size_mismatch" in got["reasons"]


def test_cache_hit_and_torch_change(tmp_path: Path) -> None:
    inputs = {"torch": "2.13.0", "pdf_sha256": "aaa"}
    old = {"inputs": dict(inputs), "entries": []}
    assert tools.cache_usable(old, inputs, force=False) is True
    assert tools.cache_usable(old, inputs, force=True) is False
    changed = dict(inputs, torch="2.14.0")
    assert tools.cache_usable(old, changed, force=False) is False
    assert tools.cache_usable(None, inputs, force=False) is False


def test_layout_unavailable_equals_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    tools._layout_cache.clear()
    monkeypatch.delenv("LAYOUT_SVC_URL", raising=False)
    monkeypatch.setenv("LAYOUT_SVC_URL", "")
    book = Path("/tmp/no-such-book-printed-toc")
    got = tools.layout_regions(book, [4, 5])
    assert got["ok"] is False
    assert got["pages"] == {}
    scored = tools.score_signals(8, 0, tools.content_score([]), None)
    assert scored["pp"] == 0
    assert scored["index"] == 2
    assert tools.pick_run([4, 5]) == [4, 5]


def test_agent_cache_short_circuit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    book = tmp_path / "bookA"
    book.mkdir()
    (book / "book.pdf").write_bytes(b"%PDF-1.4 empty")
    inputs = tools.inputs_fingerprint(book, {"revision": "r", "torch": "t", "transformers": "f"})
    old = {
        "schema_version": 1,
        "inputs": agent.arch_inputs(inputs, "v2"),
        "entries": [{"label": "第1章", "verified": "body"}],
        "method": "text_layer",
        "toc_pages": [4],
        "page_offset": 5,
        "timing_s": {"total": 1.0},
    }
    (book / "toc.print").write_text(json.dumps(old), encoding="utf-8")

    def boom(*_a, **_k):
        raise AssertionError("should not run")

    monkeypatch.setattr(tools, "layout_health", lambda: {"revision": "r", "torch": "t", "transformers": "f"})
    monkeypatch.setattr(tools, "inputs_fingerprint", lambda *_a, **_k: inputs)
    monkeypatch.setattr(tools, "book_dir", lambda *_a, **_k: book)
    monkeypatch.setattr(tools, "collect_pages", boom)
    got = agent.printed_toc_agent("bookA", root=str(tmp_path))
    assert got["cache"] == "hit"
    assert got["entry_count"] == 1
    with pytest.raises(AssertionError, match="should not run"):
        agent.printed_toc_agent("bookA", root=str(tmp_path), arch="v1")


def test_v1_cache_is_not_served_to_v2() -> None:
    base = {"pdf_sha256": "x", "prompts": {"fill": "f"}}
    v2 = agent.arch_inputs(base, "v2")
    assert agent.arch_inputs(base, "v1") is base
    assert v2["arch"] == "v2" and {"decide", "pair"} <= set(v2["prompts"])
    assert base == {"pdf_sha256": "x", "prompts": {"fill": "f"}}
    assert not tools.cache_usable({"inputs": base}, v2, force=False)


def _draft(text: str, page: int | None = None, y0: float = 0, y1: float = 10, level: int = 1) -> dict:
    return {
        "text": text,
        "print_page": page,
        "hole": None,
        "hole_box": None,
        "x0": 10.0,
        "x1": 100.0,
        "y0": y0,
        "y1": y1,
        "level": level,
        "size": 14.0,
        "bold": False,
        "chars": [],
        "pdf_page": 5,
        "col": 0,
    }


def test_apply_review_joins_wrap_and_keeps_later_page() -> None:
    rows = [
        _draft("第八章·牛顿力学的局限性", y0=577, y1=593, level=0),
        _draft("与相对论初步", page=87, y0=599, y1=617, level=1),
    ]
    got = tools.apply_review(rows, {"rows": [{"from": [1, 2], "level": 0}]})
    assert got is not None and len(got) == 1
    assert got[0]["text"] == "第八章·牛顿力学的局限性与相对论初步"
    assert got[0]["print_page"] == 87
    assert got[0]["level"] == 0
    assert got[0]["level_from"] == "review"


def test_apply_review_splits_glued_line_and_keeps_named_page() -> None:
    rows = [_draft("Appendices Vocabulary", page=77, y0=70, y1=126)]
    got = tools.apply_review(rows, {"rows": [{"from": [1], "parts": [
        {"text": "Appendices", "level": 0, "keep_page": False},
        {"text": "Vocabulary", "level": 1, "keep_page": True},
    ]}]})
    assert got is not None
    assert [row["text"] for row in got] == ["Appendices", "Vocabulary"]
    assert got[0]["print_page"] is None
    assert got[0]["level"] == 0
    assert got[1]["print_page"] == 77
    assert got[1]["level"] == 1


def test_apply_review_rejects_gap_and_rewritten_part() -> None:
    rows = [_draft("A"), _draft("B"), _draft("C")]
    assert tools.apply_review(rows, {"rows": [
        {"from": [1], "level": 0},
        {"from": [3], "level": 0},
    ]}) is None
    glued = [_draft("Appendices Vocabulary", page=77)]
    assert tools.apply_review(glued, {"rows": [{"from": [1], "parts": [
        {"text": "附录", "level": 0, "keep_page": False},
        {"text": "Vocabulary", "level": 1, "keep_page": True},
    ]}]}) is None


def _gate_row(text: str, **kwargs: object) -> dict:
    method = kwargs.pop("method", "text_layer")
    row = _draft(text, **kwargs)  # type: ignore[arg-type]
    row["fact_method"] = method
    row["level_draft"] = row["level"]
    return row


def test_gate_merges_physics_continuation() -> None:
    rows = [
        _gate_row("第八章·牛顿力学的局限性", level=0, y0=577, y1=593),
        _gate_row("与相对论初步", page=87, level=1, y0=599, y1=617),
    ]
    rows[1]["x0"] = 120
    got, rejected = tools.gate_page(rows, {"rows": [{"from": [1, 2], "level": 0}]}, [])
    assert rejected == []
    assert len(got) == 1
    assert got[0]["text"] == "第八章·牛顿力学的局限性与相对论初步"
    assert got[0]["print_page"] == 87
    assert got[0]["level"] == 0


def test_gate_rejects_sibling_and_item_heads() -> None:
    chinese = [
        _gate_row("二 善用多媒介", level=1),
        _gate_row("三 辨识媒介信息", level=1, y0=20, y1=30),
    ]
    got, rejected = tools.gate_page(chinese, {"rows": [{"from": [1, 2], "level": 1}]}, [])
    assert [row["text"] for row in got] == ["二 善用多媒介", "三 辨识媒介信息"]
    assert rejected[0]["reason"] in {"v1_item_head", "v3_sibling"}

    reading = [
        _gate_row("A Reading and interaction", level=1),
        _gate_row("Reading: The 1940s house", level=2, y0=20, y1=30),
    ]
    got, rejected = tools.gate_page(reading, {"rows": [{"from": [1, 2], "level": 1}]}, [])
    assert len(got) == 2
    assert rejected[0]["kind"] == "merge"

    parens = [
        _gate_row("(一)学业质量内涵", level=2),
        _gate_row("(二)学业质量水平", level=2, y0=20, y1=30),
        _gate_row("(三)学业质量水平与考试评价的关系", level=2, y0=40, y1=50),
    ]
    got, rejected = tools.gate_page(parens, {"rows": [{"from": [1, 2, 3], "level": 2}]}, [])
    assert len(got) == 3
    assert rejected[0]["reason"] == "v3_sibling"


def test_gate_level_citation() -> None:
    parts = [
        _gate_row("第1部分 数学建模活动案例", level=0),
        _gate_row("第2部分 数学建模活动A", level=0, y0=20, y1=30),
        _gate_row("第3部分 数学建模活动B", level=0, y0=40, y1=50),
    ]
    got, rejected = tools.gate_page(parts, {"rows": [
        {"from": [1], "level": 0},
        {"from": [2], "level": 0},
        {"from": [3], "level": 1},
    ]}, [])
    assert got[2]["level"] == 0
    assert rejected[0]["kind"] == "level"

    appendix = [_gate_row("Appendices", level=1)]
    got, rejected = tools.gate_page(
        appendix,
        {"rows": [{"from": [1], "level": 0}]},
        [{"pdf_page": 80, "text": "Appendices"}],
    )
    assert rejected == []
    assert got[0]["level"] == 0
    assert got[0]["level_reason"] == "body"

    block = [
        _gate_row("Appendices", level=1),
        _gate_row("Phonetic symbols", level=2, y0=30, y1=42),
    ]
    block[0]["indent"] = 40
    block[1]["indent"] = 80
    block[1]["x0"] = 80
    got, _rejected = tools.gate_page(block, {"rows": [
        {"from": [1], "level": 0},
        {"from": [2], "level": 2},
    ]}, [{"pdf_page": 80, "text": "Appendices"}])
    assert [row["level"] for row in got] == [0, 1]

    detail = [
        _gate_row("A Reading and interaction", level=1, method="model_lines"),
        _gate_row("Reading: Going global", level=1, method="model_lines", y0=20, y1=32),
    ]
    got, rejected = tools.gate_page(detail, {"rows": [
        {"from": [1], "level": 1},
        {"from": [2], "level": 2},
    ]}, [])
    assert rejected == []
    assert [row["level"] for row in got] == [1, 2]


def test_gate_splits_glued_image_line() -> None:
    rows = [_gate_row(
        "Reading: An excerpt from The Old Man and the Sea Mini-project: Having a class discussion",
        level=1,
        method="model_lines",
    )]
    got, rejected = tools.gate_page(rows, {"rows": [{"from": [1], "parts": [
        {"text": "Reading: An excerpt from The Old Man and the Sea", "level": 2, "keep_page": False},
        {"text": "Mini-project: Having a class discussion", "level": 2, "keep_page": False},
    ]}]}, [])
    assert rejected == []
    assert [row["text"] for row in got] == [
        "Reading: An excerpt from The Old Man and the Sea",
        "Mini-project: Having a class discussion",
    ]
    assert [row["level"] for row in got] == [2, 2]


def test_same_indent_keeps_appendix_with_parts() -> None:
    rows = [
        {"text": "第1部分 案例", "size": 35, "bold": False, "x_text": 57, "x0": 57, "pdf_page": 6, "col": 0},
        {"text": "附录", "size": 33, "bold": False, "x_text": 57, "x0": 57, "pdf_page": 6, "col": 0},
        {"text": "附录1 写作", "size": 26, "bold": False, "x_text": 207, "x0": 207, "pdf_page": 6, "col": 0},
    ]
    layout.assign_levels(rows)
    assert [row["level"] for row in rows] == [0, 0, 1]


def test_bold_still_splits_same_indent() -> None:
    rows = [
        {"text": "A Reading and interaction", "size": 10, "bold": True, "x_text": 157, "x0": 157, "pdf_page": 1, "col": 0},
        {"text": "Reading: Blame your brain", "size": 10, "bold": False, "x_text": 157, "x0": 157, "pdf_page": 1, "col": 0},
    ]
    layout.assign_levels(rows)
    assert [row["level"] for row in rows] == [0, 1]


def test_glyph_map_and_decorated_toc_banner() -> None:
    assert layout.tidy("第2部分 数学建模活动犃") == "第2部分 数学建模活动A"
    assert layout.tidy("第3部分 数学建模活动犅") == "第3部分 数学建模活动B"
    assert layout._keep({"text": "│ 目录 │", "print_page": None}) is False


def _occ(page: int, text: str, chrome: bool) -> dict:
    return {"page": page, "text": text, "chrome": chrome}


def test_footer_title_is_order_only() -> None:
    items = [_occ(page, "Across the globe", True) for page in range(9, 26)]
    items.append(_occ(32, "2 Things that matter", False))
    built = skeleton.build_skeletons(items)
    order = [item["text"] for item in built["order"]]
    expect = [item["text"] for item in built["expect"]]
    assert order.count("Across the globe") == 1
    assert "Across the globe" not in expect
    assert any("Things that matter" in item for item in expect)


def test_header_self_assessment_stays_expected() -> None:
    items = [
        _occ(25, "Self-assessment", True),
        _occ(43, "Self-assessment", False),
        _occ(61, "Self-assessment", False),
        _occ(79, "Self-assessment", False),
    ]
    built = skeleton.build_skeletons(items)
    assert any(item["text"] == "Self-assessment" for item in built["expect"])


def test_two_skeleton_titles_in_one_line_split() -> None:
    titles = [
        {"text": "Across the globe", "page": 9},
        {"text": "Things that matter", "page": 32},
    ]
    parts = skeleton.split_on_hits("1 Across the globe Things that matter", titles)
    assert parts == ["1 Across the globe", "Things that matter"]
    assert skeleton.split_on_hits(
        "Reading: Finding one's true vocation",
        [{"text": "Reading:", "page": 1}, {"text": "Finding one's true vocation", "page": 2}],
    ) is None
    packed = [_gate_row("1 Across the globe Things that matter", method="model_lines")]
    rows, rejected = tools.apply_skeleton_splits(packed, {"order": titles, "expect": titles})
    assert rejected == []
    assert [row["text"] for row in rows] == ["1 Across the globe", "Things that matter"]


def test_text_layer_chapter_is_not_split() -> None:
    packed = [_gate_row("第八章·牛顿力学的局限性与相对论初步", level=0)]
    titles = [
        {"text": "牛顿力学的局限性", "page": 80},
        {"text": "相对论初步", "page": 90},
    ]
    rows, _rejected = tools.apply_skeleton_splits(packed, {"order": titles, "expect": titles})
    assert len(rows) == 1
    assert "相对论" in rows[0]["text"]


def test_backward_page_becomes_a_hole() -> None:
    rows = [
        {"col": 1, "print_page": 60, "x0": 1, "x1": 20, "y0": 0, "y1": 10, "hole": None},
        {"col": 1, "print_page": 15, "x0": 1, "x1": 20, "y0": 20, "y1": 30, "hole": None, "filled": True},
    ]
    assert skeleton.clear_backward_pages(rows) == 1
    assert rows[1]["print_page"] is None
    assert rows[1]["hole"] == "backward"


def test_missing_slot_uses_sibling_level() -> None:
    rows = [
        {"text": "1 Decisions", "level": 0},
        {"text": "Self-assessment", "level": 1},
        {"text": "2 Digital humans", "level": 0},
        {"text": "Self-assessment", "level": 1},
        {"text": "4 Inspiration", "level": 0},
        {"text": "E Cultural focus", "level": 1},
    ]
    gaps = skeleton.slot_gaps(rows)
    assert any(gap["slot"] == "Self-assessment" and gap["unit"].startswith("4 ") for gap in gaps)
    assert skeleton.slot_level(rows, "Self-assessment") == 1


def test_model_details_drop_one_level_under_a_letter() -> None:
    rows = [
        {"fact_method": "model_lines", "text": "B Grammar activity", "level": 1, "level_draft": 1},
        {"fact_method": "model_lines", "text": "The verb-ing form as the adverbial", "level": 1, "level_draft": 1},
        {"fact_method": "model_lines", "text": "Self-assessment", "level": 1, "level_draft": 1},
    ]
    tools.deepen_model_details(rows)
    assert [row["level"] for row in rows] == [1, 2, 1]


def test_neighbor_hole_only_when_same_level_has_a_page() -> None:
    rows = [
        {"fact_method": "model_lines", "col": 1, "level": 1, "print_page": 24, "hole": None,
         "x0": 10, "x1": 40, "y0": 0, "y1": 12, "text": "A Reading and interaction"},
        {"fact_method": "model_lines", "col": 1, "level": 1, "print_page": None, "hole": None,
         "x0": 10, "x1": 40, "y0": 20, "y1": 32, "text": "A Reading and interaction"},
        {"fact_method": "model_lines", "col": 1, "level": 2, "print_page": None, "hole": None,
         "x0": 10, "x1": 40, "y0": 40, "y1": 52, "text": "Reading: The Last Leaf"},
    ]
    tools.mark_neighbor_holes(rows)
    assert rows[1]["hole"] == "neighbor"
    assert rows[2]["hole"] is None


def test_detail_line_is_not_a_hole_when_a_same_level_bar_has_a_page() -> None:
    rows = [
        {"fact_method": "model_lines", "col": 0, "level": 2, "print_page": 23, "hole": None,
         "x0": 10, "x1": 40, "y0": 0, "y1": 12, "text": "Exploring literature 1: Two poems"},
        {"fact_method": "model_lines", "col": 0, "level": 2, "print_page": None, "hole": None,
         "x0": 10, "x1": 80, "y0": 20, "y1": 32, "text": "Reading: Making school meaningful"},
        {"fact_method": "model_lines", "col": 0, "level": 2, "print_page": None, "hole": None,
         "x0": 10, "x1": 80, "y0": 40, "y1": 52, "text": "The future in different forms"},
        {"fact_method": "model_lines", "col": 0, "level": 1, "print_page": 6, "hole": None,
         "x0": 10, "x1": 40, "y0": 60, "y1": 72, "text": "A Reading and interaction"},
        {"fact_method": "model_lines", "col": 0, "level": 1, "print_page": None, "hole": None,
         "x0": 10, "x1": 40, "y0": 80, "y1": 92, "text": "B Grammar activity"},
    ]
    tools.mark_neighbor_holes(rows)
    assert rows[1]["hole"] is None
    assert rows[2]["hole"] is None
    assert rows[4]["hole"] == "neighbor"


def test_over_merge_bucket() -> None:
    gold = [
        {"label": "二 善用多媒介", "level": 1, "print_page": None},
        {"label": "三 辨识媒介信息", "level": 1, "print_page": None},
    ]
    got = [{"label": "二 善用多媒介三 辨识媒介信息", "level": 1, "print_page": None, "level_draft": 1}]
    report = tools.compare(gold, got)
    assert report["over_merge"]
    assert report["pass"] is False


def test_body_titles_keep_matching_headings() -> None:
    rows = [{"text": "1 No limits"}, {"text": "A Reading and interaction"}]
    collected = {"text": {
        6: ["1 No limits"],
        9: [
            "1 No limits",
            "A Reading and interaction",
            "How did the writer feel about risk",
            "Look at the photos and discuss",
        ],
    }}
    got = tools.body_titles(collected, [6, 7], rows)
    texts = [item["text"] for item in got]
    assert texts == ["1 No limits", "A Reading and interaction"]
    assert all(item["pdf_page"] == 9 for item in got)


def test_agent_force_bypasses_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    book = tmp_path / "bookB"
    book.mkdir()
    (book / "book.pdf").write_bytes(b"%PDF-1.4 empty")
    inputs = {"torch": "t"}
    (book / "toc.print").write_text(json.dumps({"inputs": inputs, "entries": []}), encoding="utf-8")
    called = {"n": 0}

    def locate(*_a, **_k):
        called["n"] += 1
        return {
            "route": "vote",
            "candidates": [[]],
            "toc_pages": [],
            "seconds": [0.0],
            "scores": {},
            "disagree": [],
        }

    monkeypatch.setattr(tools, "layout_health", lambda: {})
    monkeypatch.setattr(tools, "inputs_fingerprint", lambda *_a, **_k: inputs)
    monkeypatch.setattr(tools, "book_dir", lambda *_a, **_k: book)
    monkeypatch.setattr(tools, "collect_pages", lambda *_a, **_k: {"page_count": 0, "index": {}, "text": {}, "numbers": {}})
    monkeypatch.setattr(tools, "locate", locate)
    monkeypatch.setattr(tools, "fill", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(tools, "verify", lambda *_a, **_k: (None, []))
    monkeypatch.setattr(tools, "check_text", lambda *_a, **_k: None)
    got = agent.printed_toc_agent("bookB", root=str(tmp_path), force=True)
    assert called["n"] == 1
    assert got["cache"] == "miss"


# ---------------------------------------------------------------- arch v2


def test_chat_schema_and_sampling_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict] = []

    def post(_url: str, payload: dict) -> tuple[dict, float]:
        sent.append(payload)
        return {"choices": [{"message": {"content": '{"continues": true}'}}]}, 0.1

    monkeypatch.setattr(tools, "_post_json", post)
    monkeypatch.setattr(tools, "_vision_root", lambda: "http://v")
    tools._chat([], 10)
    data, _ = tools._chat([], 10, schema=decide.PAIR_SCHEMA, sample=True)
    assert "response_format" not in sent[0] and sent[0]["temperature"] == 0
    assert sent[1]["response_format"]["json_schema"]["schema"] == decide.PAIR_SCHEMA
    assert (sent[1]["temperature"], sent[1]["top_p"], sent[1]["top_k"]) == (0.7, 0.8, 20)
    assert data == {"continues": True}


def _wrap_row(text: str, y0: float, real: bool, page: int | None = None) -> dict:
    return {"text": text, "print_page": page, "hole": None, "hole_box": None, "size": 10.0,
            "bold": False, "x0": 10.0, "x1": 200.0, "y0": y0, "y1": y0 + 10, "chars": None,
            "real_size": real}


def test_model_rows_stay_apart_when_model_wraps_off() -> None:
    image = [_wrap_row("Focus on language: Synonyms; phrasal verbs", 0, False), _wrap_row("with out", 12, False)]
    assert len(layout._merge_wrapped([dict(r) for r in image])) == 1
    assert len(layout._merge_wrapped([dict(r) for r in image], model_wraps=False)) == 2
    text = [_wrap_row("第八章 牛顿力学的局限性", 0, True), _wrap_row("与相对论初步", 12, True)]
    assert len(layout._merge_wrapped([dict(r) for r in text], model_wraps=False)) == 1


def _v2_row(text: str, y0: float, page: int | None = None, col: int = 0, level: int = 1) -> dict:
    row = _draft(text, page=page, y0=y0, y1=y0 + 10, level=level)
    row.update(col=col, fact_method="text_layer", x_text=10.0, indent=10.0)
    return row


def test_check_answers_applies_the_facts() -> None:
    rows = [
        _v2_row("第一节 功", 0, page=51),
        _v2_row("率", 12),
        _v2_row("第二节 功率", 24, col=1),
        _v2_row("A B", 36, col=1),
    ]
    answers = {
        1: {"n": 1, "continues": True, "level": 1, "page_printed": True},
        2: {"n": 2, "continues": True, "level": 1, "page_printed": False},
        3: {"n": 3, "continues": True, "level": 1, "page_printed": True},
        4: {"n": 4, "continues": False, "level": 1, "page_printed": False, "split": ["A", "C"]},
    }
    plans = decide.check_answers(rows, answers)
    assert [p["continues"] for p in plans] == [False, False, False, False]
    assert plans[0]["facts"] == ["first_line"]
    assert plans[1]["facts"] == ["prev_has_page"]
    assert plans[2]["facts"] == ["other_column"]
    assert plans[3]["facts"] == ["split_changed_text"] and plans[3]["split"] == []
    missing = decide.check_answers(rows[:1], {})
    assert missing[0]["facts"] == ["unanswered"] and missing[0]["page_printed"] is True
    failed = decide.check_answers(rows[:1], None)
    assert failed[0]["facts"] == ["vision_failed"]


def test_split_keeps_letters_and_page_on_last_part() -> None:
    rows = [_v2_row("Mini-project: A Focus on language: B", 0, page=12)]
    answers = {1: {"n": 1, "continues": False, "level": 2, "page_printed": True,
                   "split": ["Mini-project: A", "Focus on language: B"]}}
    plans = decide.check_answers(rows, answers)
    got = decide.build_entries(rows, plans, {})
    assert [e["text"] for e in got] == ["Mini-project: A", "Focus on language: B"]
    assert [e["print_page"] for e in got] == [None, 12]
    assert [e["decision"]["page_printed"] for e in got] == [False, True]


def test_pair_crop_spans_the_column(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", (1000, 1400), "white").save(buf, format="JPEG")
    monkeypatch.setattr(tools, "page_raster", lambda *_a, **_k: (buf.getvalue(), (1000, 1400)))
    a = _v2_row("Focus on language: Synonyms;", 100)
    b = _v2_row("with out", 112)
    a.update(x0=100.0, x1=300.0)
    b.update(x0=100.0, x1=180.0)
    raw = decide.pair_crop(Path("."), [a, b], a, b, (2.0, 2.0))
    img = Image.open(io.BytesIO(raw))
    assert img.size == (2 * 200 + decide.CROP_PAD + decide.CROP_RIGHT, 2 * 22 + 2 * decide.CROP_PAD)
    a["y0"] = b["y1"] + 100
    assert decide.pair_crop(Path("."), [a, b], a, b, (2.0, 2.0)) is None


def _arbitrate(monkeypatch: pytest.MonkeyPatch, first: bool, votes: list[bool], second: str) -> tuple[bool, dict, int]:
    answers = [first] + votes
    calls = {"n": 0}

    def ask(_crop: bytes, _a: str, _b: str, sample: bool = False) -> tuple[bool, float]:
        calls["n"] += 1
        return answers.pop(0), 0.0

    monkeypatch.setattr(decide, "pair_crop", lambda *_a, **_k: b"jpg")
    monkeypatch.setattr(decide, "ask_pair", ask)
    rows = [_v2_row("Focus on language: Synonyms;", 0), _v2_row(second, 12)]
    joined, record, _ = decide.arbitrate_join(Path("."), rows, 1, (1.0, 1.0))
    return joined, record, calls["n"]


def test_agreement_joins_without_votes(monkeypatch: pytest.MonkeyPatch) -> None:
    joined, record, calls = _arbitrate(monkeypatch, True, [], "with out")
    assert joined and calls == 1 and "votes" not in record


def test_disagreement_goes_to_majority_vote(monkeypatch: pytest.MonkeyPatch) -> None:
    joined, record, calls = _arbitrate(monkeypatch, False, [True, False, True], "with out")
    assert joined and calls == 4 and record["needs_review"] is True
    joined, _, _ = _arbitrate(monkeypatch, False, [False, False, True], "with out")
    assert not joined


def test_new_item_head_needs_every_vote(monkeypatch: pytest.MonkeyPatch) -> None:
    joined, record, calls = _arbitrate(monkeypatch, True, [True, True, False], "Focus on language: B")
    assert record["new_item"] and calls == 4 and not joined
    joined, _, _ = _arbitrate(monkeypatch, True, [True, True, True], "Focus on language: B")
    assert joined


def test_print_style_level_is_never_overruled() -> None:
    assert decide.settle_level(1, None) == (1, "page")
    assert decide.settle_level(2, 2) == (2, "style")
    assert decide.settle_level(None, 2) == (2, "style")
    assert decide.settle_level(1, 2) == (2, "style_kept")


def test_levels_never_skip_going_down() -> None:
    entries = [{"level": 1, "decision": {}}, {"level": 3, "decision": {}}, {"level": 1, "decision": {}}]
    decide.clamp_levels(entries)
    assert [e["level"] for e in entries] == [0, 1, 1]
    assert entries[1]["decision"]["level_clamped_from"] == 3


def test_printed_hole_only_where_the_model_saw_a_page() -> None:
    seen = _v2_row("B Grammar activity", 0)
    seen["decision"] = {"page_printed": True}
    blank = _v2_row("Present continuous passive", 12)
    blank["decision"] = {"page_printed": False}
    decide.mark_printed_holes([seen, blank])
    assert seen["hole"] == "printed" and seen["hole_box"][0] == seen["x1"] + 4
    assert blank["hole"] is None
