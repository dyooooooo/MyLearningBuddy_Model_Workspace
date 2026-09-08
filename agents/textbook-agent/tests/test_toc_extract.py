from __future__ import annotations

import json
from pathlib import Path

from app import toc_extract

GOLDENS = Path(__file__).resolve().parent / "goldens" / "toc_extract"


def _load(name: str) -> dict:
    return json.loads((GOLDENS / name).read_text(encoding="utf-8"))


def test_validate_interval_goldens() -> None:
    for name in ("ok.json", "oob.json", "skip.json"):
        case = _load(name)
        rows = toc_extract._as_rows(toc_extract.parse_json_payload(case["reply"]))
        got = toc_extract.validate_interval_entries(rows, case["start_pdf"], case["end_pdf"])
        if case["expected"] is None:
            assert got is None
            continue
        assert got is not None
        assert [row["label"] for row in got] == [row["label"] for row in case["expected"]]
        assert [row["pdf_page"] for row in got] == [row["pdf_page"] for row in case["expected"]]


def test_monotonic_pages_rejected() -> None:
    rows = [
        {"label": "1 后", "pdf_page": 14},
        {"label": "2 前", "pdf_page": 12},
    ]
    assert toc_extract.validate_interval_entries(rows, 10, 16) is None


def test_sibling_intervals() -> None:
    entries = [
        {"label": "第1章", "level": 0, "pdf_page": 7, "is_chapter": True},
        {"label": "1.1 集合", "level": 1, "pdf_page": 8},
        {"label": "1.2 子集", "level": 1, "pdf_page": 14},
        {"label": "第2章", "level": 0, "pdf_page": 20, "is_chapter": True},
    ]
    intervals = toc_extract.sibling_intervals(entries)
    assert [item["key"] for item in intervals] == ["8-14", "14-20"]


def test_format_catalog_shows_body_toc() -> None:
    text = toc_extract.format_catalog(
        [
            {"label": "第1部分 案例", "level": 0, "pdf_page": 10},
            {"label": "1 刹车距离", "level": 1, "pdf_page": 11},
            {"label": "提出问题", "level": 2, "pdf_page": 11},
        ]
    )
    assert "第1部分 案例  p10" in text
    assert "1 刹车距离  p11" in text
    assert "提出问题" not in text


def test_extract_pdf_page_prefers_cleaner_markdown(tmp_path, monkeypatch) -> None:
    library = _book_home(tmp_path, monkeypatch, "mix", "对照")
    library.write_page("mix", 6, "目录\n第1章 集合\n1.1 集合初步 2\n1.2 子集 4\n")
    monkeypatch.setattr(toc_extract, "_pdf_text_only", lambda *_a, **_k: "目 录\n附录１写作 ３ ８附录２样例")
    text = toc_extract.extract_pdf_page("mix", 6)
    assert "1.1 集合初步 2" in text


def test_locate_toc_pages_from_mulu(tmp_path, monkeypatch) -> None:
    library = _book_home(tmp_path, monkeypatch, "tocp", "目录页")
    library.write_page("tocp", 1, "封面\n")
    library.write_page("tocp", 2, "目录\n第1章 集合\n1.1 集合初步 2\n")
    library.patch_book("tocp", status="ready", page_count=2)
    assert toc_extract.locate_toc_pages("tocp", 2) == [2]


def test_merge_catalog_pdf_wins_toc_fills_page() -> None:
    pdf_entries = [
        {"label": "1 刹车距离", "level": 1, "pdf_page": 0, "print_page": 4},
        {"label": "2 易拉罐的设计", "level": 1, "pdf_page": 0, "print_page": 8},
    ]
    toc_entries = [
        {"label": "前言", "level": 0, "pdf_page": 4, "is_chapter": True},
        {"label": "1 刹车距离", "level": 1, "pdf_page": 11, "print_page": 4},
    ]
    merged = toc_extract.merge_catalog(pdf_entries, toc_entries)
    labels = [row["label"] for row in merged]
    assert "1 刹车距离" in labels
    assert "2 易拉罐的设计" in labels
    assert "前言" in labels
    assert next(row["pdf_page"] for row in merged if row["label"] == "1 刹车距离") == 11


def test_scan_unnumbered_activity_headings(tmp_path, monkeypatch) -> None:
    """无编号小节要被采纳，必须是 ATX 标题（`## 建立模型`），纯正文行不算。

    「提出问题」已进 configs/toc_lexicon.yaml 的栏目停用词（物理书里它是「学生实验」
    栏目的固定小标题，出现几十次），所以即便写成 ATX 标题也不会进目录。
    """
    library = _book_home(tmp_path, monkeypatch, "act", "活动")
    library.write_page("act", 11, "1 刹车距离\n## 提出问题\n汽车刹车后…\n## 建立模型\n")
    library.write_page("act", 12, "## 求解模型\n")
    kids = toc_extract.scan_interval_entries("act", 11, 13, "1 刹车距离", set())
    assert [row["label"] for row in kids] == ["建立模型", "求解模型"]


