from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from statistics import median
from typing import Any

try:
    from ir_lib import FRONT_PAGES, safe_book_id
except ImportError:
    FRONT_PAGES = 24

    def safe_book_id(book_id: str) -> str:
        sid = (book_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
            raise ValueError("book_id required")
        return sid

CN_NUM_TOKEN = r"\d+|[一二三四五六七八九十两零百]+"
CHAPTER_LINE = re.compile(rf"^第\s*({CN_NUM_TOKEN})\s*章[\s·．.、]*(.+)$")
PART_LINE = re.compile(rf"^第\s*({CN_NUM_TOKEN})\s*部分(?:[\s·．.、]+(.+))?$")
SECTION_JIE_LINE = re.compile(rf"^第\s*({CN_NUM_TOKEN})\s*节[\s·．.、]*(.+)$")
PAGE_SLASH = re.compile(r"\s*/\s*(\d{1,4})$")
PAGE_SPACE = re.compile(r"\s+(\d{1,4})$")
PAGE_TAIL = re.compile(r"[ .\u3000．…·•、]+$")
TOC_DOT3 = re.compile(r"^(\*?)(\d+\.\d+\.\d+)(?!\.\d)\s+(.+?)\s+(\d+)\s*$")
TOC_SECTION = re.compile(r"^(\*?)(\d+\.\d+)(?!\.\d)\s+(.+?)\s+(\d+)\s*$")
TOC_NAMED = re.compile(r"^(内容提要|复习题)\s+(\d+)\s*$")
TOC_SIMPLE_SUB = re.compile(r"^(\*?)([1-9]\d{0,1})\s+(?!(?:世纪|年代))(.+?)\s+(\d+)\s*$")
INTRO_LINE = re.compile(r"^(?:引\s*论|绪\s*言)(?:\s+(\d+))?$")
APPENDIX_HEAD = re.compile(r"^附\s*录$", re.I)
APPENDIX_ITEM = re.compile(r"^附录\s*(\d+)\s+(.+?)\s+(\d+)\s*$")
BODY_DOT3 = re.compile(r"^(\*?)(\d+\.\d+\.\d+)(?!\.\d)\s+(.+?)$")
BODY_SECTION = re.compile(r"^(\*?)(\d+\.\d+)(?!\.\d)(?:\s+(.+))?$")
SUBSECTION = re.compile(
    r"^(\*?)([1-9]\d{0,1})\s+(?!(?:世纪|年代))([一-鿿]"
    r"[一-鿿A-Za-z0-9、与及的之至和（）()·—－\-《》]{0,40})$"
)
UNNUMBERED = re.compile(
    r"^([一-鿿][一-鿿、与及的之至和（）() ]{2,36})$"
)
SENTENCE_PUNCT = re.compile(r"[。．.!?！？：:;；，,]")
NAMED_HEADING = re.compile(
    r"^(前言|后记|目录|目 录|内容提要|复习题|引论|绪言|附录|本章复习|本章小结)$"
)
FRONT = re.compile(r"^前\s*言$")
BACK = re.compile(r"^后\s*记$")
LATIN_UNIT_LINE = re.compile(r"^(\d+)\s+([A-Z][A-Za-z][A-Za-z0-9’' \-]*)$")
LATIN_GROUP_BARE = re.compile(r"^[A-Z][A-Za-z]+(?:[ \-][A-Za-z]+){0,4}$")
LETTER_SECTION = re.compile(r"^([A-E])\s+(.+?)\s+(\d+)$")
LETTER_BODY_SECTION = re.compile(r"^([A-E])\s+([A-Za-z].+)$")
SELF_ASSESS = re.compile(r"^Self-assessment(?:\s+(\d+))?$", re.I)
LATIN_NAMED_PAGE = re.compile(r"^([A-Z][A-Za-z][A-Za-z0-9’' \-]*)\s+(\d+)$")
UNIT_TAIL = re.compile(r"[?？!！*∗·•]+$")
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
    "绪言",
    "附录",
    "本章复习",
    "本章小结",
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


def _lexicon() -> Any:
    """Shared lexicon module (same directory). None when it cannot be loaded.

    buddy-api 也会按路径加载本文件，那边 `tools/` 未必在 `sys.path` 上，
    所以先试普通 import，再按同目录路径加载。
    """
    global _LEXICON
    if _LEXICON is not None:
        return _LEXICON or None
    try:
        import toc_lexicon as lex  # type: ignore[import-not-found]
    except Exception:
        lex = None
    if lex is None:
        import importlib.util

        path = Path(__file__).resolve().parent / "toc_lexicon.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location("toc_lexicon", path)
            if spec is not None and spec.loader is not None:
                module = importlib.util.module_from_spec(spec)
                sys.modules.setdefault("toc_lexicon", module)
                try:
                    spec.loader.exec_module(module)
                    lex = module
                except Exception:
                    sys.modules.pop("toc_lexicon", None)
                    lex = None
    _LEXICON = lex if lex is not None else False
    return _LEXICON or None


_LEXICON: Any = None


def _bind_lexicon() -> None:
    """Prefer the shared lexicon over the built-in fallback regexes."""
    global CHAPTER_LINE, PART_LINE, SECTION_JIE_LINE
    lex = _lexicon()
    if lex is None:
        return
    chapter = lex.compiled("chapter_cn")
    part = lex.compiled("part_cn")
    jie = lex.compiled("section_cn_jie")
    if chapter is not None:
        CHAPTER_LINE = chapter
    if part is not None:
        PART_LINE = part
    if jie is not None:
        SECTION_JIE_LINE = jie


_bind_lexicon()


def _strip_page(text: str) -> tuple[str, int | None]:
    lex = _lexicon()
    if lex is not None:
        try:
            return lex.strip_page(text)
        except Exception:
            pass
    raw = (text or "").strip()
    if not raw:
        return "", None
    for regex in (PAGE_SLASH, PAGE_SPACE):
        match = regex.search(raw)
        if not match:
            continue
        label = PAGE_TAIL.sub("", raw[: match.start()]).strip()
        if label:
            return label, int(match.group(1))
    return raw, None


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


def _atx_depth(raw: str) -> tuple[str, int]:
    text = (raw or "").strip()
    hashes = 0
    while hashes < len(text) and hashes < 6 and text[hashes] == "#":
        hashes += 1
    if hashes and (hashes == len(text) or text[hashes].isspace()):
        return text[hashes:].strip(), hashes
    return ATX.sub("", text).strip(), 0


def _clean_lines(text: str) -> list[str]:
    return [line for line, _depth in _clean_lines_marked(text)]


def _clean_lines_marked(text: str) -> list[tuple[str, int]]:
    """Flatten markup but keep ATX heading depth (0 if the line is not a heading)."""
    lines: list[tuple[str, int]] = []
    for raw in _flatten_markup(text).splitlines():
        line, depth = _atx_depth(raw)
        line = re.sub(r"^\$\$\s+", "", line)
        line = GLUED_MATH.sub(r"\1 ", line)
        if not line:
            continue
        if line.startswith("![") or line.startswith("{'type'") or line.startswith("*{'type'"):
            continue
        lines.append((re.sub(r"\s+", " ", line), depth))
    return lines


def _is_contents_title(line: str) -> bool:
    compact = _norm_title(line).lower()
    return compact in {"目录", "contents"} or (line or "").strip() in {"目录", "目 录", "Contents", "CONTENTS"}


def _strip_unit_tail(line: str) -> str:
    return UNIT_TAIL.sub("", (line or "").strip()).strip()


def _is_self_heading(line: str) -> bool:
    compact = re.sub(r"[-\s]", "", _norm_title(line)).lower()
    return compact == "selfassessment"


def _is_latin_group_bare(line: str) -> bool:
    text = _strip_unit_tail(line)
    if not LATIN_GROUP_BARE.match(text):
        return False
    if _is_contents_title(text) or _is_self_heading(text):
        return False
    return True


def _is_latin_unit_line(line: str) -> bool:
    text = _strip_unit_tail(line)
    return bool(LATIN_UNIT_LINE.match(text) or _is_latin_group_bare(text))


def _trim_leader(text: str) -> str:
    return PAGE_TAIL.sub("", (text or "").strip()).strip()


def _catalog_probe(line: str) -> str:
    label, _page = _strip_page(line)
    return label or (line or "").strip()


def _lex_hit(line: str) -> Any:
    lex = _lexicon()
    if lex is None:
        return None
    probe = _catalog_probe(line)
    if not probe:
        return None
    try:
        return lex.match_line(probe)
    except Exception:
        return None


def _rejected_title(label: str) -> bool:
    lex = _lexicon()
    if lex is not None:
        try:
            return bool(lex.rejected_title(label))
        except Exception:
            pass
    return bool(re.match(r"^\d+\s*(世纪|年代)", (label or "").strip()))


def _is_column_title(label: str) -> bool:
    lex = _lexicon()
    if lex is None:
        return False
    try:
        return bool(lex.is_column_label(label))
    except Exception:
        return False


def _printed_intro_label(line: str) -> str:
    compact = _norm_title(_catalog_probe(line) or line)
    if "绪言" in compact:
        return "绪言"
    return "引论"


def _is_cn_catalog_line(line: str) -> bool:
    probe = _catalog_probe(line)
    if not probe:
        return False
    lex = _lexicon()
    if lex is not None:
        try:
            if lex.has_skip_prefix(probe) or lex.rejected_title(probe):
                return False
            if lex.match_line(probe) is not None:
                return True
        except Exception:
            pass
    return bool(
        CHAPTER_LINE.match(probe)
        or CHAPTER_LINE.match(line)
        or PART_LINE.match(probe)
        or PART_LINE.match(line)
        or SECTION_JIE_LINE.match(probe)
        or SECTION_JIE_LINE.match(line)
        or TOC_DOT3.match(line)
        or TOC_SECTION.match(line)
        or TOC_NAMED.match(line)
        or INTRO_LINE.match(line)
        or INTRO_LINE.match(probe)
        or APPENDIX_HEAD.match(probe)
        or APPENDIX_HEAD.match(line)
        or APPENDIX_ITEM.match(line)
    )


def _is_toc_page(lines: list[str]) -> bool:
    if not lines:
        return False
    if _is_contents_title(lines[0]):
        return True
    hits = 0
    en_hits = 0
    for line in lines:
        if _is_cn_catalog_line(line):
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


def _has_section_print(lines: list[str]) -> bool:
    for line in lines:
        label, page = _strip_page(line)
        if page is None:
            continue
        probe = label or line
        hit = _lex_hit(line)
        if hit is not None and hit.id in {"toc_section", "toc_dot3"}:
            return True
        if TOC_SECTION.match(line) or re.match(r"^(\*?)\d+\.\d+(?!\.\d)\s+", probe or ""):
            return True
    return False


def _is_toc_continuation(lines: list[str], prev_was_toc: bool) -> bool:
    """Previous printed-catalog page plus X.Y+page or at least two catalog hits.

    An appendix-head-only body page must not qualify.
    """
    if not prev_was_toc or not lines:
        return False
    if _is_toc_page(lines):
        return True
    if _has_section_print(lines):
        return True
    hits = sum(1 for line in lines if _is_cn_catalog_line(line))
    return hits >= 2


def _norm_title(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _chapter_key(label: str) -> str:
    text = (label or "").strip()
    match = CHAPTER_LINE.match(text) or re.match(
        rf"^第\s*({CN_NUM_TOKEN})\s*章[\s·．.、]*(.*)$", text
    )
    if match:
        num = _as_int(match.group(1))
        rest = _norm_title(match.group(2) if match.lastindex and match.lastindex >= 2 else "")
        if num is not None:
            return f"第{num}章{rest}"
    return _norm_title(text)


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
    probe = _catalog_probe(line)
    compact = _norm_title(probe or line)
    if compact in {"引论", "绪言"}:
        return True
    hit = _lex_hit(line)
    if hit is not None and hit.id == "intro":
        return True
    return bool(INTRO_LINE.match(probe or line or ""))


def _is_appendix_heading(line: str) -> bool:
    probe = _catalog_probe(line)
    compact = _norm_title(probe or line)
    if compact == "附录" or compact.lower() == "appendices":
        return True
    hit = _lex_hit(line)
    if hit is not None and hit.id == "appendix_head":
        return True
    return bool(APPENDIX_HEAD.match(probe or line or ""))


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


def _entry(
    label: str,
    print_page: int | None,
    level: int,
    pdf_page: int,
    is_chapter: bool = False,
    from_body: bool = False,
) -> dict:
    """One toc.json row.

    `from_body=True` 表示这条不是从书里印的目录页读来的，而是 `toc_extract` 去正文里
    扒的小节标题。两者的 `print_page` 都是有值的（正文那批按 offset 反推），所以光看
    有没有页码分不出来源 —— `write_toc_pages` 靠这个标记决定哪些不该印回目录页。
    """
    row = {
        "label": label,
        "print_page": print_page,
        "is_chapter": is_chapter,
        "level": 0 if is_chapter else level,
        "pdf_page": pdf_page,
    }
    if from_body:
        row["from_body"] = True
    return row


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
    sid = safe_book_id(book_id)
    direct = textbooks / sid
    return direct if direct.is_dir() else None


def _mineru_block_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, dict):
        chunks: list[str] = []
        for key in ("paragraph_content", "title_content", "text"):
            raw = content.get(key)
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict):
                        piece = str(item.get("content") or "").strip()
                        if piece:
                            chunks.append(piece)
                    elif str(raw).strip():
                        chunks.append(str(item))
            elif isinstance(raw, str) and raw.strip():
                chunks.append(raw.strip())
        if chunks:
            return "\n".join(chunks)
    text = block.get("text")
    return str(text).strip() if isinstance(text, str) else ""


