from __future__ import annotations

import json
import sys

from app import library
from app.config import settings
from app.workspace import ensure_workspace


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    settings.agent_home = str(home)
    ensure_workspace()
    sys.path.insert(0, str(settings.tools_dir))
    return home


def _write_mineru(book_id: str, blocks: list[dict]) -> None:
    folder = library.book_dir(book_id) / "mineru"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "demo_content_list.json").write_text(
        json.dumps(blocks, ensure_ascii=False),
        encoding="utf-8",
    )


def test_mineru_glued_biology_is_weak(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    import toc_mineru as mineru

    library.create_book(pdf_bytes=b"%PDF", filename="bio.pdf", book_id="bio", label="生物")
    _write_mineru(
        "bio",
        [
            {"type": "title", "page_idx": 5, "text": "目录"},
            {"type": "title", "page_idx": 5, "text": "走进生物学 1"},
            {
                "type": "text",
                "page_idx": 5,
                "text": (
                    "生物学是与人类生活密切相关的自然科学第 1 节 2"
                    "实验探究是学习生物学的重要途径第 2 节 8"
                    "探究 · 实验1-1 用高倍镜观察动植物细胞 12"
                    "细胞是生物体结构的基本单位第 3 节 15"
                ),
            },
        ],
    )
    got = mineru.harvest_mineru_catalog(library.book_dir("bio"))
    assert got["quality"] == "weak"
    assert got["glued_pages"]


def test_mineru_index_list_is_strong(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    import toc_mineru as mineru

    library.create_book(pdf_bytes=b"%PDF", filename="phy.pdf", book_id="idx", label="目录强")
    _write_mineru(
        "idx",
        [
            {
                "type": "index",
                "page_idx": 2,
                "content": {
                    "list_type": "text_list",
                    "list_items": [
                        "第1章 运动的描述 1",
                        "第一节 质点 物理模型 2",
                        "第二节 位移 6",
                    ],
                },
            }
        ],
    )
    got = mineru.harvest_mineru_catalog(library.book_dir("idx"))
    assert got["quality"] == "strong"
    assert got["index_hits"] >= 2
    assert len(got["entries"]) >= 2
    assert len(mineru.usable_catalog_rows(got["entries"])) >= 2


def test_mineru_stub_titles_are_weak(tmp_path, monkeypatch) -> None:
    """生物必修 2 那种：有目录页、四条短标签，但没有『第N章』。"""
    _home(tmp_path, monkeypatch)
    import toc_mineru as mineru

    library.create_book(pdf_bytes=b"%PDF", filename="bio2.pdf", book_id="stub", label="残行")
    _write_mineru(
        "stub",
        [
            {"type": "title", "page_idx": 3, "text": "目录"},
            {"type": "title", "page_idx": 3, "text": "遗传的分子基础 1"},
            {"type": "title", "page_idx": 3, "text": "第 章 2"},
            {"type": "title", "page_idx": 4, "text": "可遗传的变异 57"},
            {"type": "title", "page_idx": 4, "text": "生物的进化 91"},
        ],
    )
    got = mineru.harvest_mineru_catalog(library.book_dir("stub"))
    assert got["toc_pages"]
    assert len(got["entries"]) >= 2
    assert got["quality"] == "weak"
    assert mineru.usable_catalog_rows(got["entries"]) == []
    assert (
        mineru.classify_mineru_quality(
            toc_pages=[4, 5],
            entries=[
                {"label": "遗传的分子基础", "print_page": 1},
                {"label": "第 章", "print_page": 2},
                {"label": "可遗传的变异", "print_page": 57},
                {"label": "生物的进化", "print_page": 91},
            ],
            glued_pages=[],
            index_hits=0,
        )
        == "weak"
    )


def test_trees_match_and_pass_prompts(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    from app import resolve_printed_toc as rpt

    left = [
        {"label": "第1章 走进生物学", "level": 0, "is_chapter": True, "print_page": 1},
        {"label": "第 1 节 细胞", "level": 1, "print_page": 2},
    ]
    right = [
        {"label": "第 1 章 走进生物学", "level": 0, "print_page": 1},
        {"label": "第1节细胞", "level": 1, "print_page": 2},
    ]
    assert rpt.trees_match(left, right)
    assert not rpt.trees_match(left, [{"label": "第2章 别的", "level": 0, "print_page": 1}])

    read_user = rpt.assemble_read_user(toc_pages=[6, 7])
    assert "6" in read_user and "7" in read_user
    assert "走进生物学" not in read_user
    assert "MinerU 清单" not in read_user
    assert "运动的描述" not in read_user

    recon = rpt.assemble_reconcile_user(toc_pages=[6, 7], mineru_entries=left)
    assert "MinerU 清单" in recon
    assert "走进生物学" in recon


def test_run_book_weak_uses_agent_no_second_pass(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    from app import jobs, resolve_printed_toc as rpt, sdk_runner

    library.create_book(pdf_bytes=b"%PDF", filename="bio.pdf", book_id="bio2", label="生物")
    library.patch_book("bio2", status="ready", page_count=8)
    images = library.book_dir("bio2") / "images"
    images.mkdir(parents=True, exist_ok=True)
    for page in range(1, 9):
        (images / f"p{page:04d}.jpg").write_bytes(b"jpeg")
    _write_mineru(
        "bio2",
        [
            {"type": "title", "page_idx": 5, "text": "目录"},
            {
                "type": "text",
                "page_idx": 5,
                "text": "第 1 节 甲 2第 2 节 乙 8第 3 节 丙 15",
            },
        ],
    )
    calls: list[str] = []

    def fake_prompt(prompt, **_k):
        calls.append(prompt)
        if "toc_pages" in prompt and "entries" not in prompt:
            return '{"toc_pages":[6,7]}', "sdk"
        return (
            '{"entries":['
            '{"label":"第1章 走进生物学","level":0,"is_chapter":true,"print_page":1},'
            '{"label":"第 1 节 细胞","level":1,"is_chapter":false,"print_page":2}'
            "]}",
            "sdk",
        )

    monkeypatch.setattr(sdk_runner, "_prompt_sdk", fake_prompt)
    row = jobs.create_job("printed_toc", {"book_id": "bio2"})
    out = rpt.run_book("bio2", row["job_id"])
    assert out["ok"] is True, out.get("error") or out
    assert out["winner"] == "agent"
    assert out["mineru_quality"] == "weak"
    assert out["entry_count"] == 2
    assert "reconcile" not in (out.get("passes") or [])
    read_calls = [text for text in calls if "只根据图抽出" in text]
    assert read_calls
    assert all("MinerU 清单" not in text for text in read_calls)
    assert all("只输出 toc_pages" not in text for text in calls)
    sidecar = json.loads((library.book_dir("bio2") / "toc_printed.json").read_text(encoding="utf-8"))
    assert sidecar["winner"] == "agent"


def test_run_book_strong_mismatch_reconciles(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    from app import jobs, resolve_printed_toc as rpt, sdk_runner

    library.create_book(pdf_bytes=b"%PDF", filename="phy.pdf", book_id="phy", label="物理")
    library.patch_book("phy", status="ready", page_count=4)
    images = library.book_dir("phy") / "images"
    images.mkdir(parents=True, exist_ok=True)
    for page in range(1, 5):
        (images / f"p{page:04d}.jpg").write_bytes(b"jpeg")
    _write_mineru(
        "phy",
        [
            {
                "type": "index",
                "page_idx": 0,
                "content": {
                    "list_items": ["第1章 运动的描述 1", "第一节 质点 2"],
                },
            }
        ],
    )
    calls: list[str] = []

    def fake_prompt(prompt, **kwargs):
        calls.append((prompt, kwargs))
        if "toc_pages" in prompt and "entries" not in prompt:
            return '{"toc_pages":[1]}', "sdk"
        if "MinerU 清单" in prompt:
            assert "运动的描述" in prompt
            assert kwargs.get("workspace_tools") is False
            return (
                '{"toc_pages":[1],"winner":"agent","verdicts":'
                '[{"label":"第1章 运动的描述","action":"keep","note":""}],'
                '"entries":[{"label":"第1章 运动的描述","level":0,"is_chapter":true,"print_page":1},'
                '{"label":"第一节 质点","level":1,"is_chapter":false,"print_page":2}]}',
                "sdk",
            )
        return (
            '{"entries":[{"label":"第1章 运动的描述","level":0,"is_chapter":true,"print_page":1}]}',
            "sdk",
        )

    monkeypatch.setattr(sdk_runner, "_prompt_sdk", fake_prompt)
    row = jobs.create_job("printed_toc", {"book_id": "phy"})
    out = rpt.run_book("phy", row["job_id"])
    assert out["ok"] is True
    assert out["winner"] == "agent"
    assert "reconcile" in (out.get("passes") or [])
    texts = [text for text, _k in calls]
    assert any("MinerU 清单" in text for text in texts)
    assert any("MinerU 清单" not in text and "只根据图抽出" in text for text in texts)


def test_run_book_writes_sidecar_if_reconcile_fails(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    from app import jobs, resolve_printed_toc as rpt, sdk_runner

    library.create_book(pdf_bytes=b"%PDF", filename="phy.pdf", book_id="phy2", label="物理")
    library.patch_book("phy2", status="ready", page_count=4)
    images = library.book_dir("phy2") / "images"
    images.mkdir(parents=True, exist_ok=True)
    for page in range(1, 5):
        (images / f"p{page:04d}.jpg").write_bytes(b"jpeg")
    _write_mineru(
        "phy2",
        [
            {
                "type": "index",
                "page_idx": 0,
                "content": {
                    "list_items": ["第1章 运动的描述 1", "第一节 质点 2"],
                },
            }
        ],
    )

    def fake_prompt(prompt, **_k):
        if "MinerU 清单" in prompt:
            raise ValueError("核对目录失败：闲逛")
        return (
            '{"entries":[{"label":"第1章 运动的描述","level":0,"is_chapter":true,"print_page":1}]}',
            "sdk",
        )

    monkeypatch.setattr(sdk_runner, "_prompt_sdk", fake_prompt)
    row = jobs.create_job("printed_toc", {"book_id": "phy2"})
    out = rpt.run_book("phy2", row["job_id"])
    assert out["ok"] is True
    assert out["winner"] == "agent"
    assert "reconcile" not in (out.get("passes") or [])
    sidecar = json.loads((library.book_dir("phy2") / "toc_printed.json").read_text(encoding="utf-8"))
    assert sidecar["ok"] is True
    assert sidecar["entries"][0]["label"] == "第1章 运动的描述"


def test_run_book_stub_mineru_skips_reconcile(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    from app import jobs, resolve_printed_toc as rpt, sdk_runner

    library.create_book(pdf_bytes=b"%PDF", filename="bio2.pdf", book_id="bio3", label="生物")
    library.patch_book("bio3", status="ready", page_count=6)
    images = library.book_dir("bio3") / "images"
    images.mkdir(parents=True, exist_ok=True)
    for page in range(1, 7):
        (images / f"p{page:04d}.jpg").write_bytes(b"jpeg")
    _write_mineru(
        "bio3",
        [
            {"type": "title", "page_idx": 3, "text": "目录"},
            {"type": "title", "page_idx": 3, "text": "遗传的分子基础 1"},
            {"type": "title", "page_idx": 3, "text": "第 章 2"},
            {"type": "title", "page_idx": 4, "text": "可遗传的变异 57"},
            {"type": "title", "page_idx": 4, "text": "生物的进化 91"},
        ],
    )
    calls: list[str] = []

    def fake_prompt(prompt, **_k):
        calls.append(prompt)
        return (
            '{"entries":['
            '{"label":"第1章 遗传因子的发现","level":0,"is_chapter":true,"print_page":1},'
            '{"label":"第 1 节 孟德尔","level":1,"is_chapter":false,"print_page":2}'
            "]}",
            "sdk",
        )

    monkeypatch.setattr(sdk_runner, "_prompt_sdk", fake_prompt)
    row = jobs.create_job("printed_toc", {"book_id": "bio3"})
    out = rpt.run_book("bio3", row["job_id"])
    assert out["ok"] is True
    assert out["mineru_quality"] == "weak"
    assert out["winner"] == "agent"
    assert "reconcile" not in (out.get("passes") or [])
    assert all("MinerU 清单" not in text for text in calls)


def test_build_toc_sidecar_skips_catalog_parse(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    from test_build_toc import _make_book

    _make_book("phy4x4")
    library.write_page("phy4x4", 1, "目录\n第9章 错的 / 1\n第一节 错节 /2\n")
    (library.book_dir("phy4x4") / "toc_printed.json").write_text(
        json.dumps(
            {
                "ok": True,
                "toc_pages": [1],
                "winner": "agent",
                "mineru_quality": "weak",
                "entries": [
                    {"label": "第1章 运动的描述", "level": 0, "is_chapter": True, "print_page": 1},
                    {"label": "第一节 质点 物理模型", "level": 1, "is_chapter": False, "print_page": 2},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    import build_toc as bt

    called = {"n": 0}
    real = bt._parse_catalog

    def wrapped(*args, **kwargs):
        called["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(bt, "_parse_catalog", wrapped)
    out = bt.build_toc(str(settings.home), "phy4x4")
    assert out["ok"] is True
    assert called["n"] == 0
    labels = [row["label"] for row in library.read_toc("phy4x4")["entries"]]
    assert any(item.startswith("第1章 运动的描述") for item in labels)
    assert all("第9章" not in item for item in labels)


def test_toc_extract_uses_sidecar_not_page_text(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    from app import toc_extract

    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="side", label="侧车")
    library.write_page("side", 1, "目录\n第9章 错的 1\n")
    library.write_page("side", 3, "第1章 集合\n1.1 集合初步\n## 元素\n")
    library.patch_book("side", status="ready", page_count=4)
    (library.book_dir("side") / "toc_printed.json").write_text(
        json.dumps(
            {
                "ok": True,
                "toc_pages": [1],
                "entries": [
                    {"label": "第1章 集合", "level": 0, "is_chapter": True, "print_page": 1},
                    {"label": "1.1 集合初步", "level": 1, "is_chapter": False, "print_page": 2},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cat = toc_extract.read_pdf_catalog("side", 4)
    assert cat["source"] == "printed_sidecar"
    assert cat["text"] == ""
    assert [row["label"] for row in cat["entries"]] == ["第1章 集合", "1.1 集合初步"]
    assert toc_extract.has_printed_catalog("side") is True
    assert toc_extract.locate_toc_pages("side", 4) == [1]


def test_resolve_tool_refuses_nested_chat(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    from app import jobs, resolve_printed_toc as rpt, sdk_runner
    from app.context import current_job_id

    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="nest", label="嵌套")
    library.patch_book("nest", status="ready", page_count=2)

    def boom(*_a, **_k):
        raise AssertionError("chat must not nest Agent.create for printed TOC")

    monkeypatch.setattr(sdk_runner, "_prompt_sdk", boom)
    row = jobs.create_job("chat", {"book_id": "nest", "message": "建目录"})
    jobs._set(row["job_id"], persist=True, status="running")
    token = current_job_id.set(row["job_id"])
    try:
        try:
            rpt.resolve_printed_toc("nest")
        except ValueError as exc:
            assert "看图桥" in str(exc)
        else:
            raise AssertionError("expected ValueError")
    finally:
        current_job_id.reset(token)