def test_interval_hash_tracks_extraction_rules(tmp_path, monkeypatch) -> None:
    """区间缓存必须跟着规则失效，否则改了停用词重跑也纹丝不动。

    缓存原来只按页文本哈希命中：往 configs/toc_lexicon.yaml 加一个栏目停用词、
    或收紧无编号小节的判定，正文没变就直接复用上一轮结果 —— 看着像「改了没用」。
    """
    texts = ["## 学生实验\n正文\n"]
    before = toc_extract.interval_hash(texts)
    assert before != toc_extract.page_hash(texts)

    monkeypatch.setattr(toc_extract, "rules_stamp", lambda: "v99|other")
    assert toc_extract.interval_hash(texts) != before


def test_scan_skips_plain_body_sentences(tmp_path, monkeypatch) -> None:
    """UNNUMBERED 不再收正文短句 —— 物理必修第1册就是被这些行灌满了小节。"""
    library = _book_home(tmp_path, monkeypatch, "phy2", "物理")
    library.write_page(
        "phy2",
        30,
        "也可用等式表示为\n得飞机起飞阶段所需的最短时间\n数据分析\n## 真正的小节\n",
    )
    kids = toc_extract.scan_interval_entries("phy2", 30, 31, "第一节 加速度", set())
    assert [row["label"] for row in kids] == ["真正的小节"]


def test_scan_atx_headings_skip_column_labels(tmp_path, monkeypatch) -> None:
    library = _book_home(tmp_path, monkeypatch, "phy", "物理")
    library.write_page(
        "phy",
        20,
        "## 第一节 质点 物理模型\n\n## 何种条件下可将物体抽象为质点？\n\n## 自主活动\n\n正文。\n",
    )
    kids = toc_extract.scan_interval_entries("phy", 20, 21, "第一节 质点 物理模型", set())
    assert [row["label"] for row in kids] == ["何种条件下可将物体抽象为质点？"]
    assert kids[0]["level"] == 2


def test_parse_book_payload() -> None:
    payload = toc_extract.parse_book_payload(
        '{"skeleton_entries":[],"intervals":[{"start_pdf":11,"section_label":"1 刹车距离",'
        '"entries":[{"label":"提出问题","pdf_page":11}]}]}'
    )
    assert payload["intervals"][0]["section_label"] == "1 刹车距离"
    assert toc_extract.parse_book_payload("[]") == {"skeleton_entries": [], "intervals": []}


def _book_home(tmp_path, monkeypatch, book_id: str, label: str):
    from app import jobs, library
    from app.config import settings
    from app.workspace import ensure_workspace

    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    settings.agent_home = str(home)
    toc_extract._builder_mod = None
    jobs.reset_for_tests()
    ensure_workspace()
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id=book_id, label=label)
    return library