def _mineru_page_texts(book: Path) -> dict[int, str]:
    path = book / "mineru" / "blocks.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, list):
        return {}
    pages: dict[int, list[str]] = {}
    for block in data:
        if not isinstance(block, dict):
            continue
        try:
            pdf_page = int(block.get("page_idx") or -1) + 1
        except (TypeError, ValueError):
            continue
        if pdf_page <= 0:
            continue
        text = _mineru_block_text(block)
        if text:
            pages.setdefault(pdf_page, []).append(text)
    return {page: "\n".join(parts) for page, parts in pages.items()}


def _prepare_lines(lines: list[str]) -> list[str]:
    lex = _lexicon()
    prepare = getattr(lex, "prepare_catalog_lines", None) if lex is not None else None
    if callable(prepare):
        try:
            return list(prepare(lines))
        except Exception:
            return list(lines)
    return list(lines)


def _catalog_score(lines: list[str]) -> int:
    return sum(1 for line in lines if _is_cn_catalog_line(line))


def _pdf_layout_line_sets(book: Path, pdf_page: int) -> list[list[str]]:
    pdf_path = book / "book.pdf"
    if not pdf_path.is_file():
        return []
    try:
        from toc_pdf import page_layout_candidates
    except Exception:
        return []
    out: list[list[str]] = []
    for blob in page_layout_candidates(pdf_path, pdf_page):
        lines = _clean_lines(blob)
        if lines:
            out.append(lines)
    return out


