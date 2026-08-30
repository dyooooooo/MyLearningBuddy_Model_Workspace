from __future__ import annotations

import json
import re
from pathlib import Path
from statistics import median

CN_NUM_TOKEN = r"\d+|[一二三四五六七八九十两零百]+"
CHAPTER_LINE = re.compile(rf"^第\s*({CN_NUM_TOKEN})\s*章\s+(.+)$")
PART_LINE = re.compile(rf"^第\s*({CN_NUM_TOKEN})\s*部分(?:\s+(.+))?$")
TOC_DOT3 = re.compile(r"^(\*?)(\d+\.\d+\.\d+)(?!\.\d)\s+(.+?)\s+(\d+)\s*$")
TOC_SECTION = re.compile(r"^(\*?)(\d+\.\d+)(?!\.\d)\s+(.+?)\s+(\d+)\s*$")
TOC_NAMED = re.compile(r"^(内容提要|复习题)\s+(\d+)\s*$")
TOC_SIMPLE_SUB = re.compile(r"^(\*?)([1-9]\d{0,1})\s+(.+?)\s+(\d+)\s*$")
INTRO_LINE = re.compile(r"^引\s*论(?:\s+(\d+))?$")
APPENDIX_HEAD = re.compile(r"^附\s*录$")
APPENDIX_ITEM = re.compile(r"^附录\s*(\d+)\s+(.+?)\s+(\d+)\s*$")
BODY_DOT3 = re.compile(r"^(\*?)(\d+\.\d+\.\d+)(?!\.\d)\s+(.+?)$")
BODY_SECTION = re.compile(r"^(\*?)(\d+\.\d+)(?!\.\d)(?:\s+(.+))?$")
SUBSECTION = re.compile(
    r"^(\*?)([1-9]\d{0,1})\s+([一-鿿]"
    r"[一-鿿A-Za-z0-9、与及的之至和（）()·—－\-《》]{0,40})$"
)
UNNUMBERED = re.compile(
    r"^([一-鿿][一-鿿、与及的之至和（）() ]{2,36})$"
)
SENTENCE_PUNCT = re.compile(r"[。．.!?！？：:;；，,]")
NAMED_HEADING = re.compile(r"^(前言|后记|目录|目 录|内容提要|复习题|引论|附录)$")
FRONT = re.compile(r"^前\s*言$")
BACK = re.compile(r"^后\s*记$")
LATIN_UNIT_LINE = re.compile(r"^(\d+)\s+([A-Z][A-Za-z][A-Za-z0-9’' \-]*)$")
LATIN_GROUP_BARE = re.compile(r"^[A-Z][A-Za-z]+(?:[ \-][A-Za-z]+){0,4}$")
LETTER_SECTION = re.compile(r"^([A-E])\s+(.+?)\s+(\d+)$")
LETTER_BODY_SECTION = re.compile(r"^([A-E])\s+([A-Za-z].+)$")
SELF_ASSESS = re.compile(r"^Self-assessment(?:\s+(\d+))?$", re.I)
LATIN_NAMED_PAGE = re.compile(r"^([A-Z][A-Za-z][A-Za-z0-9’' \-]*)\s+(\d+)$")
OCR_GROUP_LETTER = {
    "犃": "A",
    "犅": "B",
    "犄": "C",
    "Ａ": "A",
    "Ｂ": "B",
    "Ｃ": "C",
}
CN_DIGIT = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
ATX = re.compile(r"^#{1,6}\s+")
GLUED_MATH = re.compile(r"^(\*?\d+(?:\.\d+)*)\s*\$\$\s+")
LATEX_PLAIN = (
    (r"\sin", "sin"),
    (r"\cos", "cos"),
    (r"\tan", "tan"),
    (r"\omega", "ω"),
    (r"\varphi", "φ"),
    (r"\phi", "φ"),
    (r"\pi", "π"),
)
TOC_NAV = re.compile(r"<nav\b[^>]*class=\"[^\"]*kb-page-toc[\s\S]*?</nav>", re.I)
HTML_TOC_TITLE = re.compile(r'kb-page-toc-title">([^<]*)</span>')
HTML_TOC_PAGE = re.compile(r'kb-page-toc-page">(\d+)</span>')
HTML_TOC_GOTO = re.compile(r'data-goto-print-page="(\d+)"')
HTML_TOC_LI = re.compile(r"<li\b[^>]*>(.*?)</li>", re.I | re.S)

SKIP_UNNUMBERED = {
    "内容提要",
    "复习题",
    "前言",
    "后记",
    "引论",
    "附录",
    "目录",
    "目 录",
    "上海教育出版社",
    "拓展与思考",
    "证明",
    "解",
    "性质",
    "定理",
}
SKIP_PREFIXES = ("证明", "定理", "例", "练习", "解")


def _pdf_from_name(path: Path) -> int:
    try:
        return int(path.stem)
    except ValueError:
        return 0


def _html_toc_to_lines(block: str) -> str:
    """把 page-body 抬升后的目录 HTML 还原成「标题 印张页」文本行。"""
    rows: list[str] = []
    for item in HTML_TOC_LI.finditer(block):
        title_m = HTML_TOC_TITLE.search(item.group(1))
        if not title_m:
            continue
        title = re.sub(r"\s+", " ", title_m.group(1)).strip()
        if not title:
            continue
        page_m = HTML_TOC_PAGE.search(item.group(1)) or HTML_TOC_GOTO.search(item.group(0))
        rows.append(f"{title} {page_m.group(1)}" if page_m else title)
    return "\n".join(rows)


def _flatten_markup(text: str) -> str:
    if "kb-page-toc" in (text or ""):
        text = TOC_NAV.sub(lambda m: "\n" + _html_toc_to_lines(m.group(0)) + "\n", text)
    return text