def test_run_book_scans_without_nested_agent(tmp_path, monkeypatch) -> None:
    from app import jobs, sdk_runner

    library = _book_home(tmp_path, monkeypatch, "b1", "演示")
    library.write_page("b1", 1, "目录\n第1章 集合\n1.1 集合初步 2\n1.2 子集 4\n")
    library.write_page("b1", 3, "1.1 集合初步\n1 元素\n")
    library.write_page("b1", 5, "1.2 子集\n")
    library.patch_book("b1", status="ready", page_count=5)
    (library.book_dir("b1") / "mineru_toc.json").write_text(
        json.dumps({"toc_pages": [1], "print_to_pdf": {}}),
        encoding="utf-8",
    )

    calls = {"n": 0}

    def fake_prompt(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("printed catalog should not open an extra agent")

    monkeypatch.setattr(sdk_runner, "_prompt_sdk", fake_prompt)
    row = jobs.create_job("toc_extract", {"book_id": "b1"})
    first = toc_extract.run_book("b1", row["job_id"])
    assert first["extracted"] >= 1
    assert first["source"] == "printed"
    assert first["llm"] == ""
    assert first["pdf_toc_pages"] == [1]
    details = [str(step.get("detail") or "") for step in (jobs.get_job(row["job_id"]) or {}).get("trace") or []]
    assert any(text.startswith("先看原文 PDF 目录页") for text in details)
    assert details.index(next(text for text in details if text.startswith("先看原文 PDF"))) < details.index(
        next(text for text in details if text.startswith("再读 toc.json"))
    )
    sidecar = json.loads((library.book_dir("b1") / "toc_agent.json").read_text(encoding="utf-8"))
    assert sidecar["intervals"]
    assert calls["n"] == 0
    second = toc_extract.run_book("b1", row["job_id"])
    assert second["reused"] >= 1
    assert calls["n"] == 0
    toc = library.read_toc("b1")
    labels = [item["label"] for item in toc["entries"]]
    assert "1 元素" in labels


def test_no_printed_catalog_scans_skeleton(tmp_path, monkeypatch) -> None:
    from app import jobs, sdk_runner

    library = _book_home(tmp_path, monkeypatch, "b2", "无目录")
    library.write_page("b2", 1, "前言\n")
    library.write_page("b2", 3, "第1章 集合\n1.1 集合初步\n")
    library.write_page("b2", 6, "1.2 子集\n")
    library.patch_book("b2", status="ready", page_count=6)
    toc_extract.run_workspace_toc("b2")
    assert toc_extract.has_printed_catalog("b2") is False

    def fake_prompt(*_a, **_k):
        raise AssertionError("body headings should build the skeleton without an extra agent")

    monkeypatch.setattr(sdk_runner, "_prompt_sdk", fake_prompt)
    row = jobs.create_job("toc_extract", {"book_id": "b2"})
    out = toc_extract.run_book("b2", row["job_id"])
    assert out["source"] == "agent"
    assert out["skeleton"] == 3
    assert out["llm"] == ""
    toc = library.read_toc("b2")
    assert toc.get("source") == "agent"
    labels = [item["label"] for item in toc["entries"]]
    assert any(label.startswith("第1章") for label in labels)
    assert "1.1 集合初步" in labels


def test_chat_job_does_not_open_bridge(tmp_path, monkeypatch) -> None:
    from app import jobs, sdk_runner

    library = _book_home(tmp_path, monkeypatch, "b3", "对话")
    library.write_page("b3", 1, "目录\n第1章 集合\n1.1 集合初步 2\n")
    library.write_page("b3", 3, "1.1 集合初步\n这段没有独立小标题，只有说明文字。\n")
    library.patch_book("b3", status="ready", page_count=3)
    (library.book_dir("b3") / "mineru_toc.json").write_text(
        json.dumps({"toc_pages": [1], "print_to_pdf": {}}),
        encoding="utf-8",
    )

    def fake_prompt(*_a, **_k):
        raise AssertionError("chat must not nest Agent.create")

    monkeypatch.setattr(sdk_runner, "_prompt_sdk", fake_prompt)
    row = jobs.create_job("chat", {"book_id": "b3", "message": "抽目录"})
    out = toc_extract.run_book("b3", row["job_id"])
    assert out["llm"] == "skipped_nested"
    assert (library.book_dir("b3") / "toc_agent.json").is_file()


def test_empty_job_id_skips_nested_when_chat_running(tmp_path, monkeypatch) -> None:
    from app import jobs, sdk_runner

    library = _book_home(tmp_path, monkeypatch, "b5", "回调")
    library.write_page("b5", 1, "目录\n第1章 集合\n1.1 集合初步 2\n")
    library.write_page("b5", 3, "1.1 集合初步\n这段没有独立小标题，只有说明文字。\n")
    library.patch_book("b5", status="ready", page_count=3)
    (library.book_dir("b5") / "mineru_toc.json").write_text(
        json.dumps({"toc_pages": [1], "print_to_pdf": {}}),
        encoding="utf-8",
    )

    def fake_prompt(*_a, **_k):
        raise AssertionError("tool callback must not nest Agent.create")

    monkeypatch.setattr(sdk_runner, "_prompt_sdk", fake_prompt)
    chat = jobs.create_job("chat", {"message": "抽目录"})
    jobs._set(chat["job_id"], persist=True, status="running")
    out = toc_extract.run_book("b5", "")
    assert out["llm"] == "skipped_nested"
    assert toc_extract.holds_foreign_bridge("") is True


def test_standalone_asks_once_for_whole_book(tmp_path, monkeypatch) -> None:
    from app import jobs, sdk_runner

    library = _book_home(tmp_path, monkeypatch, "b4", "一次问")
    library.write_page("b4", 1, "目录\n第1章 集合 1\n1.1 集合初步 2\n")
    library.write_page("b4", 3, "1.1 集合初步\n这里没有可用标题行，只有一段说明。\n")
    library.patch_book("b4", status="ready", page_count=3)
    (library.book_dir("b4") / "mineru_toc.json").write_text(
        json.dumps({"toc_pages": [1], "print_to_pdf": {}}),
        encoding="utf-8",
    )
    calls = {"n": 0, "prompt": ""}

    def fake_prompt(prompt, **_k):
        calls["n"] += 1
        calls["prompt"] = prompt
        return (
            '{"skeleton_entries":[],"intervals":['
            '{"start_pdf":3,"section_label":"1.1 集合初步",'
            '"entries":[{"label":"1 元素","pdf_page":3}]}]}'
        ), "sdk"

    monkeypatch.setattr(sdk_runner, "_prompt_sdk", fake_prompt)
    row = jobs.create_job("toc_extract", {"book_id": "b4"})
    out = toc_extract.run_book("b4", row["job_id"])
    assert calls["n"] == 1
    assert "先看原文 PDF 目录页读出的目录" in calls["prompt"]
    assert out["llm"] == "once"
    toc = library.read_toc("b4")
    assert "1 元素" in [item["label"] for item in toc["entries"]]