def _load_pages(book: Path) -> list[tuple[int, Path, list[str]]]:
    mineru_texts = _mineru_page_texts(book)
    pages: list[tuple[int, Path, list[str]]] = []
    prev_was_toc = False
    for path in sorted((book / "pages").glob("*.md")):
        pdf_page = _pdf_from_name(path)
        if pdf_page <= 0:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        md_lines = _clean_lines(text)
        mu_lines = _clean_lines(mineru_texts.get(pdf_page, "")) if mineru_texts.get(pdf_page) else []
        md_hits = _catalog_score(md_lines)
        mu_hits = _catalog_score(mu_lines)
        body_lines = mu_lines if mu_hits > md_hits else md_lines
        probe = pdf_page <= FRONT_PAGES or _is_toc_page(md_lines) or _is_toc_page(mu_lines) or prev_was_toc
        pdf_preps = [_prepare_lines(rows) for rows in _pdf_layout_line_sets(book, pdf_page)] if probe else []
        best_pdf = max(pdf_preps, key=_catalog_score) if pdf_preps else []
        # Prefer the original PDF layout on catalog pages. pages/*.md may already
        # be a write_toc_pages reprint and must not outrank the printed sheet.
        if best_pdf and _catalog_score(best_pdf) >= 3:
            pages.append((pdf_page, path, best_pdf))
            prev_was_toc = True
            continue
        prepared = [_prepare_lines(rows) for rows in (md_lines, mu_lines) if rows]
        if not prepared:
            pages.append((pdf_page, path, body_lines))
            prev_was_toc = False
            continue
        best = max(prepared, key=lambda rows: (_catalog_score(rows), len(rows)))
        if _is_toc_page(best) or _is_toc_continuation(best, prev_was_toc):
            pages.append((pdf_page, path, best))
            prev_was_toc = True
        else:
            pages.append((pdf_page, path, body_lines))
            prev_was_toc = False
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
    intro_label = ""
    appendix_pdf = 0
    part_first: dict[int, int] = {}
    part_title: dict[int, str] = {}
    pending: list[tuple[int, str]] = []

    for pdf_page, _path, lines in pages:
        if pdf_page in toc_set or not lines:
            continue
        if _is_intro_heading(lines[0]):
            intro_pdf = intro_pdf or pdf_page
            intro_label = intro_label or _printed_intro_label(lines[0])
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
            {
                "kind": "chapter",
                "label": intro_label or "引论",
                "print_page": None,
                "pdf_page": intro_pdf,
            }
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
    if _is_intro_heading(label):
        return any(
            item.get("kind") == "chapter" and _is_intro_heading(str(item.get("label") or ""))
            for item in skeleton
        )
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
        if _is_intro_heading(label) and offset and head.get("pdf_page"):
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
    text, _page = _strip_page(ATX.sub("", (line or "").strip()).strip())
    if not text:
        return None
    if _is_contents_title(text):
        return ("contents",)
    if _is_intro_heading(text):
        return ("intro",)
    if _is_appendix_heading(text):
        return ("appendix",)
    latin_text = _strip_unit_tail(text)
    latin_unit = LATIN_UNIT_LINE.match(latin_text) or (
        _is_latin_group_bare(latin_text) and not _is_appendix_heading(latin_text)
    )
    if latin_unit:
        return ("latin_unit", _norm_title(latin_text))
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
    jie = SECTION_JIE_LINE.match(text)
    if jie:
        return ("jie", _as_int(jie.group(1)), _norm_title(jie.group(2)))
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
        if _is_intro_heading(label):
            return ("intro",)
        if _norm_title(label) in {"附录", "appendices"}:
            return ("appendix",)
        part_n = _part_num_from_label(label)
        if part_n is not None:
            return ("part", part_n)
        ch = _catalog_chapter_num(label)
        if ch is not None:
            return ("chapter", ch)
        unit_label = _strip_unit_tail(label)
        if LATIN_UNIT_LINE.match(unit_label) or _is_latin_group_bare(unit_label):
            return ("latin_unit", _norm_title(unit_label))
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
        target = max(0, len(loaded) - 1)
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
    # Directory pages are written only by write_toc_pages (stamp + gate).
    # Keep inserted labels for the return value; do not touch pages/ here.
    _ = dirty
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


