from __future__ import annotations

import importlib.util
from pathlib import Path

from app.mid_text import strip_skin
from app.workspace import ensure_workspace


def _tool():
    here = Path(__file__).resolve()
    candidates = [
        here.parents[1] / "workspace" / "tools" / "tag_chinese_layout.py",
        Path("/data/agent/tools/tag_chinese_layout.py"),
    ]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        raise FileNotFoundError("tag_chinese_layout.py")
    spec = importlib.util.spec_from_file_location("tag_chinese_layout", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _para(text: str, bbox: list[float]) -> dict:
    return {
        "type": "paragraph",
        "content": {"paragraph_content": [{"type": "text", "content": text}]},
        "bbox": bbox,
    }


def test_apply_left_aligned_verse() -> None:
    mod = _tool()
    source = (
        "## 立在地球边上放号$^{①}$\n"
        "\n"
        "郭沫若\n"
        "\n"
        "无数的白云正在空中怒涌，\n"
        "啊啊！好幅壮丽的北冰洋的晴景哟！\n"
        "无限的太平洋提起他全身的力量来要把地球推倒。\n"
        "\n"
        "1919年9、10月间作\n"
    )
    blocks = [
        _para("无数的白云正在空中怒涌，", [267, 247, 507, 267]),
        _para("啊啊！好幅壮丽的北冰洋的晴景哟！", [267, 271, 589, 291]),
        _para("无限的太平洋提起他全身的力量来要把地球推倒。", [268, 296, 709, 316]),
    ]
    out = mod.apply_chinese_layout(
        source,
        blocks=blocks,
        titles={"立在地球边上放号"},
        page_width=1000,
    )
    assert "::: kb-title\n## 立在地球边上放号$^{①}$\n:::" in out
    assert "::: kb-author\n郭沫若\n:::" in out
    assert "::: kb-verse\n" in out
    assert "::: kb-verse-center" not in out
    assert "::: kb-date\n1919年9、10月间作\n:::" in out
    assert "1919年9、10月间作" not in out.split("::: kb-verse")[1].split(":::")[0]
    assert strip_skin(out).strip() == source.strip()


def test_apply_line_centered_verse() -> None:
    mod = _tool()
    source = (
        "## 致云雀$^{③}$\n"
        "\n"
        "雪莱\n"
        "\n"
        "你好啊，欢乐的精灵！\n"
        "\n"
        "你似乎从不是飞禽，\n"
        "\n"
        "从天堂或天堂的邻近，\n"
    )
    blocks = [
        _para("你好啊，欢乐的精灵！", [376, 602, 578, 624]),
        _para("你似乎从不是飞禽，", [394, 627, 576, 650]),
        _para("从天堂或天堂的邻近，", [374, 652, 576, 674]),
    ]
    out = mod.apply_chinese_layout(
        source,
        blocks=blocks,
        titles={"致云雀"},
        page_width=1000,
    )
    assert "::: kb-title\n## 致云雀$^{③}$\n:::" in out
    assert "::: kb-author\n雪莱\n:::" in out
    assert "::: kb-verse-center\n" in out
    body = out.split("::: kb-verse-center")[1].split(":::")[0]
    assert "你好啊，欢乐的精灵！" in body
    assert "从天堂或天堂的邻近，" in body
    assert strip_skin(out).strip() == source.strip()


def test_apply_skips_page_footnotes() -> None:
    mod = _tool()
    source = (
        "曾记否，\n"
        "到中流$^{⑦}$击水$^{⑧}$，\n"
        "浪遏$^{⑨}$飞舟？\n"
        "\n"
        "1925年\n"
        "\n"
        "---\n"
        "\n"
        "⑥〔粪土当年万户侯〕意思是把当时的军阀官僚看得同粪土一样。粪土，视……如粪土，表鄙视。万户侯。\n"
        "\n"
        "⑦〔中流〕江河水流中央。\n"
        "\n"
        "⑧〔击水〕指游泳。\n"
        "\n"
        "⑨〔遏（è）〕阻止。\n"
    )
    blocks = [
        _para("曾记否，", [267, 100, 350, 120]),
        _para("到中流击水，", [267, 124, 400, 144]),
        _para("浪遏飞舟？", [267, 148, 380, 168]),
        _para("⑦〔中流〕江河水流中央。", [80, 700, 280, 720]),
        _para("⑧〔击水〕指游泳。", [80, 724, 200, 744]),
        _para("⑨〔遏（è）〕阻止。", [80, 748, 180, 768]),
    ]
    out = mod.apply_chinese_layout(source, blocks=blocks, page_width=1000)
    verse = out.split("::: kb-verse")[1].split(":::")[0] if "::: kb-verse" in out else ""
    assert "曾记否，" in verse
    assert "浪遏$^{⑨}$飞舟？" in verse
    assert "⑦〔中流〕" not in verse
    assert "⑧〔击水〕" not in verse
    assert "⑨〔遏（è）〕" not in verse
    assert "::: kb-verse\n⑦" not in out
    assert strip_skin(out).strip() == source.strip()


def test_apply_novel_title_author_only() -> None:
    mod = _tool()
    source = (
        "## 百合花$^{①}$\n"
        "\n"
        "茹志鹃\n"
        "\n"
        "1946年的中秋。\n"
        "\n"
        "这天打海岸的部队决定晚上总攻。我们文工团创作室的几个同志就出发了。\n"
    )
    out = mod.apply_chinese_layout(source, titles={"百合花"})
    assert "::: kb-title\n## 百合花$^{①}$\n:::" in out
    assert "::: kb-author\n茹志鹃\n:::" in out
    assert "::: kb-verse" not in out
    assert "::: kb-date" not in out
    assert "1946年的中秋。" in out
    assert strip_skin(out).strip() == source.strip()


def test_apply_groups_composition_dates() -> None:
    mod = _tool()
    source = (
        "快慰。\n"
        "\n"
        "1962年8月2日初稿\n"
        "\n"
        "1983年7月27日删定\n"
        "\n"
        "## 致云雀$^{③}$\n"
        "\n"
        "雪莱\n"
    )
    out = mod.apply_chinese_layout(source, titles={"致云雀"})
    assert "::: kb-date\n1962年8月2日初稿\n\n1983年7月27日删定\n:::" in out
    assert "::: kb-title\n## 致云雀$^{③}$\n:::" in out
    assert strip_skin(out).strip() == source.strip()


def test_gate_skips_math_and_syllabus(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app import library
    from app.tools_loader import load_tool_specs

    settings.agent_home = str(tmp_path / "home")
    ensure_workspace()
    math = library.create_book(
        pdf_bytes=b"%PDF",
        filename="math.pdf",
        book_id="math1",
        label="数学",
        subject="math",
        category="textbook",
    )
    library.write_page(math["id"], 1, "## 集合\n\n正文\n", sync=False)
    syllabus = library.create_book(
        pdf_bytes=b"%PDF",
        filename="yuwen.pdf",
        book_id="syl1",
        label="语文考纲",
        subject="chinese",
        category="syllabus",
    )
    library.write_page(syllabus["id"], 1, "## 沁园春\n\n毛泽东\n", sync=False)
    specs = {s["id"]: s for s in load_tool_specs()}
    assert "tag_chinese_layout" in specs
    skipped_math = specs["tag_chinese_layout"]["execute"]({"book_id": math["id"]})
    skipped_syl = specs["tag_chinese_layout"]["execute"]({"book_id": syllabus["id"]})
    assert skipped_math["skipped"] is True
    assert skipped_syl["skipped"] is True
    assert library.get_page_mid(math["id"], 1) is None


def test_apply_work_kind_holds_across_pages() -> None:
    mod = _tool()
    first = (
        "## 致云雀\n"
        "\n"
        "雪莱\n"
        "\n"
        "你好啊，欢乐的精灵！\n"
        "你似乎从不是飞禽，\n"
        "从天堂或天堂的邻近，\n"
    )
    second = (
        "直到普天下的同情\n"
        "都被未曾留意过的希望和忧虑唤醒；\n"
        "\n"
        "淡淡的紫色黄昏\n"
        "在你航程周围消融。\n"
        "像昼空里的星星。\n"
    )
    boxes = [
        _para("你好啊，欢乐的精灵！", [376, 602, 578, 622]),
        _para("你似乎从不是飞禽，", [394, 627, 576, 647]),
        _para("从天堂或天堂的邻近，", [374, 652, 576, 672]),
        _para("直到普天下的同情", [396, 52, 569, 72]),
        _para("淡淡的紫色黄昏", [324, 250, 500, 270]),
        _para("在你航程周围消融。", [324, 275, 520, 295]),
        _para("像昼空里的星星。", [324, 300, 500, 320]),
    ]
    kind = mod.classify_work(
        [
            "你好啊，欢乐的精灵！",
            "你似乎从不是飞禽，",
            "从天堂或天堂的邻近，",
            "直到普天下的同情",
            "淡淡的紫色黄昏",
            "在你航程周围消融。",
            "像昼空里的星星。",
        ],
        [row["bbox"] for row in boxes],
        1000,
    )
    assert kind == "kb-verse-center"
    first_out = mod.apply_chinese_layout(
        first,
        blocks=boxes[:3],
        titles={"致云雀"},
        page_width=1000,
        work_kinds={"致云雀": kind},
        current_work="致云雀",
    )
    second_out = mod.apply_chinese_layout(
        second,
        blocks=boxes[3:],
        titles={"致云雀"},
        page_width=1000,
        work_kinds={"致云雀": kind},
        current_work="致云雀",
    )
    assert "::: kb-verse-center" in first_out
    assert second_out.startswith("::: kb-verse-center\n直到普天下的同情")
    assert "::: kb-verse\n" not in second_out
    assert strip_skin(second_out).strip() == second.strip()


def test_apply_novel_dialogue_is_not_verse() -> None:
    mod = _tool()
    source = (
        "## 百合花$^{①}$\n"
        "\n"
        "茹志鹃\n"
        "\n"
        "这天打海岸的部队决定晚上总攻。我们文工团创作室的几个同志就出发了。\n"
        "\n"
        "“在家时你干什么？”\n"
        "\n"
        "“帮人拖毛竹。”\n"
        "\n"
        "“你多大了？”\n"
        "\n"
        "“十九。”\n"
    )
    out = mod.apply_chinese_layout(
        source,
        titles={"百合花"},
        blocks=[
            _para("这天打海岸的部队决定晚上总攻。我们文工团创作室的几个同志就出发了。", [80, 80, 820, 140]),
            _para("“在家时你干什么？”", [200, 160, 420, 180]),
            _para("“帮人拖毛竹。”", [200, 184, 380, 204]),
            _para("“你多大了？”", [200, 208, 360, 228]),
            _para("“十九。”", [200, 232, 300, 252]),
        ],
        page_width=1000,
    )
    assert "::: kb-title" in out
    assert "::: kb-author" in out
    assert "::: kb-verse" not in out
    assert "“帮人拖毛竹。”" in out
    assert strip_skin(out).strip() == source.strip()


def test_apply_classical_poems_with_book_source() -> None:
    mod = _tool()
    source = (
        "6\n"
        "\n"
        "## 苯 菖$^{①}$\n"
        "\n"
        "《诗经·周南》\n"
        "\n"
        "采采$^{②}$苯苣，薄言$^{③}$采之。采采苯苣，薄言有$^{④}$之。\n"
        "采采苯苣，薄言掇$^{⑤}$之。采采苯苣，薄言捋$^{⑥}$之。\n"
        "采采苯苣，薄言祜$^{⑦}$之。采采苯苣，薄言襴$^{⑧}$之。\n"
        "\n"
        "## 插秧歌$^{⑨}$\n"
        "\n"
        "杨万里\n"
        "\n"
        "田夫抛秧田妇接，小儿拔秧大儿插。\n"
        "笠是兜鍪$^{⑩}$蓑是甲，雨从头上湿到胛$^{⑪}$。\n"
        "唤渠$^{⑫}$朝餐歇半霎$^{⑬}$，低头折腰只不答：\n"
        "“秧根未牢莳未匝$^{⑭}$，照管$^{⑮}$鹅儿与雏鸭。”\n"
        "\n"
        "本单元特别选入这两首描写劳动的古诗，从中可以读到古人的日常生活与劳作场景，感受他们劳动时的欢乐与情趣。\n"
    )
    out = mod.apply_chinese_layout(
        source,
        titles={"芣苢", "插秧歌"},
        page_width=1000,
    )
    assert "::: kb-title\n## 苯 菖$^{①}$\n:::" in out
    assert "::: kb-author\n《诗经·周南》\n:::" in out
    assert "::: kb-title\n## 插秧歌$^{⑨}$\n:::" in out
    assert "::: kb-author\n杨万里\n:::" in out
    assert out.count("::: kb-verse\n") >= 2
    first = out.split("::: kb-verse\n")[1].split(":::")[0]
    second = out.split("::: kb-verse\n")[2].split(":::")[0]
    assert "采采$^{②}$苯苣" in first
    assert "田夫抛秧田妇接" in second
    assert "本单元特别选入" not in first
    assert "本单元特别选入" not in second
    assert strip_skin(out).strip() == source.strip()


def test_apply_two_poems_on_one_page_keep_own_kind() -> None:
    mod = _tool()
    source = (
        "山海。石砾不时滑坡，\n"
        "引动棕色深渊自上而下的一派嚣鸣，\n"
        "像军旅远去的喊杀声。\n"
        "\n"
        "## 致云雀$^{③}$\n"
        "\n"
        "雪莱\n"
        "\n"
        "你好啊，欢乐的精灵！\n"
        "你似乎从不是飞禽，\n"
        "从天堂或天堂的邻近，\n"
    )
    out = mod.apply_chinese_layout(
        source,
        titles={"峨日朵雪峰之侧", "致云雀"},
        blocks=[
            _para("山海。石砾不时滑坡，", [317, 54, 517, 74]),
            _para("引动棕色深渊自上而下的一派嚣鸣，", [317, 79, 640, 99]),
            _para("像军旅远去的喊杀声。", [319, 104, 519, 124]),
            _para("你好啊，欢乐的精灵！", [376, 602, 578, 622]),
            _para("你似乎从不是飞禽，", [394, 627, 576, 647]),
            _para("从天堂或天堂的邻近，", [374, 652, 576, 672]),
        ],
        page_width=1000,
        work_kinds={"峨日朵雪峰之侧": "kb-verse", "致云雀": "kb-verse-center"},
        current_work="峨日朵雪峰之侧",
    )
    left = out.split("::: kb-verse")[1].split(":::")[0]
    center = out.split("::: kb-verse-center")[1].split(":::")[0]
    assert "山海。石砾不时滑坡，" in left
    assert "你好啊，欢乐的精灵！" not in left
    assert "你好啊，欢乐的精灵！" in center
    assert "山海。石砾不时滑坡，" not in center
    assert strip_skin(out).strip() == source.strip()


def test_tool_writes_mid_for_chinese_textbook(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app import library
    from app.tools_loader import load_tool_specs

    settings.agent_home = str(tmp_path / "home")
    ensure_workspace()
    book = library.create_book(
        pdf_bytes=b"%PDF",
        filename="yuwen.pdf",
        book_id="cn1",
        label="语文必修",
        subject="chinese",
        category="textbook",
    )
    source = "## 致云雀\n\n雪莱\n\n你好啊，欢乐的精灵！\n你似乎从不是飞禽，\n"
    library.write_page(book["id"], 1, source, sync=False)
    library.write_toc(
        book["id"],
        {
            "entries": [
                {
                    "label": "*致云雀 / 雪莱",
                    "level": 2,
                    "pdf_page": 1,
                    "print_page": 1,
                    "is_chapter": False,
                }
            ]
        },
    )
    specs = {s["id"]: s for s in load_tool_specs()}
    out = specs["tag_chinese_layout"]["execute"]({"book_id": book["id"]})
    assert out["skipped"] is False
    assert out["changed"] == 1
    mid = library.get_page_mid(book["id"], 1) or ""
    assert "::: kb-title\n## 致云雀\n:::" in mid
    assert "::: kb-author\n雪莱\n:::" in mid
    assert "::: kb-verse" in mid
    assert strip_skin(mid).strip() == source.strip()
    assert library.get_page_markdown(book["id"], 1) == source


def test_apply_skips_cover_imprint() -> None:
    mod = _tool()
    source = (
        "普通高中教科书\n"
        "\n"
        "## 语文\n"
        "\n"
        "必修\n"
        "\n"
        "上册\n"
        "\n"
        "教育部组织编写\n"
    )
    out = mod.apply_chinese_layout(source, titles={"沁园春·长沙"})
    assert "::: kb-verse" not in out
    assert "::: kb-title" not in out
    assert "## 语文" in out
    assert strip_skin(out).strip() == source.strip()


def test_apply_does_not_center_numbered_or_pedagogy() -> None:
    mod = _tool()
    source = (
        "## 活动提示\n"
        "\n"
        "先看材料。\n"
        "\n"
        "## （1）要开调查会作讨论式的调查\n"
        "\n"
        "只有这样才能近于正确。\n"
    )
    out = mod.apply_chinese_layout(source, titles={"百合花"})
    assert "::: kb-title" not in out
    assert "::: kb-section\n## （1）要开调查会作讨论式的调查\n:::" in out
    assert "## 活动提示" in out
    assert "::: kb-title\n## 活动提示" not in out
    assert strip_skin(out).strip() == source.strip()


def test_apply_section_center_from_bbox() -> None:
    mod = _tool()
    source = (
        "## 喜看稻菽千重浪$^{①}$\n"
        "\n"
        "## ——记首届国家最高科技奖获得者袁隆平\n"
        "\n"
        "沈英甲\n"
        "\n"
        "袁隆平走进稻田，后面我们将看到他对真理的发现使他不可避免地要向权威结论发起挑战。\n"
        "\n"
        "## 创新是科学家的灵魂和本质\n"
        "\n"
        "有人说，袁隆平具有敢于挑战的勇气和信心，他在决定选择水稻杂种优势利用作为自己的攻关方向时并不知道前人已经失败。\n"
    )
    blocks = [
        {
            "type": "title",
            "content": {"title_content": [{"type": "text", "content": "喜看稻菽千重浪①"}]},
            "bbox": [436, 115, 709, 145],
        },
        {
            "type": "title",
            "content": {"title_content": [{"type": "text", "content": "——记首届国家最高科技奖获得者袁隆平"}]},
            "bbox": [357, 167, 786, 189],
        },
        {
            "type": "title",
            "content": {"title_content": [{"type": "text", "content": "创新是科学家的灵魂和本质"}]},
            "bbox": [448, 217, 697, 236],
        },
        _para(
            "袁隆平走进稻田，后面我们将看到他对真理的发现使他不可避免地要向权威结论发起挑战。",
            [92, 250, 820, 320],
        ),
        _para(
            "有人说，袁隆平具有敢于挑战的勇气和信心，他在决定选择水稻杂种优势利用作为自己的攻关方向时并不知道前人已经失败。",
            [92, 400, 820, 470],
        ),
    ]
    out = mod.apply_chinese_layout(
        source,
        titles={"喜看稻菽千重浪——记首届国家最高科技奖获得者袁隆平"},
        blocks=blocks,
        page_width=890,
    )
    assert "::: kb-title\n## 喜看稻菽千重浪$^{①}$\n:::" in out
    assert "::: kb-subtitle\n## ——记首届国家最高科技奖获得者袁隆平\n:::" in out
    assert "::: kb-author\n沈英甲\n:::" in out
    assert "::: kb-section-center\n## 创新是科学家的灵魂和本质\n:::" in out
    assert "::: kb-title\n## 创新是科学家的灵魂和本质" not in out
    assert strip_skin(out).strip() == source.strip()