def _plain_heading(text: str) -> str:
    """目录侧不渲染公式，把标题里的 $...$ 收成可读文本。"""
    text = re.sub(r"\$+", "", text or "")
    for src, dst in LATEX_PLAIN:
        text = text.replace(src, dst)
    text = re.sub(r"\\[a-zA-Z]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _num_key(num: str) -> tuple[int, ...]:
    return tuple(int(part) for part in (num or "").split(".") if part.isdigit())


def _clean_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in _flatten_markup(text).splitlines():
        line = ATX.sub("", raw.strip()).strip()
        line = re.sub(r"^\$\$\s+", "", line)
        line = GLUED_MATH.sub(r"\1 ", line)
        if not line:
            continue
        if line.startswith("![") or line.startswith("{'type'") or line.startswith("*{'type'"):
            continue
        lines.append(re.sub(r"\s+", " ", line))
    return lines


def _is_contents_title(line: str) -> bool:
    compact = _norm_title(line).lower()
    return compact in {"目录", "contents"} or (line or "").strip() in {"目录", "目 录", "Contents", "CONTENTS"}


def _is_self_heading(line: str) -> bool:
    return _norm_title(line) == "selfassessment"


def _is_latin_group_bare(line: str) -> bool:
    text = (line or "").strip()
    if not LATIN_GROUP_BARE.match(text):
        return False
    if _is_contents_title(text) or _is_self_heading(text):
        return False
    return True


def _is_latin_unit_line(line: str) -> bool:
    text = (line or "").strip()
    return bool(LATIN_UNIT_LINE.match(text) or _is_latin_group_bare(text))


def _is_toc_page(lines: list[str]) -> bool:
    if not lines:
        return False
    if _is_contents_title(lines[0]):
        return True
    hits = 0
    en_hits = 0
    for line in lines:
        if (
            CHAPTER_LINE.match(line)
            or PART_LINE.match(line)
            or TOC_DOT3.match(line)
            or TOC_SECTION.match(line)
            or TOC_NAMED.match(line)
            or INTRO_LINE.match(line)
            or APPENDIX_HEAD.match(line)
            or APPENDIX_ITEM.match(line)
        ):
            hits += 1
        if (
            _is_latin_unit_line(line)
            or LETTER_SECTION.match(line)
            or SELF_ASSESS.match(line)
            or LATIN_NAMED_PAGE.match(line)
        ):
            en_hits += 1
    if en_hits >= 4 and en_hits * 2 >= len(lines):
        return True
    return hits >= 3 and hits * 2 >= len(lines)


def _norm_title(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _as_int(token: str) -> int | None:
    raw = (token or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if raw.startswith("十") and len(raw) == 2:
        tail = CN_DIGIT.get(raw[1])
        return 10 + tail if tail is not None else None
    if raw.endswith("十") and len(raw) == 2:
        head = CN_DIGIT.get(raw[0])
        return head * 10 if head else None
    if "十" in raw and len(raw) == 3:
        head = CN_DIGIT.get(raw[0])
        tail = CN_DIGIT.get(raw[2])
        if head is not None and tail is not None:
            return head * 10 + tail
    return CN_DIGIT.get(raw)


def _part_num_from_label(label: str) -> int | None:
    match = PART_LINE.match(label or "") or re.match(
        rf"^第\s*({CN_NUM_TOKEN})\s*部分", label or ""
    )
    return _as_int(match.group(1)) if match else None


def _part_label(num: int, title: str = "") -> str:
    title = (title or "").strip()
    return f"第{num}部分 {title}".strip() if title else f"第{num}部分"


def _fix_group_title(title: str) -> str:
    text = title or ""
    for src, dst in OCR_GROUP_LETTER.items():
        text = text.replace(src, dst)
    text = re.sub(r"(数学建模活动)\s*([A-Z])\s*$", r"\1 \2", text.strip())
    return re.sub(r"\s+", " ", text).strip()


def _is_intro_heading(line: str) -> bool:
    return _norm_title(line) == "引论" or bool(INTRO_LINE.match(line or ""))


def _is_appendix_heading(line: str) -> bool:
    compact = _norm_title(line)
    return compact == "附录" or compact.lower() == "appendices" or bool(APPENDIX_HEAD.match(line or ""))


def _is_front_heading(line: str) -> bool:
    return _norm_title(line) == "前言" or bool(FRONT.match(line or ""))


def _is_back_heading(line: str) -> bool:
    return _norm_title(line) == "后记" or bool(BACK.match(line or ""))


def _section_num(label: str) -> str:
    match = re.search(r"(\d+\.\d+)(?!\.\d)", label or "")
    if match:
        return match.group(1)
    match = re.match(r"^([A-E])\s+\S", label or "")
    if match:
        return match.group(1)
    match = re.match(r"^\*?(\d+)\s+", label or "")
    return match.group(1) if match else ""


def _parent_of_dot3(num: str) -> str:
    parts = (num or "").split(".")
    if len(parts) < 3:
        return ""
    return ".".join(parts[:2])


def _entry(label: str, print_page: int | None, level: int, pdf_page: int, is_chapter: bool = False) -> dict:
    return {
        "label": label,
        "print_page": print_page,
        "is_chapter": is_chapter,
        "level": 0 if is_chapter else level,
        "pdf_page": pdf_page,
    }


def _agent_home(root: str) -> Path:
    base = Path(root).resolve() if root else Path.cwd()
    if (base / "textbooks").is_dir():
        return base
    if base.name == "textbooks":
        return base.parent
    if base.parent.name == "textbooks":
        return base.parent.parent
    return base


def _pick_book(base: Path, book_id: str) -> Path | None:
    textbooks = base / "textbooks"
    if not textbooks.is_dir():
        return None
    if book_id:
        direct = textbooks / book_id
        return direct if direct.is_dir() else None
    defaults: list[Path] = []
    others: list[Path] = []
    for folder in sorted(p for p in textbooks.iterdir() if p.is_dir()):
        if not (folder / "pages").is_dir():
            continue
        meta_path = folder / "meta.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = {}
            if meta.get("is_default"):
                defaults.append(folder)
                continue
        others.append(folder)
    chosen = defaults or others
    return chosen[0] if chosen else None


def _load_pages(book: Path) -> list[tuple[int, Path, list[str]]]:
    pages: list[tuple[int, Path, list[str]]] = []
    for path in sorted((book / "pages").glob("*.md")):
        pdf_page = _pdf_from_name(path)
        if pdf_page <= 0:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        pages.append((pdf_page, path, _clean_lines(text)))
    return pages


def _catalog_chapter_num(label: str) -> int | None:
    """只认第N章。第N部分的部号不能拿去给 1/2/5 这种活动号归桶。"""
    match = CHAPTER_LINE.match(label or "") or re.match(
        rf"^第\s*({CN_NUM_TOKEN})\s*章", label or ""
    )
    return _as_int(match.group(1)) if match else None


def _section_chapter_num(num: str) -> int | None:
    head = str(num or "").split(".")[0]
    return int(head) if head.isdigit() else None


def _reorder_catalog_skeleton(skeleton: list[dict]) -> list[dict]:
    """两栏印刷目录会先吐出后一章章头，再跟上前一章剩下的节。

    按节号把条目归回对应章，再按章号拼回，避免第4章插到 3.2 前面。
    引论/附录等无章号的章头：出现在编号章之前的仍放前面，其余跟在全部编号章之后。
    """
    has_zhang = any(
        item.get("kind") == "chapter" and _catalog_chapter_num(str(item.get("label") or "")) is not None
        for item in skeleton
    )
    has_dotted = any(
        item.get("kind") == "section" and "." in str(item.get("num") or "")
        for item in skeleton
    )
    if not (has_zhang and has_dotted):
        return list(skeleton)

    prefix: list[dict] = []
    chapters: dict[int, dict] = {}
    buckets: dict[int, list[dict]] = {}
    suffix: list[dict] = []
    current_ch: int | None = None
    started = False

    for item in skeleton:
        if item.get("kind") == "chapter":
            ch = _catalog_chapter_num(str(item.get("label") or ""))
            if ch is None:
                if started:
                    suffix.append(item)
                else:
                    prefix.append(item)
                continue
            started = True
            chapters.setdefault(ch, item)
            current_ch = ch
            continue
        if item.get("kind") == "section":
            ch = _section_chapter_num(str(item.get("num") or ""))
            if ch is not None:
                started = True
                current_ch = ch
                buckets.setdefault(ch, []).append(item)
                continue
        if current_ch is not None:
            buckets.setdefault(current_ch, []).append(item)
        elif not started:
            prefix.append(item)
        else:
            suffix.append(item)

    result = list(prefix)
    for ch in sorted(set(chapters) | set(buckets)):
        if ch in chapters:
            result.append(chapters[ch])
        result.extend(buckets.get(ch, []))
    result.extend(suffix)
    return result


def _item_pdf(item: dict, offset: int) -> int:
    try:
        pdf_page = int(item.get("pdf_page") or 0)
    except (TypeError, ValueError):
        pdf_page = 0
    if pdf_page:
        return pdf_page
    print_page = item.get("print_page")
    if print_page and offset:
        try:
            guessed = int(print_page) + int(offset)
        except (TypeError, ValueError):
            return 0
        return guessed if guessed > 0 else 0
    return 0


def _assemble_split_part(lines: list[str]) -> tuple[int | None, str]:
    """正文部头常被拆成「第」「部分」「数学建模活动案例」。"""
    heads = [line for line in lines[:8] if line]
    if not heads:
        return None, ""
    direct = PART_LINE.match(heads[0])
    if direct:
        return _as_int(direct.group(1)), _fix_group_title(direct.group(2) or "")
    if _norm_title(heads[0]) not in {"第", "第部分"}:
        return None, ""
    num: int | None = None
    title = ""
    for line in heads[1:]:
        part = PART_LINE.match(line)
        if part:
            num = num or _as_int(part.group(1))
            title = title or _fix_group_title(part.group(2) or "")
            continue
        if _norm_title(line) in {"第", "部分", "第部分"}:
            continue
        if SENTENCE_PUNCT.search(line) or len(line) > 24:
            break
        if BODY_SECTION.match(line) or TOC_SIMPLE_SUB.match(f"{line} 0"):
            break
        if not title:
            title = _fix_group_title(line)
            break
    return num, title


def _scan_group_heads(
    pages: list[tuple[int, Path, list[str]]],
    toc_set: set[int],
) -> list[dict]:
    """从正文回补印刷目录漏掉的引论 / 第N部分 / 附录。"""
    intro_pdf = 0
    appendix_pdf = 0
    part_first: dict[int, int] = {}
    part_title: dict[int, str] = {}
    pending: list[tuple[int, str]] = []

    for pdf_page, _path, lines in pages:
        if pdf_page in toc_set or not lines:
            continue
        if _is_intro_heading(lines[0]):
            intro_pdf = intro_pdf or pdf_page
        if _is_appendix_heading(lines[0]):
            appendix_pdf = appendix_pdf or pdf_page
        for line in lines[:4]:
            part = PART_LINE.match(line)
            if not part:
                continue
            num = _as_int(part.group(1))
            if num is None:
                continue
            part_first.setdefault(num, pdf_page)
            title = _fix_group_title(part.group(2) or "")
            if title:
                part_title.setdefault(num, title)
        num, title = _assemble_split_part(lines)
        if title and (num is not None or "数学建模" in title or title.startswith("第")):
            if num is not None:
                part_title.setdefault(num, title)
                prev = part_first.get(num)
                if prev is None or pdf_page < prev:
                    part_first[num] = pdf_page
            else:
                pending.append((pdf_page, title))

    for pdf_page, title in pending:
        for num, first in list(part_first.items()):
            if first - 2 <= pdf_page <= first:
                part_title.setdefault(num, title)
                if pdf_page < first:
                    part_first[num] = pdf_page

    recovered: list[dict] = []
    if intro_pdf:
        recovered.append(
            {"kind": "chapter", "label": "引论", "print_page": None, "pdf_page": intro_pdf}
        )
    for num in sorted(part_first):
        recovered.append(
            {
                "kind": "chapter",
                "label": _part_label(num, part_title.get(num, "")),
                "print_page": None,
                "pdf_page": part_first[num],
            }
        )
    if appendix_pdf:
        recovered.append(
            {"kind": "chapter", "label": "附录", "print_page": None, "pdf_page": appendix_pdf}
        )
    return recovered


def _has_group_head(skeleton: list[dict], head: dict) -> bool:
    label = str(head.get("label") or "")
    want = _norm_title(label)
    want_part = _part_num_from_label(label)
    for item in skeleton:
        if item.get("kind") != "chapter":
            continue
        have = _norm_title(str(item.get("label") or ""))
        if have == want or have.startswith(want) or want.startswith(have):
            return True
        if want_part is not None and _part_num_from_label(str(item.get("label") or "")) == want_part:
            return True
    return False


def _merge_group_heads(skeleton: list[dict], recovered: list[dict], offset: int) -> list[str]:
    """把正文回补的部头按印张/PDF 页插进印刷目录骨架。"""
    added: list[str] = []
    for head in recovered:
        if _has_group_head(skeleton, head):
            for item in skeleton:
                if item.get("kind") != "chapter":
                    continue
                if not _has_group_head([item], head):
                    continue
                if not item.get("pdf_page") and head.get("pdf_page"):
                    item["pdf_page"] = head["pdf_page"]
                if item.get("print_page") is None and head.get("print_page") is not None:
                    item["print_page"] = head["print_page"]
                if head.get("label") and len(str(head["label"])) > len(str(item.get("label") or "")):
                    item["label"] = head["label"]
            continue
        label = str(head.get("label") or "")
        if _norm_title(label) == "引论" and offset and head.get("pdf_page"):
            guessed = int(head["pdf_page"]) - int(offset)
            if guessed > 0 and head.get("print_page") is None:
                head["print_page"] = guessed
        head_pdf = _item_pdf(head, offset)
        insert_at: int | None = None
        for i, item in enumerate(skeleton):
            if item.get("kind") == "chapter":
                continue
            item_pdf = _item_pdf(item, offset)
            if head_pdf and item_pdf and item_pdf >= head_pdf:
                insert_at = i
                break
        row = {
            "kind": "chapter",
            "label": label,
            "print_page": head.get("print_page"),
            "pdf_page": head.get("pdf_page"),
        }
        if insert_at is None:
            skeleton.append(row)
        else:
            skeleton.insert(insert_at, row)
        added.append(label)
    return added


def _line_identity(line: str) -> tuple | None:
    text = ATX.sub("", (line or "").strip()).strip()
    if not text:
        return None
    if _is_contents_title(text):
        return ("contents",)
    if _is_intro_heading(text):
        return ("intro",)
    if _is_appendix_heading(text):
        return ("appendix",)
    latin_unit = LATIN_UNIT_LINE.match(text) or (_is_latin_group_bare(text) and not _is_appendix_heading(text))
    if latin_unit:
        return ("latin_unit", _norm_title(text))
    letter_sec = LETTER_SECTION.match(text)
    if letter_sec:
        return ("letter_sec", letter_sec.group(1).upper(), _norm_title(letter_sec.group(2)))
    if _is_self_heading(text):
        return ("self",)
    named_latin = LATIN_NAMED_PAGE.match(text)
    if named_latin:
        return ("latin_named", _norm_title(named_latin.group(1)))
    part = PART_LINE.match(text)
    if part:
        return ("part", _as_int(part.group(1)))
    appendix_item = APPENDIX_ITEM.match(text)
    if appendix_item:
        return ("appendix_item", appendix_item.group(1))
    chapter = CHAPTER_LINE.match(text)
    if chapter:
        return ("chapter", _as_int(chapter.group(1)))
    section = TOC_SECTION.match(text)
    if section:
        return ("section", section.group(2))
    simple = TOC_SIMPLE_SUB.match(text)
    if simple:
        return ("section", simple.group(2))
    named = TOC_NAMED.match(text)
    if named:
        return ("named", named.group(1))
    return None


def _item_identity(item: dict) -> tuple | None:
    kind = item.get("kind")
    label = str(item.get("label") or "")
    if kind == "chapter":
        if _norm_title(label) == "引论":
            return ("intro",)
        if _norm_title(label) in {"附录", "appendices"}:
            return ("appendix",)
        part_n = _part_num_from_label(label)
        if part_n is not None:
            return ("part", part_n)
        ch = _catalog_chapter_num(label)
        if ch is not None:
            return ("chapter", ch)
        if LATIN_UNIT_LINE.match(label) or _is_latin_group_bare(label):
            return ("latin_unit", _norm_title(label))
        return ("chapter", _norm_title(label))
    if kind == "section":
        letter_sec = re.match(r"^([A-E])\s+(.+)$", label)
        if letter_sec and not str(item.get("num") or "").count("."):
            return ("letter_sec", letter_sec.group(1).upper(), _norm_title(letter_sec.group(2)))
        if _is_self_heading(label):
            return ("self",)
        return ("section", str(item.get("num") or ""))
    raw_num = str(item.get("num") or "")
    if raw_num.startswith("附录"):
        return ("appendix_item", raw_num.replace("附录", "", 1))
    if kind == "named":
        return ("named", _norm_title(label))
    return None


def _head_toc_line(item: dict) -> str:
    label = str(item.get("label") or "").strip()
    print_page = item.get("print_page")
    if print_page:
        return f"{label} {int(print_page)}"
    return label


def _is_group_head_item(item: dict) -> bool:
    ident = _item_identity(item)
    return bool(ident) and ident[0] in {"intro", "part", "appendix"}


def _read_toc_page_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if "kb-page-toc" in text:
        heading = "目录"
        html_title = re.search(r'kb-page-toc-heading">([^<]*)</', text)
        if html_title:
            heading = html_title.group(1).strip() or heading
        text = f"## {heading}\n\n" + _html_toc_to_lines(text) + "\n"
    return text.replace("\r\n", "\n").split("\n")


def _patch_printed_toc(book: Path, toc_pages: list[int], skeleton: list[dict]) -> list[str]:
    """目录页缺的引论/第N部分/附录按骨架写回 Markdown；不改第N章。"""
    if not toc_pages:
        return []
    loaded: list[tuple[int, Path, list[str]]] = []
    present: set[tuple] = set()
    for raw in toc_pages:
        pdf_page = int(raw)
        path = book / "pages" / f"{pdf_page:04d}.md"
        if not path.is_file():
            continue
        lines = _read_toc_page_lines(path)
        loaded.append((pdf_page, path, lines))
        for line in lines:
            ident = _line_identity(line)
            if ident is not None:
                present.add(ident)
    if not loaded:
        return []
    inserted: list[str] = []
    dirty: set[int] = set()
    for index, item in enumerate(skeleton):
        if not _is_group_head_item(item):
            continue
        ident = _item_identity(item)
        if ident is None or ident in present:
            continue
        target = 0
        insert_at: int | None = None
        for later in skeleton[index + 1 :]:
            later_id = _item_identity(later)
            if later_id is None:
                continue
            for page_idx, (_pdf, _path, lines) in enumerate(loaded):
                for i, line in enumerate(lines):
                    if _line_identity(line) == later_id:
                        target = page_idx
                        insert_at = i
                        break
                if insert_at is not None:
                    break
            if insert_at is not None:
                break
        new_line = _head_toc_line(item)
        _pdf, _path, lines = loaded[target]
        if insert_at is None:
            lines.append(new_line)
        else:
            lines.insert(insert_at, new_line)
        loaded[target] = (_pdf, _path, lines)
        present.add(ident)
        dirty.add(target)
        inserted.append(str(item.get("label") or new_line))
    for page_idx in dirty:
        _pdf, path, lines = loaded[page_idx]
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return inserted


def _read_sidecar(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_mineru_toc(book: Path) -> dict:
    data = _read_sidecar(book / "mineru_toc.json")
    pages: list[int] = []
    for raw in data.get("toc_pages") or []:
        try:
            page = int(raw)
        except (TypeError, ValueError):
            continue
        if page > 0:
            pages.append(page)
    mapping = data.get("print_to_pdf") if isinstance(data.get("print_to_pdf"), dict) else {}
    return {"toc_pages": sorted(set(pages)), "print_to_pdf": mapping}


def _load_agent_toc(book: Path) -> dict:
    return _read_sidecar(book / "toc_agent.json")


def _parse_catalog(
    pages: list[tuple[int, Path, list[str]]],
    known_toc_pages: list[int] | None = None,
) -> tuple[list[int], list[dict], list[dict]]:
    toc_pages: list[int] = []
    skeleton: list[dict] = []
    catalog_subs: list[dict] = []
    last_section = ""
    current_unit = ""
    known = {int(p) for p in (known_toc_pages or []) if int(p) > 0}
    for _pdf_page, _path, lines in pages:
        if known:
            if _pdf_page not in known:
                continue
        elif not _is_toc_page(lines):
            continue
        toc_pages.append(_pdf_page)
        for line in lines:
            if _is_contents_title(line):
                continue
            latin_unit = LATIN_UNIT_LINE.match(line)
            if latin_unit or _is_latin_group_bare(line):
                last_section = ""
                if latin_unit:
                    current_unit = latin_unit.group(1)
                    label = f"{latin_unit.group(1)} {latin_unit.group(2).strip()}"
                else:
                    title = line.strip()
                    current_unit = title
                    label = title
                skeleton.append({"kind": "chapter", "label": label, "print_page": None})
                continue
            letter_sec = LETTER_SECTION.match(line)
            if letter_sec:
                letter, title, print_page = letter_sec.groups()
                last_section = f"{current_unit}.{letter}" if current_unit else letter
                skeleton.append(
                    {
                        "kind": "section",
                        "label": f"{letter} {title.strip()}",
                        "num": last_section,
                        "print_page": int(print_page),
                    }
                )
                continue
            self_row = SELF_ASSESS.match(line)
            if self_row and self_row.group(1):
                last_section = ""
                skeleton.append(
                    {
                        "kind": "named",
                        "label": "Self-assessment",
                        "print_page": int(self_row.group(1)),
                    }
                )
                continue
            named_latin = LATIN_NAMED_PAGE.match(line)
            if named_latin and not LETTER_SECTION.match(line):
                last_section = ""
                skeleton.append(
                    {
                        "kind": "named",
                        "label": named_latin.group(1).strip(),
                        "print_page": int(named_latin.group(2)),
                    }
                )
                continue
            chapter = CHAPTER_LINE.match(line)
            if chapter:
                last_section = ""
                ch_num = _as_int(chapter.group(1))
                ch_token = str(ch_num) if ch_num is not None else chapter.group(1)
                skeleton.append(
                    {
                        "kind": "chapter",
                        "label": f"第{ch_token}章 {chapter.group(2).strip()}",
                        "print_page": None,
                    }
                )
                continue
            part = PART_LINE.match(line)
            if part:
                last_section = ""
                part_num = _as_int(part.group(1))
                title = _fix_group_title(part.group(2) or "")
                if part_num is not None:
                    label = _part_label(part_num, title)
                else:
                    label = f"第{part.group(1)}部分 {title}".strip()
                skeleton.append({"kind": "chapter", "label": label, "print_page": None})
                continue
            if _is_intro_heading(line):
                last_section = ""
                intro = INTRO_LINE.match(line)
                skeleton.append(
                    {
                        "kind": "chapter",
                        "label": "引论",
                        "print_page": int(intro.group(1)) if intro and intro.group(1) else None,
                    }
                )
                continue
            if _is_appendix_heading(line):
                last_section = ""
                skeleton.append({"kind": "chapter", "label": "附录", "print_page": None})
                continue
            appendix_item = APPENDIX_ITEM.match(line)
            if appendix_item:
                last_section = ""
                skeleton.append(
                    {
                        "kind": "named",
                        "label": f"附录{appendix_item.group(1)} {appendix_item.group(2).strip()}",
                        "num": f"附录{appendix_item.group(1)}",
                        "print_page": int(appendix_item.group(3)),
                    }
                )
                continue
            dot3 = TOC_DOT3.match(line)
            if dot3:
                star, num, title, print_page = dot3.groups()
                catalog_subs.append(
                    {
                        "kind": "dotted",
                        "label": f"{star}{num} {title.strip()}",
                        "num": num,
                        "parent": _parent_of_dot3(num),
                        "print_page": int(print_page),
                    }
                )
                continue
            section = TOC_SECTION.match(line)
            if section:
                star, num, title, print_page = section.groups()
                last_section = num
                skeleton.append(
                    {
                        "kind": "section",
                        "label": f"{star}{num} {title.strip()}",
                        "num": num,
                        "print_page": int(print_page),
                    }
                )
                continue
            named = TOC_NAMED.match(line)
            if named:
                last_section = ""
                skeleton.append(
                    {
                        "kind": "named",
                        "label": named.group(1),
                        "print_page": int(named.group(2)),
                    }
                )
                continue
            simple = TOC_SIMPLE_SUB.match(line)
            if simple:
                star, num, title, print_page = simple.groups()
                if last_section and "." in str(last_section):
                    catalog_subs.append(
                        {
                            "kind": "simple",
                            "label": f"{star}{num} {title.strip()}",
                            "num": int(num),
                            "parent": last_section,
                            "print_page": int(print_page),
                        }
                    )
                else:
                    last_section = num
                    skeleton.append(
                        {
                            "kind": "section",
                            "label": f"{star}{num} {title.strip()}",
                            "num": num,
                            "print_page": int(print_page),
                        }
                    )
    return toc_pages, skeleton, catalog_subs


def _looks_like_chapter_title(line: str, titles: set[str]) -> bool:
    compact = _norm_title(line)
    return compact in titles or line in titles


def _title_core(label: str) -> str:
    return _norm_title(re.sub(r"^\*?\d+(?:\.\d+)*\s*", "", label or ""))


def _skip_heading(title: str, chapter_titles: set[str]) -> bool:
    compact = _norm_title(title)
    if not compact or SENTENCE_PUNCT.search(title or ""):
        return True
    if compact in SKIP_UNNUMBERED or NAMED_HEADING.match(compact):
        return True
    if compact.startswith(("图", "表", "若")):
        return True
    if _looks_like_chapter_title(compact, chapter_titles):
        return True
    return compact.startswith(SKIP_PREFIXES)


def _guess_print(pdf_page: int, parent: dict, offset: int, catalog_print: int | None) -> int | None:
    if catalog_print:
        return int(catalog_print)
    parent_print = parent.get("print_page")
    parent_pdf = int(parent.get("pdf_page") or 0)
    if offset and pdf_page:
        guessed = pdf_page - offset
        if guessed > 0:
            return guessed
    if parent_print and parent_pdf and pdf_page:
        guessed = int(parent_print) + (pdf_page - parent_pdf)
        if guessed > 0:
            return guessed
    if parent_print:
        return int(parent_print)
    return None


def _merge_sub(existing: dict, incoming: dict) -> dict:
    merged = dict(existing)
    if not merged.get("pdf_page") and incoming.get("pdf_page"):
        merged["pdf_page"] = incoming["pdf_page"]
    if not merged.get("print_page") and incoming.get("print_page"):
        merged["print_page"] = incoming["print_page"]
    if incoming.get("label") and len(str(incoming["label"])) > len(str(merged.get("label") or "")):
        merged["label"] = incoming["label"]
    return merged


def _same_sub(left: dict, right: dict) -> bool:
    if left.get("kind") == "dotted" and right.get("kind") == "dotted":
        return left.get("num") == right.get("num")
    return _title_core(left.get("label")) == _title_core(right.get("label"))


def _skeleton_chapter_nums(skeleton: list[dict]) -> set[str]:
    """Chapter integers already in the printed-catalog skeleton (e.g. 10, 13)."""
    nums: set[str] = set()
    for item in skeleton:
        if item.get("kind") == "chapter":
            raw_label = str(item.get("label") or "")
            match = (
                CHAPTER_LINE.match(raw_label)
                or PART_LINE.match(raw_label)
                or re.match(rf"^第\s*({CN_NUM_TOKEN})\s*(?:章|部分)", raw_label)
            )
            parsed = _as_int(match.group(1)) if match else None
            if parsed is not None:
                nums.add(str(parsed))
        raw = item.get("num")
        if raw:
            head = str(raw).split(".")[0]
            if head.isdigit():
                nums.add(str(int(head)))
    return nums


def _insert_section(skeleton: list[dict], extra: dict) -> None:
    extra_key = _num_key(extra["num"])
    extra_ch = extra["num"].split(".")[0]
    known_ch = _skeleton_chapter_nums(skeleton)
    insert_at: int | None = None
    for i, item in enumerate(skeleton):
        if item["kind"] == "chapter":
            match = (
                CHAPTER_LINE.match(item["label"])
                or PART_LINE.match(item["label"])
                or re.match(rf"^第\s*({CN_NUM_TOKEN})\s*(?:章|部分)", item["label"])
            )
            parsed = _as_int(match.group(1)) if match else None
            if parsed is not None and extra_ch in known_ch and parsed > int(extra_ch):
                insert_at = i
                break
        if item["kind"] == "section" and item.get("num"):
            item_ch = str(item["num"]).split(".")[0]
            if item_ch != extra_ch:
                continue
            if _num_key(str(item["num"])) > extra_key:
                insert_at = i
                break
        if item["kind"] == "named":
            later_same = any(
                row["kind"] == "section" and str(row.get("num") or "").split(".")[0] == extra_ch
                for row in skeleton[i + 1 :]
            )
            prev_sections = [row for row in skeleton[:i] if row["kind"] == "section"]
            if (
                prev_sections
                and str(prev_sections[-1].get("num") or "").split(".")[0] == extra_ch
                and not later_same
            ):
                insert_at = i
                break
    if insert_at is None:
        skeleton.append(extra)
    else:
        skeleton.insert(insert_at, extra)


def _backbone_from_agent(rows: list) -> list[dict]:
    out: list[dict] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        try:
            level = int(item.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        if level >= 2:
            continue
        is_chapter = bool(item.get("is_chapter") or level == 0)
        try:
            pdf_page = int(item.get("pdf_page") or 0)
        except (TypeError, ValueError):
            pdf_page = 0
        print_page = item.get("print_page")
        if print_page is not None:
            try:
                print_page = int(print_page)
            except (TypeError, ValueError):
                print_page = None
        out.append(_entry(label, print_page, 0 if is_chapter else 1, pdf_page, is_chapter=is_chapter))
    return out


def _agent_kids_for_section(
    agent: dict,
    start_pdf: int,
    end_pdf: int,
    section_label: str,
) -> list[dict]:
    intervals = agent.get("intervals") if isinstance(agent.get("intervals"), list) else []
    out: list[dict] = []
    want = _norm_title(section_label)
    for interval in intervals:
        if not isinstance(interval, dict):
            continue
        try:
            start = int(interval.get("start_pdf") or 0)
        except (TypeError, ValueError):
            start = 0
        label = str(interval.get("section_label") or "")
        if start != start_pdf and _norm_title(label) != want:
            continue
        kids = interval.get("entries") if isinstance(interval.get("entries"), list) else []
        for kid in kids:
            if not isinstance(kid, dict):
                continue
            text = str(kid.get("label") or "").strip()
            try:
                pdf_page = int(kid.get("pdf_page") or 0)
            except (TypeError, ValueError):
                pdf_page = 0
            if not text or not pdf_page:
                continue
            if _norm_title(text) in {"上海教育出版社", "致谢"}:
                continue
            if start_pdf and end_pdf and not (start_pdf <= pdf_page < end_pdf):
                continue
            print_page = kid.get("print_page")
            if print_page is not None:
                try:
                    print_page = int(print_page)
                except (TypeError, ValueError):
                    print_page = None
            num = kid.get("num")
            if num is None:
                match = re.match(r"^\*?(\d+)\b", text)
                num = int(match.group(1)) if match else None
            out.append(
                {
                    "kind": "simple",
                    "label": text,
                    "num": num,
                    "pdf_page": pdf_page,
                    "print_page": print_page,
                }
            )
    return out


def _merge_body_sections(
    skeleton: list[dict],
    body_sections: dict[str, int],
    body_section_titles: dict[str, str],
    offset: int,
    subsections: list[dict] | None = None,
) -> None:
    """印刷目录漏掉、但正文有带标题的节，按正文补入。

    章内小标题（如 13.5 里的「2.1 …」）章号不在本书骨架里，降成小节，
    按 PDF 页挂到当前节下面，避免插到第10章前面。
    """
    known = {item["num"] for item in skeleton if item.get("num")}
    known_ch = _skeleton_chapter_nums(skeleton)
    extras: list[dict] = []
    kids = subsections if subsections is not None else []
    for num, title in body_section_titles.items():
        if num in known or not title or SENTENCE_PUNCT.search(title):
            continue
        pdf_page = int(body_sections.get(num) or 0)
        print_page = pdf_page - offset if offset and pdf_page > offset else None
        chapter = num.split(".")[0]
        if chapter not in known_ch:
            kids.append(
                {
                    "kind": "simple",
                    "label": f"{num} {_plain_heading(title)}".strip(),
                    "num": num,
                    "pdf_page": pdf_page,
                }
            )
            continue
        extras.append(
            {
                "kind": "section",
                "label": f"{num} {_plain_heading(title)}".strip(),
                "num": num,
                "print_page": print_page,
            }
        )
    extras.sort(key=lambda item: _num_key(item["num"]))
    for extra in extras:
        _insert_section(skeleton, extra)


def _has_lettered_sections(skeleton: list[dict]) -> bool:
    return any(
        item.get("kind") == "section" and re.match(r"^[A-E]\s+", str(item.get("label") or ""))
        for item in skeleton
    )


def _named_title_keys(skeleton: list[dict]) -> set[str]:
    keys: set[str] = set()
    for item in skeleton:
        if item.get("kind") != "named":
            continue
        key = _norm_title(str(item.get("label") or ""))
        if key and key != "selfassessment":
            keys.add(key)
    return keys


def _assign_lettered_body_pages(
    skeleton: list[dict],
    pages: list[tuple[int, Path, list[str]]],
    toc_set: set[int],
) -> None:
    """字母分节（A–E）按正文出现顺序回填 PDF 页：单元目标页归章和 A，其余板块取专页。"""
    if not _has_lettered_sections(skeleton):
        return

    named_keys = _named_title_keys(skeleton)
    events: list[tuple[int, str, str]] = []
    for pdf_page, _path, lines in pages:
        if pdf_page in toc_set or not lines:
            continue
        heads = [ATX.sub("", line).strip() for line in lines[:12] if line]
        letters: list[str] = []
        for head in heads:
            match = LETTER_BODY_SECTION.match(head)
            if match:
                letters.append(match.group(1).upper())
        compact = [_norm_title(head) for head in heads]
        if len(set(letters)) >= 3:
            events.append((pdf_page, "opener", "A"))
            continue
        if letters:
            events.append((pdf_page, "section", letters[0]))
            continue
        if compact and compact[0] == "selfassessment":
            events.append((pdf_page, "self", ""))
            continue
        if compact and compact[0] == "appendices":
            events.append((pdf_page, "appendix", ""))
            for _head, key in zip(heads, compact):
                if key in named_keys:
                    events.append((pdf_page, "app_item", key))
                    break
            continue
        for _head, key in zip(heads, compact):
            if key in named_keys:
                events.append((pdf_page, "app_item", key))
                break

    cursor = 0

    def take(kind: str, extra: str = "", stop_opener: bool = False) -> int:
        nonlocal cursor
        index = cursor
        while index < len(events):
            pdf_page, ev_kind, ev_extra = events[index]
            if stop_opener and ev_kind == "opener" and kind != "opener":
                return 0
            if ev_kind == kind and (not extra or ev_extra == extra):
                cursor = index + 1
                return pdf_page
            index += 1
        return 0

    pending_a = False
    last_chapter: dict | None = None
    for item in skeleton:
        kind = item.get("kind")
        label = str(item.get("label") or "")
        if kind == "chapter":
            last_chapter = item
            if _norm_title(label).lower() == "appendices":
                pdf_page = take("appendix")
            else:
                pdf_page = take("opener")
                pending_a = True
            if pdf_page:
                item["pdf_page"] = pdf_page
            continue
        if kind == "section" and re.match(r"^[A-E]\s+", label):
            letter = label[0].upper()
            if letter == "A" and pending_a:
                pending_a = False
                opener = int((last_chapter or {}).get("pdf_page") or 0)
                if opener:
                    item["pdf_page"] = opener
                    continue
            pdf_page = take("section", letter, stop_opener=True)
            if pdf_page:
                item["pdf_page"] = pdf_page
            continue
        if kind == "named" and _norm_title(label) == "selfassessment":
            pdf_page = take("self", stop_opener=True)
            if pdf_page:
                item["pdf_page"] = pdf_page
            continue
        if kind == "named":
            pdf_page = take("app_item", _norm_title(label))
            if pdf_page:
                item["pdf_page"] = pdf_page


def build_toc(root: str, book_id: str = "") -> dict:
    """从书页抽出章/节/小节，写入该教材的 toc.json。"""
    base = _agent_home(root)
    book = _pick_book(base, (book_id or "").strip())
    if book is None:
        return {"ok": False, "error": "工作区里没有可用教材"}

    pages = _load_pages(book)
    if not pages:
        return {"ok": False, "error": "教材没有分页 Markdown", "book_id": book.name}

    mineru = _load_mineru_toc(book)
    agent = _load_agent_toc(book)
    known_pages = list(mineru.get("toc_pages") or [])
    toc_pages, skeleton, catalog_subs = _parse_catalog(pages, known_toc_pages=known_pages or None)
    if known_pages and not skeleton:
        toc_pages, skeleton, catalog_subs = _parse_catalog(pages, known_toc_pages=None)
    skeleton = _reorder_catalog_skeleton(skeleton)
    toc_set = set(toc_pages)
    _assign_lettered_body_pages(skeleton, pages, toc_set)
    chapter_titles: set[str] = set()
    for item in skeleton:
        if item["kind"] != "chapter":
            continue
        label = str(item["label"] or "")
        chapter_titles.add(_norm_title(label))
        chapter_titles.add(_norm_title(re.sub(r"^第\d+(?:章|部分)\s*", "", label)))
        part = PART_LINE.match(label)
        if part:
            chapter_titles.add(_norm_title(f"第{part.group(1)}部分"))
    section_titles = {
        item["num"]: _title_core(item["label"])
        for item in skeleton
        if item["kind"] == "section" and item.get("num")
    }
    plain_section_nums = {
        str(item["num"])
        for item in skeleton
        if item.get("kind") == "section" and str(item.get("num") or "").isdigit()
    }

    body_sections: dict[str, int] = {}
    body_section_titles: dict[str, str] = {}
    body_dot3: dict[str, int] = {}
    front_pdf = 0
    back_pdf = 0
    chapter_openers: dict[str, int] = {}
    subsections: list[dict] = []

    for pdf_page, _path, lines in pages:
        if pdf_page in toc_set:
            continue
        if lines and _is_front_heading(lines[0]):
            front_pdf = front_pdf or pdf_page
        if any(_is_back_heading(line) for line in lines[:6]):
            back_pdf = pdf_page
        if any(_norm_title(line) == "致谢" for line in lines[:6]):
            chapter_openers.setdefault("致谢", pdf_page)
        if lines and _is_intro_heading(lines[0]):
            chapter_openers.setdefault("引论", pdf_page)
        if lines and _is_appendix_heading(lines[0]):
            chapter_openers.setdefault("附录", pdf_page)
        for line in lines:
            dot3 = BODY_DOT3.match(line)
            if dot3:
                _star, num, _title = dot3.groups()
                body_dot3.setdefault(num, pdf_page)
                continue
            section = BODY_SECTION.match(line)
            if section and not TOC_SECTION.match(line):
                num = section.group(2)
                body_sections.setdefault(num, pdf_page)
                title = (section.group(3) or "").strip()
                if title:
                    body_section_titles.setdefault(num, title)
            part = PART_LINE.match(line)
            if part:
                part_num = _as_int(part.group(1))
                if part_num is not None:
                    chapter_openers.setdefault(_norm_title(_part_label(part_num)), pdf_page)
            if _looks_like_chapter_title(line, chapter_titles):
                chapter_openers.setdefault(_norm_title(line), pdf_page)

        for line in lines:
            if BODY_DOT3.match(line) or BODY_SECTION.match(line):
                continue
            sub = SUBSECTION.match(line)
            if not sub:
                continue
            _star, num, title = sub.groups()
            if str(num) in plain_section_nums:
                body_sections.setdefault(str(num), pdf_page)
                if title:
                    body_section_titles.setdefault(str(num), title.strip())
                continue
            if _skip_heading(title, chapter_titles):
                continue
            subsections.append(
                {
                    "kind": "simple",
                    "label": f"{_star}{num} {title.strip()}",
                    "num": int(num),
                    "pdf_page": pdf_page,
                }
            )

    offsets: list[int] = []
    for item in skeleton:
        if item["kind"] not in {"section", "named"}:
            continue
        pdf_page = body_sections.get(str(item.get("num") or ""), 0) or int(item.get("pdf_page") or 0)
        if pdf_page and item.get("print_page"):
            offsets.append(pdf_page - int(item["print_page"]))
    offset = int(median(offsets)) if offsets else 0
    _merge_body_sections(skeleton, body_sections, body_section_titles, offset, subsections)
    recovered_heads = _scan_group_heads(pages, toc_set)
    added_heads = _merge_group_heads(skeleton, recovered_heads, offset)
    patched_toc = _patch_printed_toc(book, toc_pages, skeleton)

    def print_to_pdf(print_page: int | None, fallback: int = 0) -> int:
        if print_page is None:
            return fallback
        if offset:
            return int(print_page) + offset
        return fallback

    used_agent_skeleton = False
    agent_skeleton = agent.get("skeleton_entries") if isinstance(agent.get("skeleton_entries"), list) else []
    backbone: list[dict] = []
    if front_pdf:
        backbone.append(_entry("前言", None, 0, front_pdf, is_chapter=True))

    last_pdf = 0
    if not skeleton and agent_skeleton:
        used_agent_skeleton = True
        for row in _backbone_from_agent(agent_skeleton):
            backbone.append(row)
            last_pdf = int(row.get("pdf_page") or last_pdf)
    for index, item in enumerate(skeleton):
        if item["kind"] == "chapter":
            keys = [
                _norm_title(item["label"]),
                _norm_title(re.sub(r"^第\d+(?:章|部分)\s*", "", item["label"])),
            ]
            part = PART_LINE.match(item["label"])
            if part:
                part_num = _as_int(part.group(1))
                if part_num is not None:
                    keys.append(_norm_title(_part_label(part_num)))
            opener = int(item.get("pdf_page") or 0)
            for key in keys:
                opener = opener or chapter_openers.get(key, 0)
                if opener:
                    break
            nxt = next((x for x in skeleton[index + 1 :] if x["kind"] == "section"), None)
            guessed = 0
            if nxt:
                first_pdf = body_sections.get(nxt["num"], print_to_pdf(nxt["print_page"]))
                guessed = first_pdf - 1 if first_pdf > 1 else first_pdf
            pdf_page = opener or guessed or last_pdf
            backbone.append(_entry(item["label"], item.get("print_page"), 0, pdf_page, is_chapter=True))
            if pdf_page:
                last_pdf = pdf_page
            continue

        if item["kind"] == "section":
            pdf_page = (
                body_sections.get(item["num"], 0)
                or int(item.get("pdf_page") or 0)
                or print_to_pdf(item["print_page"])
            )
            last_pdf = pdf_page
            backbone.append(_entry(item["label"], item["print_page"], 1, pdf_page))
            continue

        pdf_page = int(item.get("pdf_page") or 0) or print_to_pdf(item["print_page"], last_pdf)
        last_pdf = pdf_page
        backbone.append(_entry(item["label"], item["print_page"], 1, pdf_page))

    if back_pdf:
        backbone.append(_entry("后记", None, 0, back_pdf, is_chapter=True))
    thanks_pdf = int(chapter_openers.get("致谢") or 0)
    if thanks_pdf:
        backbone.append(_entry("致谢", None, 0, thanks_pdf, is_chapter=True))

    placed: set[int] = set()
    entries: list[dict] = []
    for pos, item in enumerate(backbone):
        entries.append(item)
        if item["level"] != 1 or item["label"] in {"内容提要", "复习题"}:
            continue
        section_num = _section_num(item["label"])
        start_pdf = int(item.get("pdf_page") or 0)
        end_pdf = 10**9
        for later in backbone[pos + 1 :]:
            later_pdf = int(later.get("pdf_page") or 0)
            if later_pdf:
                end_pdf = later_pdf
                break

        kids: list[dict] = []

        def add_kid(candidate: dict) -> None:
            for idx, old in enumerate(kids):
                if _same_sub(old, candidate):
                    kids[idx] = _merge_sub(old, candidate)
                    return
            kids.append(dict(candidate))

        for sub in catalog_subs:
            if sub.get("parent") != section_num:
                continue
            row = {key: value for key, value in sub.items() if key != "pdf_page"}
            add_kid(row)

        for idx, sub in enumerate(subsections):
            if idx in placed:
                continue
            pdf_page = int(sub.get("pdf_page") or 0)
            if sub.get("kind") == "dotted":
                if sub.get("parent") != section_num:
                    continue
                if pdf_page and not (start_pdf <= pdf_page < end_pdf):
                    continue
            else:
                if not (start_pdf <= pdf_page < end_pdf):
                    continue
            placed.add(idx)
            add_kid(sub)

        for row in _agent_kids_for_section(agent, start_pdf, end_pdf, str(item.get("label") or "")):
            add_kid(row)

        for sub in kids:
            if int(sub.get("pdf_page") or 0):
                continue
            if sub.get("kind") == "dotted":
                sub["pdf_page"] = body_dot3.get(str(sub.get("num")), print_to_pdf(sub.get("print_page")))
            else:
                sub["pdf_page"] = print_to_pdf(sub.get("print_page"), start_pdf)

        def kid_sort(row: dict) -> tuple:
            pdf_page = int(row.get("pdf_page") or 10**9)
            raw = row.get("num")
            if isinstance(raw, str) and "." in raw:
                parts = tuple(int(p) for p in raw.split(".") if p.isdigit())
            else:
                try:
                    parts = (int(raw),)
                except (TypeError, ValueError):
                    parts = (0,)
            return (pdf_page, parts)

        kids.sort(key=kid_sort)
        for sub in kids:
            pdf_page = int(sub.get("pdf_page") or 0)
            if not pdf_page:
                pdf_page = print_to_pdf(sub.get("print_page"), start_pdf)
            print_page = _guess_print(pdf_page, item, offset, sub.get("print_page"))
            entries.append(_entry(sub["label"], print_page, 2, pdf_page))

    print_to_pdf_map: dict[str, int] = {}
    for item in entries:
        print_page = item.get("print_page")
        pdf_page = item.get("pdf_page")
        if print_page is None or not pdf_page:
            continue
        key = str(print_page)
        if key not in print_to_pdf_map:
            print_to_pdf_map[key] = pdf_page

    if used_agent_skeleton:
        source = "agent"
    elif toc_pages or skeleton:
        source = "printed"
    else:
        source = "body"
    payload = {
        "entries": entries,
        "print_to_pdf": print_to_pdf_map,
        "toc_pages": toc_pages,
        "source": source,
    }
    dest = book / "toc.json"
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    levels = {"0": 0, "1": 0, "2": 0}
    for item in entries:
        key = str(item["level"])
        if key in levels:
            levels[key] += 1

    return {
        "ok": True,
        "book_id": book.name,
        "path": str(dest),
        "entries": len(entries),
        "levels": levels,
        "offset": offset,
        "toc_pages": toc_pages,
        "recovered_heads": added_heads,
        "patched_toc": patched_toc,
        "note": "已按书页抽出章、节、小节和印张页，写入 toc.json。印刷目录漏掉的引论/第N部分/附录从正文回补并写回目录页。导入写完书页后会调用本工具。",
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build textbooks/<id>/toc.json")
    parser.add_argument("--root", default="")
    parser.add_argument("--book-id", default="")
    args = parser.parse_args()
    print(json.dumps(build_toc(args.root or str(Path.cwd()), args.book_id), ensure_ascii=False))