def _load_printed_toc(book: Path) -> dict:
    """Agent 看图核对后的印刷目录侧车。文件在就当权威，不再词法重读目录页。"""
    data = _read_sidecar(book / "toc_printed.json")
    if not data:
        return {"present": False, "toc_pages": [], "entries": []}
    pages: list[int] = []
    for raw in data.get("toc_pages") or []:
        try:
            page = int(raw)
        except (TypeError, ValueError):
            continue
        if page > 0:
            pages.append(page)
    entries = [
        row
        for row in (data.get("entries") or [])
        if isinstance(row, dict) and str(row.get("label") or "").strip()
    ]
    return {
        "present": True,
        "ok": bool(data.get("ok", True)),
        "toc_pages": sorted(set(pages)),
        "entries": entries,
        "winner": str(data.get("winner") or ""),
    }


def _printed_print_page(row: dict) -> int | None:
    raw = row.get("print_page")
    if raw is None or raw == "":
        return None
    try:
        page = int(raw)
    except (TypeError, ValueError):
        return None
    return page if page > 0 else None


def _skeleton_from_printed(entries: list[dict]) -> list[dict]:
    """把 toc_printed.json 的条目收成内部骨架。只靠标题形状，不读目录页文本。"""
    skeleton: list[dict] = []
    current_chapter = ""
    last_section = ""
    for row in entries:
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        print_page = _printed_print_page(row)
        try:
            level = int(row.get("level") or (0 if row.get("is_chapter") else 1))
        except (TypeError, ValueError):
            level = 1
        is_chapter = bool(row.get("is_chapter")) or level <= 0
        probe = label

        latin_line = _strip_unit_tail(label)
        latin_unit = LATIN_UNIT_LINE.match(latin_line)
        if latin_unit or _is_latin_group_bare(latin_line):
            last_section = ""
            if latin_unit:
                current_chapter = latin_unit.group(1)
                item_label = f"{latin_unit.group(1)} {latin_unit.group(2).strip()}"
            else:
                current_chapter = latin_line
                item_label = latin_line
            skeleton.append({"kind": "chapter", "label": item_label, "print_page": print_page})
            continue

        letter_body = LETTER_BODY_SECTION.match(label)
        if letter_body:
            letter, title = letter_body.groups()
            last_section = f"{current_chapter}.{letter}" if current_chapter else letter
            skeleton.append(
                {
                    "kind": "section",
                    "label": f"{letter} {title.strip()}",
                    "num": last_section,
                    "print_page": print_page,
                }
            )
            continue

        if SELF_ASSESS.match(label) or _norm_title(label) == "selfassessment":
            last_section = ""
            skeleton.append({"kind": "named", "label": "Self-assessment", "print_page": print_page})
            continue

        chapter = CHAPTER_LINE.match(probe)
        if chapter:
            last_section = ""
            ch_num = _as_int(chapter.group(1))
            ch_token = str(ch_num) if ch_num is not None else chapter.group(1)
            current_chapter = ch_token
            skeleton.append(
                {
                    "kind": "chapter",
                    "label": f"第{ch_token}章 {chapter.group(2).strip()}",
                    "print_page": print_page,
                }
            )
            continue

        part = PART_LINE.match(probe)
        if part:
            last_section = ""
            part_num = _as_int(part.group(1))
            title = _fix_group_title(part.group(2) or "")
            if part_num is not None:
                item_label = _part_label(part_num, title)
            else:
                item_label = f"第{part.group(1)}部分 {title}".strip()
            current_chapter = f"bu{part_num if part_num is not None else part.group(1)}"
            skeleton.append({"kind": "chapter", "label": item_label, "print_page": print_page})
            continue

        jie = SECTION_JIE_LINE.match(probe)
        if jie:
            last_section = f"jie:{current_chapter or 0}:{_as_int(jie.group(1))}"
            skeleton.append(
                {
                    "kind": "section",
                    "label": probe,
                    "num": last_section,
                    "print_page": print_page,
                    "level": 1,
                }
            )
            continue

        if _is_intro_heading(probe):
            last_section = ""
            current_chapter = "yinlun"
            skeleton.append(
                {
                    "kind": "chapter",
                    "label": _printed_intro_label(probe),
                    "print_page": print_page,
                }
            )
            continue

        if _is_appendix_heading(probe):
            last_section = ""
            current_chapter = "fulu"
            skeleton.append({"kind": "chapter", "label": "附录", "print_page": print_page})
            continue

        app_bare = re.match(r"^附录\s*(\d+)\s+(.+)$", probe)
        if app_bare:
            last_section = ""
            skeleton.append(
                {
                    "kind": "named",
                    "label": f"附录{app_bare.group(1)} {app_bare.group(2).strip()}",
                    "num": f"附录{app_bare.group(1)}",
                    "print_page": print_page,
                }
            )
            continue

        if BODY_DOT3.match(probe) or TOC_DOT3.match(label):
            continue

        bare_sec = re.match(r"^(\*?)(\d+\.\d+)(?!\.\d)\s+(.+)$", probe)
        if bare_sec:
            star, num, title = bare_sec.groups()
            last_section = num
            skeleton.append(
                {
                    "kind": "section",
                    "label": f"{star}{num} {_trim_leader(title)}",
                    "num": num,
                    "print_page": print_page,
                }
            )
            continue

        if probe in {"内容提要", "复习题"} or TOC_NAMED.match(probe):
            last_section = ""
            named = TOC_NAMED.match(probe)
            skeleton.append(
                {
                    "kind": "named",
                    "label": named.group(1) if named else probe,
                    "print_page": print_page,
                }
            )
            continue

        if _is_back_heading(probe) or _is_front_heading(probe):
            last_section = ""
            skeleton.append(
                {
                    "kind": "chapter",
                    "label": "后记" if _is_back_heading(probe) else "前言",
                    "print_page": print_page,
                }
            )
            continue

        if is_chapter:
            last_section = ""
            skeleton.append({"kind": "chapter", "label": label, "print_page": print_page})
            continue

        last_section = ""
        skeleton.append({"kind": "named", "label": label, "print_page": print_page, "level": 1})
    return _dedupe_catalog_rows(skeleton)


