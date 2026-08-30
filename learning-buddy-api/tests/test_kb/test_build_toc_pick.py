from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_builder():
    path = (
        Path(__file__).resolve().parents[3]
        / "data"
        / "agent-home"
        / "tools"
        / "build_toc.py"
    )
    spec = importlib.util.spec_from_file_location("live_build_toc", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_toc_does_not_reuse_another_book(tmp_path) -> None:
    module = _load_builder()
    textbooks = tmp_path / "textbooks"
    other = textbooks / "defaultbook"
    target = textbooks / "newbook"
    (other / "pages").mkdir(parents=True)
    (other / "pages" / "0001.md").write_text("目录\n1.1 集合 2\n", encoding="utf-8")
    (other / "meta.json").write_text(
        json.dumps({"id": "defaultbook", "is_default": True}),
        encoding="utf-8",
    )
    other_toc = {"entries": [{"label": "只属于旧书"}], "print_to_pdf": {}, "toc_pages": []}
    (other / "toc.json").write_text(json.dumps(other_toc), encoding="utf-8")
    target.mkdir(parents=True)
    (target / "toc.json").write_text(
        json.dumps({"entries": [{"label": "新书旧目录"}], "print_to_pdf": {}, "toc_pages": []}),
        encoding="utf-8",
    )
    result = module.build_toc(str(tmp_path), "newbook")
    assert result.get("ok") is False
    assert json.loads((other / "toc.json").read_text(encoding="utf-8"))["entries"][0]["label"] == "只属于旧书"
    assert json.loads((target / "toc.json").read_text(encoding="utf-8"))["entries"][0]["label"] == "新书旧目录"


def test_in_section_heading_is_not_promoted_before_chapter_10(tmp_path) -> None:
    module = _load_builder()
    book = tmp_path / "textbooks" / "book3"
    pages = book / "pages"
    pages.mkdir(parents=True)
    (book / "meta.json").write_text(json.dumps({"id": "book3", "label": "第三册"}), encoding="utf-8")
    (pages / "0001.md").write_text("前言\n", encoding="utf-8")
    (pages / "0002.md").write_text(
        "\n".join(
            [
                "目录",
                "第10章 空间直线与平面",
                "10.1 平面及其基本性质 2",
                "第13章 统计",
                "13.5 统计估计 10",
                "13.6 统计活动 20",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (pages / "0003.md").write_text("10.1 平面及其基本性质\n正文\n", encoding="utf-8")
    (pages / "0010.md").write_text(
        "13.5 统计估计\n2 估计总体的数字特征\n## 2.1 通过样本估计总体的集中趋势\n",
        encoding="utf-8",
    )
    (pages / "0011.md").write_text("## 2.2 通过样本估计总体的离散程度\n", encoding="utf-8")
    (pages / "0020.md").write_text("13.6 统计活动\n", encoding="utf-8")
    result = module.build_toc(str(tmp_path), "book3")
    assert result.get("ok") is True
    toc = json.loads((book / "toc.json").read_text(encoding="utf-8"))
    labels = [row["label"] for row in toc["entries"]]
    assert labels[0] == "前言"
    assert "2.1 通过样本估计总体的集中趋势" not in labels[:3]
    assert labels[1].startswith("第10章")
    idx_135 = labels.index("13.5 统计估计")
    idx_21 = labels.index("2.1 通过样本估计总体的集中趋势")
    idx_22 = labels.index("2.2 通过样本估计总体的离散程度")
    idx_136 = labels.index("13.6 统计活动")
    assert idx_135 < idx_21 < idx_22 < idx_136
    assert toc["entries"][idx_21]["level"] == 2


def test_unnumbered_body_heading_is_not_invented(tmp_path) -> None:
    module = _load_builder()
    book = tmp_path / "textbooks" / "book1"
    pages = book / "pages"
    pages.mkdir(parents=True)
    (book / "meta.json").write_text(json.dumps({"id": "book1"}), encoding="utf-8")
    (pages / "0001.md").write_text(
        "\n".join(["目录", "第1章 集合", "1.1 集合初步 2"]) + "\n",
        encoding="utf-8",
    )
    (pages / "0003.md").write_text("1.1 集合初步\n充分必要条件\n这是正文。\n", encoding="utf-8")
    result = module.build_toc(str(tmp_path), "book1")
    assert result.get("ok") is True
    toc = json.loads((book / "toc.json").read_text(encoding="utf-8"))
    labels = [row["label"] for row in toc["entries"]]
    assert "1.1 集合初步" in labels
    assert not any("充分必要条件" in label for label in labels)
    assert toc.get("source") == "printed"


def test_mineru_toc_pages_override_heuristic(tmp_path) -> None:
    module = _load_builder()
    book = tmp_path / "textbooks" / "book2"
    pages = book / "pages"
    pages.mkdir(parents=True)
    (book / "meta.json").write_text(json.dumps({"id": "book2"}), encoding="utf-8")
    (book / "mineru_toc.json").write_text(json.dumps({"toc_pages": [5], "print_to_pdf": {}}), encoding="utf-8")
    (pages / "0001.md").write_text("前言\n", encoding="utf-8")
    (pages / "0005.md").write_text(
        "第1章 集合\n1.1 集合的含义 2\n",
        encoding="utf-8",
    )
    (pages / "0008.md").write_text("1.1 集合的含义\n正文\n", encoding="utf-8")
    result = module.build_toc(str(tmp_path), "book2")
    assert result.get("ok") is True
    toc = json.loads((book / "toc.json").read_text(encoding="utf-8"))
    assert 5 in toc["toc_pages"]
    labels = [row["label"] for row in toc["entries"]]
    assert any(label.startswith("第1章") for label in labels)
    assert "1.1 集合的含义" in labels


def test_agent_sidecar_merges_level_two(tmp_path) -> None:
    module = _load_builder()
    book = tmp_path / "textbooks" / "book4"
    pages = book / "pages"
    pages.mkdir(parents=True)
    (book / "meta.json").write_text(json.dumps({"id": "book4"}), encoding="utf-8")
    (pages / "0001.md").write_text(
        "\n".join(["目录", "第1章 集合", "1.1 集合初步 2", "1.2 子集 8"]) + "\n",
        encoding="utf-8",
    )
    (pages / "0003.md").write_text("1.1 集合初步\n正文\n", encoding="utf-8")
    (pages / "0004.md").write_text("1 元素与集合\n", encoding="utf-8")
    (pages / "0009.md").write_text("1.2 子集\n", encoding="utf-8")
    (book / "toc_agent.json").write_text(
        json.dumps(
            {
                "source": "printed",
                "skeleton_entries": [],
                "intervals": [
                    {
                        "start_pdf": 3,
                        "end_pdf": 9,
                        "section_label": "1.1 集合初步",
                        "entries": [{"label": "1 元素与集合", "pdf_page": 4}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = module.build_toc(str(tmp_path), "book4")
    assert result.get("ok") is True
    toc = json.loads((book / "toc.json").read_text(encoding="utf-8"))
    labels = [row["label"] for row in toc["entries"]]
    assert "1 元素与集合" in labels
    idx = labels.index("1 元素与集合")
    assert toc["entries"][idx]["level"] == 2
    assert toc["entries"][idx]["pdf_page"] == 4


def test_insert_section_keeps_foreign_chapter_off_the_front() -> None:
    module = _load_builder()
    skeleton = [
        {"kind": "chapter", "label": "第10章 空间直线与平面", "print_page": None},
        {"kind": "section", "label": "10.1 平面", "num": "10.1", "print_page": 2},
    ]
    extra = {
        "kind": "section",
        "label": "2.1 通过样本估计总体的集中趋势",
        "num": "2.1",
        "print_page": 153,
    }
    module._insert_section(skeleton, extra)
    assert skeleton[0]["label"].startswith("第10章")
    assert skeleton[-1]["num"] == "2.1"


def test_lettered_contents_uses_shapes_not_book_titles(tmp_path) -> None:
    module = _load_builder()
    book = tmp_path / "textbooks" / "eng1"
    pages = book / "pages"
    pages.mkdir(parents=True)
    (book / "meta.json").write_text(json.dumps({"id": "eng1"}), encoding="utf-8")
    (pages / "0001.md").write_text(
        "\n".join(
            [
                "Contents",
                "1 Alpha theme",
                "A First skill 6",
                "B Second skill 13",
                "Self-assessment 20",
                "2 Beta theme",
                "A First skill 24",
                "Appendices",
                "Word list 40",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (pages / "0009.md").write_text(
        "A First skill\nB Second skill\nC Third skill\nAlpha theme\n",
        encoding="utf-8",
    )
    (pages / "0016.md").write_text("B Second skill\n", encoding="utf-8")
    (pages / "0023.md").write_text("Self-assessment\n", encoding="utf-8")
    (pages / "0027.md").write_text(
        "A First skill\nB Second skill\nC Third skill\n2 Beta theme\n",
        encoding="utf-8",
    )
    (pages / "0043.md").write_text("Appendices\nWord list\n", encoding="utf-8")
    result = module.build_toc(str(tmp_path), "eng1")
    assert result.get("ok") is True
    toc = json.loads((book / "toc.json").read_text(encoding="utf-8"))
    labels = [row["label"] for row in toc["entries"]]
    assert "1 Alpha theme" in labels
    assert "2 Beta theme" in labels
    assert any(label.startswith("A First skill") for label in labels)
    assert "Self-assessment" in labels
    assert "Appendices" in labels
    assert "Word list" in labels
    assert toc.get("source") == "printed"