def _parse_catalog(
    pages: list[tuple[int, Path, list[str]]],
    known_toc_pages: list[int] | None = None,
) -> tuple[list[int], list[dict], list[dict]]:
    toc_pages: list[int] = []
    skeleton: list[dict] = []
    catalog_subs: list[dict] = []
    last_section = ""
    current_unit = ""
    # 「第N节」的节号每章都从 1 重新起算，所以 num 必须带上章号，否则第 2 章的
    # 「第一节」会在 body_sections 里覆盖第 1 章的「第一节」，全书节页码塌成最后一章。
    current_chapter = ""
    known = {int(p) for p in (known_toc_pages or []) if int(p) > 0}
    prev_was_toc = False
    for _pdf_page, _path, lines in pages:
        if known:
            if _pdf_page not in known:
                prev_was_toc = False
                continue
        elif not (_is_toc_page(lines) or _is_toc_continuation(lines, prev_was_toc)):
            prev_was_toc = False
            continue
        toc_pages.append(_pdf_page)
        prev_was_toc = True
        for line in _prepare_lines(lines):
            if _is_contents_title(line):
                continue
            stripped_label, stripped_page = _strip_page(line)
            probe = stripped_label or line
            latin_line = _strip_unit_tail(line)
            latin_unit = LATIN_UNIT_LINE.match(latin_line)
            if latin_unit or _is_latin_group_bare(latin_line):
                last_section = ""
                if latin_unit:
                    current_unit = latin_unit.group(1)
                    label = f"{latin_unit.group(1)} {latin_unit.group(2).strip()}"
                else:
                    title = latin_line
                    current_unit = title
                    label = title
                current_chapter = current_unit
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
            if self_row:
                last_section = ""
                print_page = int(self_row.group(1)) if self_row.group(1) else None
                skeleton.append(
                    {
                        "kind": "named",
                        "label": "Self-assessment",
                        "print_page": print_page,
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
            chapter = CHAPTER_LINE.match(probe) or CHAPTER_LINE.match(line)
            if chapter:
                last_section = ""
                ch_num = _as_int(chapter.group(1))
                ch_token = str(ch_num) if ch_num is not None else chapter.group(1)
                current_chapter = ch_token
                skeleton.append(
                    {
                        "kind": "chapter",
                        "label": f"第{ch_token}章 {chapter.group(2).strip()}",
                        "print_page": stripped_page,
                    }
                )
                continue
            part = PART_LINE.match(probe) or PART_LINE.match(line)
            if part:
                last_section = ""
                part_num = _as_int(part.group(1))
                title = _fix_group_title(part.group(2) or "")
                if part_num is not None:
                    label = _part_label(part_num, title)
                else:
                    label = f"第{part.group(1)}部分 {title}".strip()
                current_chapter = f"bu{part_num if part_num is not None else part.group(1)}"
                skeleton.append({"kind": "chapter", "label": label, "print_page": stripped_page})
                continue
            jie = SECTION_JIE_LINE.match(probe) or SECTION_JIE_LINE.match(line)
            if jie:
                last_section = f"jie:{current_chapter or 0}:{_as_int(jie.group(1))}"
                skeleton.append(
                    {
                        "kind": "section",
                        "label": probe,
                        "num": last_section,
                        "print_page": stripped_page,
                        "level": 1,
                    }
                )
                continue
            if _is_intro_heading(line) or _is_intro_heading(probe):
                last_section = ""
                current_chapter = "yinlun"
                skeleton.append(
                    {
                        "kind": "chapter",
                        "label": _printed_intro_label(probe or line),
                        "print_page": stripped_page,
                    }
                )
                continue
            if _is_appendix_heading(line) or _is_appendix_heading(probe):
                last_section = ""
                current_chapter = "fulu"
                skeleton.append({"kind": "chapter", "label": "附录", "print_page": stripped_page})
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
                        "label": f"{star}{num} {_trim_leader(title)}",
                        "num": num,
                        "parent": _parent_of_dot3(num),
                        "print_page": int(print_page),
                    }
                )
                continue
            section = TOC_SECTION.match(line)
            if not section:
                bare = re.match(r"^(\*?)(\d+\.\d+)(?!\.\d)\s+(.+)$", probe)
                if bare and stripped_page is not None:
                    section = bare
            if section:
                if len(section.groups()) == 4:
                    star, num, title, print_page = section.groups()
                    print_page = int(print_page)
                else:
                    star, num, title = section.groups()
                    print_page = stripped_page
                last_section = num
                skeleton.append(
                    {
                        "kind": "section",
                        "label": f"{star}{num} {_trim_leader(title)}",
                        "num": num,
                        "print_page": print_page,
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
            hit = _lex_hit(line)
            if hit is not None and hit.id == "named_cn" and not _rejected_title(probe):
                last_section = ""
                skeleton.append(
                    {
                        "kind": "named",
                        "label": probe,
                        "print_page": stripped_page,
                    }
                )
                continue
            if hit is not None and hit.id == "appendix_roman" and not _rejected_title(probe):
                last_section = ""
                skeleton.append(
                    {
                        "kind": "named",
                        "label": probe,
                        "print_page": stripped_page,
                        "num": f"附录{probe[:1]}",
                    }
                )
                continue
            simple = TOC_SIMPLE_SUB.match(line)
            if simple and not _rejected_title(f"{simple.group(2)} {simple.group(3)}"):
                star, num, title, print_page = simple.groups()
                if last_section and "." in str(last_section):
                    catalog_subs.append(
                        {
                            "kind": "simple",
                            "label": f"{star}{num} {_trim_leader(title)}",
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
                            "label": f"{star}{num} {_trim_leader(title)}",
                            "num": num,
                            "print_page": int(print_page),
                        }
                    )
                continue
            skip_prefix = False
            lex = _lexicon()
            if lex is not None:
                try:
                    skip_prefix = bool(lex.has_skip_prefix(probe))
                except Exception:
                    skip_prefix = False
            if (
                stripped_page is not None
                and probe
                and not _is_contents_title(probe)
                and not _rejected_title(probe)
                and not _is_column_title(probe)
                and not skip_prefix
            ):
                last_section = ""
                skeleton.append(
                    {
                        "kind": "named",
                        "label": probe,
                        "print_page": stripped_page,
                    }
                )
    return toc_pages, _dedupe_catalog_rows(skeleton), _dedupe_catalog_rows(catalog_subs)


def _dedupe_catalog_rows(rows: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for row in rows:
        key = (_norm_title(str(row.get("label") or "")), row.get("print_page"), str(row.get("kind") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _looks_like_chapter_title(line: str, titles: set[str]) -> bool:
    compact = _norm_title(line)
    return compact in titles or line in titles or _chapter_key(line) in titles


def _title_core(label: str) -> str:
    return _norm_title(re.sub(r"^\*?\d+(?:\.\d+)*\s*", "", label or ""))


_JIE_HEAD = re.compile(rf"^第\s*({CN_NUM_TOKEN})\s*节\s*")


def _jie_title_core(label: str) -> str:
    return _norm_title(_JIE_HEAD.sub("", label or ""))


def _assign_jie_by_title(jie_rows: list[dict], body_jie: list[tuple[int, str]]) -> None:
    """Pair catalog 「第N节」 to body headings by title, not by zip order.

    Every chapter restarts at 第1节. Zipping the two lists in reading order
    jumps as soon as the body scan picks up one extra heading.
    """
    unused = list(enumerate(body_jie))
    for item in jie_rows:
        want = _jie_title_core(str(item.get("label") or ""))
        if not want:
            continue
        hit_i = None
        for pos, (index, (pdf_page, title)) in enumerate(unused):
            got = _norm_title(title)
            if got == want or (len(want) >= 6 and (want in got or got in want)):
                item["pdf_page"] = pdf_page
                hit_i = pos
                break
        if hit_i is not None:
            unused.pop(hit_i)


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
            try:
                kid_level = int(kid.get("level") or 2)
            except (TypeError, ValueError):
                kid_level = 2
            out.append(
                {
                    "kind": "simple",
                    "label": text,
                    "num": num,
                    "pdf_page": pdf_page,
                    "print_page": print_page,
                    "level": kid_level,
                    # 来自 toc_agent.json = toc_extract 从正文扒的，不是书里印的目录
                    "from_body": True,
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


def _toc_diff_summary(prev: list, nxt: list) -> dict:
    old_map = {
        (str(item.get("label") or ""), item.get("print_page")): item.get("level")
        for item in prev
        if isinstance(item, dict)
    }
    new_map = {
        (str(item.get("label") or ""), item.get("print_page")): item.get("level")
        for item in nxt
        if isinstance(item, dict)
    }
    added = [k for k in new_map if k not in old_map]
    removed = [k for k in old_map if k not in new_map]
    changed = [k for k in new_map if k in old_map and old_map[k] != new_map[k]]
    return {"added": len(added), "removed": len(removed), "level_changed": len(changed)}


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
    printed = _load_printed_toc(book)
    catalog_subs: list[dict] = []
    if printed.get("present"):
        toc_pages = list(printed.get("toc_pages") or [])
        skeleton = _skeleton_from_printed(list(printed.get("entries") or []))
    else:
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
        chapter_titles.add(_chapter_key(label))
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
    body_jie: list[tuple[int, str]] = []
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
            printed = _printed_intro_label(lines[0])
            chapter_openers.setdefault(printed, pdf_page)
            chapter_openers.setdefault("引论", pdf_page)
            chapter_openers.setdefault("绪言", pdf_page)
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
            jie_line = line
            lex = _lexicon()
            repair = getattr(lex, "repair_jie_heading", None) if lex is not None else None
            if callable(repair):
                try:
                    jie_line = repair(line)
                except Exception:
                    jie_line = line
            jie = SECTION_JIE_LINE.match(jie_line)
            if jie:
                body_jie.append((pdf_page, jie.group(2).strip() or jie_line))
            part = PART_LINE.match(line)
            if part:
                part_num = _as_int(part.group(1))
                if part_num is not None:
                    chapter_openers.setdefault(_norm_title(_part_label(part_num)), pdf_page)
            if _looks_like_chapter_title(line, chapter_titles):
                chapter_openers.setdefault(_chapter_key(line), pdf_page)
                chapter_openers.setdefault(_norm_title(line), pdf_page)

        for line in lines:
            if BODY_DOT3.match(line) or BODY_SECTION.match(line) or SECTION_JIE_LINE.match(line):
                continue
            sub = SUBSECTION.match(line)
            if not sub:
                continue
            _star, num, title = sub.groups()
            if _rejected_title(f"{num} {title}") or _rejected_title(title):
                continue
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

    jie_rows = [
        item
        for item in skeleton
        if item.get("kind") == "section" and str(item.get("num") or "").startswith("jie:")
    ]
    _assign_jie_by_title(jie_rows, body_jie)
    # 不写回 body_sections：它按 num 索引 `1.1` 这类小数节号，`第N节` 的正文页码
    # 已经落在 item["pdf_page"] 上，再进这个字典只会多一次跨章覆盖的机会。

    offsets: list[int] = []
    for item in skeleton:
        if item["kind"] not in {"section", "named"}:
            continue
        pdf_page = int(item.get("pdf_page") or 0) or body_sections.get(str(item.get("num") or ""), 0)
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
    if front_pdf and not any(_norm_title(str(item.get("label") or "")) == "前言" for item in skeleton):
        backbone.append(_entry("前言", None, 0, front_pdf, is_chapter=True))

    def _has_backbone_label(label: str) -> bool:
        want = _norm_title(label)
        return any(_norm_title(str(item.get("label") or "")) == want for item in backbone)

    last_pdf = 0
    if not skeleton and agent_skeleton:
        used_agent_skeleton = True
        for row in _backbone_from_agent(agent_skeleton):
            backbone.append(row)
            last_pdf = int(row.get("pdf_page") or last_pdf)
    for index, item in enumerate(skeleton):
        if item["kind"] == "chapter":
            keys = [
                _chapter_key(item["label"]),
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
            # item["pdf_page"] 优先：`第N节` 的正文页在上面按阅读顺序逐个配过，
            # 而 body_sections 只按小数节号索引，跨章会串。
            pdf_page = (
                int(item.get("pdf_page") or 0)
                or body_sections.get(item["num"], 0)
                or print_to_pdf(item["print_page"])
            )
            last_pdf = pdf_page
            backbone.append(_entry(item["label"], item["print_page"], 1, pdf_page))
            continue

        pdf_page = int(item.get("pdf_page") or 0) or print_to_pdf(item["print_page"], last_pdf)
        last_pdf = pdf_page
        backbone.append(_entry(item["label"], item["print_page"], 1, pdf_page))

    if back_pdf and not _has_backbone_label("后记"):
        backbone.append(_entry("后记", None, 0, back_pdf, is_chapter=True))
    thanks_pdf = int(chapter_openers.get("致谢") or 0)
    if thanks_pdf and not _has_backbone_label("致谢"):
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
            kid_level = 2
            try:
                kid_level = int(sub.get("level") or 2)
            except (TypeError, ValueError):
                kid_level = 2
            entries.append(
                _entry(
                    sub["label"],
                    print_page,
                    kid_level,
                    pdf_page,
                    from_body=bool(sub.get("from_body")),
                )
            )

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
    prev_entries: list = []
    if dest.is_file():
        try:
            prev = json.loads(dest.read_text(encoding="utf-8"))
            if isinstance(prev, dict) and isinstance(prev.get("entries"), list):
                prev_entries = prev["entries"]
        except (OSError, json.JSONDecodeError):
            prev_entries = []
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(dest)

    levels: dict[str, int] = {}
    for item in entries:
        key = str(item.get("level"))
        levels[key] = levels.get(key, 0) + 1

    return {
        "ok": True,
        "book_id": book.name,
        "path": str(dest),
        "entries": len(entries),
        "levels": levels,
        "toc_diff": _toc_diff_summary(prev_entries, entries),
        "offset": offset,
        "toc_pages": toc_pages,
        "toc_pages_dirty": list(toc_pages),
        "recovered_heads": added_heads,
        "patched_toc": patched_toc,
        "note": "已按书页抽出章、节、小节和印张页，写入 toc.json。目录页由 write_toc_pages 落盘。",
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build textbooks/<id>/toc.json")
    parser.add_argument("--root", default="")
    parser.add_argument("--book-id", required=True)
    args = parser.parse_args()
    print(json.dumps(build_toc(args.root or str(Path.cwd()), args.book_id), ensure_ascii=False))
