from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


KIND_META = {
    "mineru_span": {
        "title": "MinerU JSON 残留",
        "layer": "source",
        "action": "polish",
        "surface_already": True,
        "hint": "源里还留着 {'type':'text'}。prepare 会剥一层；导出源文件或漏网写法仍会露出来。",
    },
    "mineru_img_alt": {
        "title": "图片 alt 里的 MinerU JSON",
        "layer": "source",
        "action": "polish",
        "surface_already": True,
        "hint": "图注写成了 JSON。apply 收成「图 x-x」这类短标题。",
    },
    "table_plus_shot": {
        "title": "表格后又跟了一张表的截图",
        "layer": "source",
        "action": "polish",
        "surface_already": True,
        "hint": "紧跟 </table> 的表截图。apply 按画面同一规则丢掉，并去掉重复图注。",
    },
    "formula_shot": {
        "title": "公式截图紧挨着 LaTeX",
        "layer": "source",
        "action": "polish",
        "surface_already": True,
        "hint": "OCR 把公式拍成图，下文已有 LaTeX。apply 按画面同一规则丢掉这类图。",
    },
    "bare_latex": {
        "title": "未包 $ 的 LaTeX",
        "layer": "source",
        "action": "polish",
        "surface_already": True,
        "hint": "正文里的裸命令或上下标。apply 按 wrapBareMath 同一套规则包上 $。",
    },
    "baked_html": {
        "title": "源里已写入画面 HTML",
        "layer": "info",
        "action": "unwrap",
        "surface_already": False,
        "hint": "kb-list-item 等写回了 pages/*.md。prepare 会跳过这些标签，内部裸公式再也包不上。",
    },
    "bare_in_kb": {
        "title": "画面 HTML 里还有裸公式",
        "layer": "source",
        "action": "unwrap",
        "surface_already": False,
        "hint": "apply=unwrap 把标签拆回正文，让画面重新处理。",
    },
    "display_in_p": {
        "title": "段落标签里塞了 $$ 块公式",
        "layer": "source",
        "action": "unwrap",
        "surface_already": False,
        "hint": "列表项里的 $$ 会撑破行高。拆标签后，单行公式收成 $...$。",
    },
    "broken_dollar_colon": {
        "title": "错误定界 $:",
        "layer": "source",
        "action": "polish",
        "surface_already": False,
        "hint": "求证$:x$ 这类导入损伤。apply 改成 $x$。",
    },
    "empty_math": {
        "title": "空的 $$ 块",
        "layer": "source",
        "action": "polish",
        "surface_already": False,
        "hint": "空公式框。apply 删掉。",
    },
    "broken_math": {
        "title": "美元符过早闭合",
        "layer": "source",
        "action": "agent",
        "surface_already": False,
        "hint": "像 $\\angle B$'A'C'。对照 PDF 再改，工具不自动猜。",
    },
    "html_entity": {
        "title": "正文里的 HTML 实体",
        "layer": "source",
        "action": "polish",
        "surface_already": False,
        "hint": "A&#x27;B 在公式里会碎。apply 还原成字符。",
    },
    "ocr_parallel": {
        "title": "平行符号被认成 / /",
        "layer": "source",
        "action": "polish",
        "surface_already": False,
        "hint": "BB_1 / / AA_1。apply 收成 \\parallel。",
    },
    "odd_dollar": {
        "title": "未配对的 $",
        "layer": "source",
        "action": "agent",
        "surface_already": False,
        "hint": "少写或多写了美元符。对照 PDF。",
    },
    "noise_line": {
        "title": "空标题或噪声行",
        "layer": "source",
        "action": "polish",
        "surface_already": True,
        "hint": "单独一行的 # / 第 / 例 / ?。apply 删掉。",
    },
    "text_dollar": {
        "title": "\\text 里嵌了 $",
        "layer": "source",
        "action": "polish",
        "surface_already": False,
        "hint": "\\text {$或$} 或 \\text {$$或$$} 会把一组公式撕成两截。apply 收成 \\text{或}。",
    },
    "packed_math": {
        "title": "多项挤在同一对 $",
        "layer": "source",
        "action": "polish",
        "surface_already": False,
        "hint": "(1)$... (2)...$ 拆成列表项时会撕开定界符。apply 按题号拆开。",
    },
    "blank_in_math": {
        "title": "公式里的填空下划线",
        "layer": "source",
        "action": "polish",
        "surface_already": False,
        "hint": "____ 在 $ 里会变成下标。apply 改成 \\underline{\\quad}。",
    },
    "bare_blank": {
        "title": "未包 $ 的填空式",
        "layer": "source",
        "action": "polish",
        "surface_already": False,
        "hint": "0 ____ N、1 Z 这类隶属填空。apply 包上 $ 并收成正体数集。",
    },
    "math_html_rel": {
        "title": "公式里的裸 < >",
        "layer": "source",
        "action": "polish",
        "surface_already": False,
        "hint": "x < 2 的 < 会被当成 HTML 标签，整段公式露成源码。apply 改成 \\lt / \\gt。",
    },
}

MINERU_SPAN = re.compile(
    r"\{\s*['\"]type['\"]\s*:\s*['\"][^'\"]+['\"]\s*,\s*['\"]content['\"]"
    r"\s*:\s*['\"]([^'\"]*)['\"]\s*\}"
)
IMG = re.compile(r"!\[([\s\S]*?)\]\(([^)]+)\)")
TABLE = re.compile(r"<table\b[\s\S]*?</table>", re.I)
KB_P = re.compile(
    r'<p class="(kb-list-item|kb-solution|kb-solution-tail|kb-lead)">([\s\S]*?)</p>',
    re.I,
)
LABEL_SPAN = re.compile(r'<span class="kb-(?:solution|lead)-label">([\s\S]*?)</span>\s*', re.I)
MATH_CHUNK = re.compile(
    r"\$\$[\s\S]+?\$\$|\$[^$\n]+?\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)"
)
BARE_CMD = re.compile(
    r"\\(?:frac|sqrt|mathbf|varnothing|cdot|times|leqslant|geqslant|leq|geq|"
    r"neq|in|notin|mid|subset|subseteq|cup|cap|overline|overrightarrow|"
    r"triangle|angle|parallel|perp|pm|cdots|ldots|sin|cos|tan|log|ln|"
    r"begin|end|left|right|mathrm|text|omega|varphi|phi|pi|alpha|beta|"
    r"gamma|theta|infty)(?![a-zA-Z])"
)
BARE_SUB = re.compile(r"[A-Za-z0-9]\\?_\{|[A-Za-z]\^\{")
BROKEN_TAIL = re.compile(r"\$'[A-Za-z]|[A-Za-z]\$'[A-Za-z]")
OCR_PARALLEL = re.compile(r"([A-Za-z0-9}\\])\s*/\s*/\s*([A-Za-z\\])")
ENTITY = re.compile(r"&#(?:x[0-9a-fA-F]+|\d+);")
NOISE = re.compile(r"^(?:#+\s*|第|例|[?？])\s*$")
DISPLAY_IN_P = re.compile(r"<p\b[^>]*>[\s\S]*?\$\$", re.I)
EMPTY_MATH = re.compile(r"\$\$\s*\$\$")
DOLLAR_COLON = re.compile(r"\$:")
TABLE_SHOT_TAIL = re.compile(r"\s*\n+\s*!\[([^\]]*)\]\([^)]+\)")
TABLE_SHOT_BLOCK = re.compile(
    r"(</table>)(\s*\n+\s*!\[([^\]]*)\]\([^)]+\)[ \t]*(?:\n+[ \t]*\*[^\n*]+\*[ \t]*)?)",
    re.I,
)
CJK_SPLIT = re.compile(
    r"([\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff，。；：、！？（）《》"
    r"\u201c\u201d\u2018\u2019\"'…—]+)"
)
BARE_CMD_WRAP = re.compile(r"\\(?:[a-zA-Z]+|[{}\[\]])")
UNICODE_MATH = re.compile(r"[√∞∪∩≤≥≠παβγθ∈∉∅]")
LETTER_REL = re.compile(r"[a-zA-Z]\s*[=<>]")
PROTECTED_MATH = re.compile(
    r"\$\$[\s\S]+?\$\$|\$[^$\n]+?\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|"
    r"!\[[\s\S]*?\]\([^)]+\)|"
    r'<div class="kb-table-wrap"[\s\S]+?</div>|'
    r'<div class="kb-page-toc-heading"[\s\S]+?</div>|'
    r"<table[\s\S]+?</table>|"
    r"<img\b[^>]*>|"
    r"<nav[\s\S]+?</nav>|"
    r'<p class="kb-(?:list-item|solution|solution-tail|lead|page-toc-heading)"[\s\S]+?</p>',
    re.I,
)
TEXT_OR_ENV = re.compile(
    r"\\text\s*\{[^{}]*\}|\\begin\{([a-zA-Z*]+)\}[\s\S]*?\\end\{\1\}"
)
TEXT_DOLLAR = re.compile(r"\\text\s*\{\s*\$")
ITEM_SPLIT = re.compile(r"(?<=[.;。；,，\s\)\d])(?=[（(]\s*\d+\s*[）)])")
FILL_BLANK = re.compile(r"_{2,}")
BARE_MEMBER = re.compile(
    r"(?:_{2,}\s*(?:\\mathbf\{[NZQR]\}|[NZQR])\b|(?:^|[\s（(])[-+]?\d+\s+[NZQR]\s*[;；.。])"
)
INLINE_MATH = re.compile(r"(?<!\$)\$([^$\n]+)\$(?!\$)")
DISPLAY_MATH = re.compile(r"\$\$([\s\S]+?)\$\$")
SUB_DOLLAR = re.compile(r"_\{\$([\u4e00-\u9fff]+)\}")
NUM_SET = re.compile(r"^([-+]?\d+)\s+([NZQR])(?![a-zA-Z])(\s*[;；.。])?$")
SET_AFTER_BLANK = re.compile(
    r"(\\underline\{\\quad\})\s*(?:\\mathbf\{([NZQR])\}|([NZQR]))(?![a-zA-Z])"
)


def _agent_home(root: str) -> Path:
    base = Path(root).resolve() if root else Path.cwd()
    if (base / "textbooks").is_dir():
        return base
    if base.name == "textbooks":
        return base.parent
    if base.parent.name == "textbooks":
        return base.parent.parent
    return base


def _load_meta(book: Path) -> dict:
    path = book / "meta.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _list_books(base: Path, book_id: str) -> list[tuple[Path, dict]]:
    textbooks = base / "textbooks"
    if not textbooks.is_dir():
        return []
    needle = (book_id or "").strip()
    found: list[tuple[Path, dict]] = []
    for folder in sorted(p for p in textbooks.iterdir() if p.is_dir()):
        if not (folder / "pages").is_dir():
            continue
        meta = _load_meta(folder)
        label = str(meta.get("label") or folder.name)
        filename = str(meta.get("filename") or "")
        if needle and needle not in {folder.name, label} and needle not in label and needle not in filename:
            continue
        found.append((folder, meta))
    return found


def _page_no(path: Path) -> str:
    return path.stem


def _snippet(text: str, limit: int = 96) -> str:
    compact = re.sub(r"\s+", " ", (text or "").strip())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _add(hits: dict[str, list], kind: str, page: str, raw: str) -> None:
    bucket = hits[kind]
    if len(bucket) >= 40:
        return
    bucket.append({"page": page, "snippet": _snippet(raw)})


def _unprotected(text: str) -> str:
    stripped = IMG.sub(" ", text or "")
    stripped = TABLE.sub(" ", stripped)
    stripped = MATH_CHUNK.sub(" ", stripped)
    return stripped


def _scan_page(page: str, text: str) -> dict[str, list]:
    hits: dict[str, list] = defaultdict(list)
    if MINERU_SPAN.search(text):
        _add(hits, "mineru_span", page, next(MINERU_SPAN.finditer(text)).group(0))
        for match in IMG.finditer(text):
            if MINERU_SPAN.search(match.group(1)):
                _add(hits, "mineru_img_alt", page, match.group(0))
                break
    for table in TABLE.finditer(text):
        img = TABLE_SHOT_TAIL.match(text[table.end() :])
        if not img:
            continue
        alt = (img.group(1) or "").strip()
        if _is_table_shot_alt(alt):
            _add(hits, "table_plus_shot", page, alt or "插图")
            break
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        alt_m = re.match(r"!\[([^\]]*)\]\([^)]+\)\s*$", line.strip())
        if not alt_m:
            continue
        alt = alt_m.group(1).strip()
        if alt.startswith("图") or alt.startswith("表"):
            continue
        nxt = ""
        for later in lines[i + 1 :]:
            if later.strip():
                nxt = later.strip()
                break
        open_nxt = _unprotected(nxt)
        if nxt and (BARE_CMD.search(open_nxt) or BARE_SUB.search(open_nxt)):
            if alt in {"插图", "公式", ""} or MINERU_SPAN.search(alt):
                _add(hits, "formula_shot", page, alt or nxt)
                break
    unprotected = _unprotected(text)
    if BARE_CMD.search(unprotected) or BARE_SUB.search(unprotected):
        probe = next(BARE_CMD.finditer(unprotected), None) or next(BARE_SUB.finditer(unprotected), None)
        _add(hits, "bare_latex", page, probe.group(0) if probe else unprotected)
    if 'class="kb-' in text or "class='kb-" in text:
        _add(hits, "baked_html", page, "kb-*")
        for block in KB_P.finditer(text):
            inner = LABEL_SPAN.sub(" ", block.group(2))
            inner = re.sub(r"<[^>]+>", "", inner)
            if BARE_CMD.search(inner) or BARE_SUB.search(inner):
                _add(hits, "bare_in_kb", page, inner)
                break
        if DISPLAY_IN_P.search(text):
            _add(hits, "display_in_p", page, "$$ inside <p>")
    if DOLLAR_COLON.search(text):
        _add(hits, "broken_dollar_colon", page, "$:")
    if EMPTY_MATH.search(text):
        _add(hits, "empty_math", page, "$$ $$")
    if BROKEN_TAIL.search(text):
        _add(hits, "broken_math", page, next(BROKEN_TAIL.finditer(text)).group(0))
    if ENTITY.search(text):
        _add(hits, "html_entity", page, next(ENTITY.finditer(text)).group(0))
    if OCR_PARALLEL.search(text):
        _add(hits, "ocr_parallel", page, next(OCR_PARALLEL.finditer(text)).group(0))
    dollars = text.replace("$$", "")
    if dollars.count("$") % 2 == 1:
        _add(hits, "odd_dollar", page, "unpaired $")
    for line in lines:
        if NOISE.match(line.strip()):
            _add(hits, "noise_line", page, line)
            break
    if TEXT_DOLLAR.search(text):
        _add(hits, "text_dollar", page, next(TEXT_DOLLAR.finditer(text)).group(0))
    for match in INLINE_MATH.finditer(text):
        body = match.group(1)
        if r"\begin{" in body:
            continue
        if ITEM_SPLIT.search(body):
            _add(hits, "packed_math", page, match.group(0))
            break
    if INLINE_MATH.search(text) and any(
        FILL_BLANK.search(m.group(1)) for m in INLINE_MATH.finditer(text)
    ):
        probe = next(
            (m.group(0) for m in INLINE_MATH.finditer(text) if FILL_BLANK.search(m.group(1))),
            "",
        )
        _add(hits, "blank_in_math", page, probe)
    if BARE_MEMBER.search(unprotected):
        _add(hits, "bare_blank", page, next(BARE_MEMBER.finditer(unprotected)).group(0))
    if _has_raw_rel(text):
        _add(hits, "math_html_rel", page, "< or > in $")
    return hits


def _scan_book(book: Path, meta: dict, max_samples: int) -> dict:
    pages_dir = book / "pages"
    by_kind: dict[str, dict] = {}
    pages_with = 0
    issue_count = 0
    page_files = sorted(pages_dir.glob("*.md")) if pages_dir.is_dir() else []
    for path in page_files:
        page = _page_no(path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits = _scan_page(page, text)
        if not hits:
            continue
        pages_with += 1
        for kind, rows in hits.items():
            issue_count += len(rows)
            slot = by_kind.setdefault(
                kind,
                {
                    **KIND_META.get(kind, {"title": kind, "layer": "source", "action": "agent"}),
                    "kind": kind,
                    "pages": 0,
                    "hits": 0,
                    "samples": [],
                },
            )
            slot["pages"] += 1
            slot["hits"] += len(rows)
            for row in rows:
                if len(slot["samples"]) < max_samples:
                    slot["samples"].append(row)
    kinds = sorted(by_kind.values(), key=lambda x: (-int(x["hits"]), str(x["kind"])))
    return {
        "book_id": book.name,
        "label": str(meta.get("label") or book.name),
        "page_count": len(page_files),
        "pages_with_issues": pages_with,
        "issue_count": issue_count,
        "by_kind": kinds,
    }


def _mineru_caption(alt: str) -> str:
    spans = [s.strip() for s in MINERU_SPAN.findall(alt or "") if s.strip()]
    if not spans:
        return (alt or "").strip()
    pictured = next((s for s in spans if re.match(r"^[图表]\s*\d", s)), None)
    if pictured:
        return pictured
    words = [s for s in spans if not re.match(r"^\(\d+\)$", s) and len(s) > 1]
    return words[0] if words else spans[-1]


def _polish_mineru(text: str) -> str:
    def img_repl(_m: re.Match) -> str:
        alt, url = _m.group(1), _m.group(2)
        if not MINERU_SPAN.search(alt):
            return _m.group(0)
        return f"![{_mineru_caption(alt)}]({url})"

    text = IMG.sub(img_repl, text)
    return MINERU_SPAN.sub(r"\1", text)


def _drop_formula_shots(text: str) -> str:
    lines = (text or "").splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        img = re.match(r"!\[([^\]]*)\]\(([^)]+)\)\s*$", line.strip())
        if img:
            alt = img.group(1).strip()
            nxt = ""
            for later in lines[i + 1 :]:
                if later.strip():
                    nxt = later.strip()
                    break
            drop_alt = alt in {"插图", "公式", ""} or bool(MINERU_SPAN.search(alt))
            open_nxt = _unprotected(nxt)
            if drop_alt and not alt.startswith("图") and nxt and (
                BARE_CMD.search(open_nxt) or BARE_SUB.search(open_nxt)
            ):
                i += 1
                continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _is_table_shot_alt(alt: str) -> bool:
    s = (alt or "").strip()
    if re.match(r"^图\s*\d", s):
        return False
    return (not s) or s.startswith("表") or s == "插图" or "type" in s or bool(MINERU_SPAN.search(s))


def _drop_table_shots(text: str) -> str:
    def repl(match: re.Match) -> str:
        return match.group(1) if _is_table_shot_alt(match.group(3)) else match.group(0)

    out = text or ""
    for _ in range(6):
        nxt = TABLE_SHOT_BLOCK.sub(repl, out)
        if nxt == out:
            break
        out = nxt
    return out


def _drop_noise_lines(text: str) -> str:
    return "\n".join(line for line in (text or "").splitlines() if not NOISE.match(line.strip()))


def _is_math_run(part: str) -> bool:
    text = part.strip()
    if not text or "$" in text:
        return False
    if text.startswith("![") or text.startswith("<") or text.startswith("#"):
        return False
    if IMG.search(text) or text.startswith("http") or text.startswith("/api/"):
        return False
    marker = re.match(r"^((?:[（(]\s*\d+\s*[）)]|\d+\.)\s*)(.+)$", text)
    if marker:
        text = marker.group(2).strip()
        if not text:
            return False
    if BARE_CMD_WRAP.search(text) or BARE_SUB.search(text):
        return True
    if FILL_BLANK.search(text) and re.search(r"[A-Za-z0-9\\√π]", text):
        if re.search(r"[\u4e00-\u9fff]", text) and not BARE_CMD_WRAP.search(text):
            return False
        return True
    if re.fullmatch(r"[-+]?\d+\s+[NZQR]\s*[;；.。]?", text):
        return True
    if UNICODE_MATH.search(text) and re.search(r"[=<>+\-*/^_{}\\]|[a-zA-Z]", text):
        return True
    if LETTER_REL.search(text) and re.search(r"[+\-*/^_{}\\]|\\", text):
        return True
    if re.search(r"[a-zA-Z]\s*[<>]=?\s*[-+]?\d", text):
        return True
    return False


def _wrap_run(part: str) -> str:
    text = part.strip()
    lead = part[: len(part) - len(part.lstrip())]
    tail = part[len(part.rstrip()) :]
    marker = re.match(r"^((?:[（(]\s*\d+\s*[）)]|\d+\.)\s*)(.+)$", text)
    if marker and _is_math_run(marker.group(2)):
        lead += marker.group(1)
        text = marker.group(2)
    if text[:1] in {":", "："}:
        lead += text[0]
        text = text[1:].lstrip()
        if not text or not _is_math_run(text):
            return f"{lead}{text}{tail}"
    if "\\begin{" in text:
        return f"{lead}$${text}$${tail}"
    return f"{lead}${text}${tail}"


def _stash_text_env(text: str) -> tuple[str, list[str]]:
    held: list[str] = []

    def keep(match: re.Match) -> str:
        held.append(match.group(0))
        return f"\x00H{len(held) - 1}\x00"

    return TEXT_OR_ENV.sub(keep, text or ""), held


def _unstash_text_env(text: str, held: list[str]) -> str:
    def restore(match: re.Match) -> str:
        idx = int(match.group(1))
        return held[idx] if 0 <= idx < len(held) else match.group(0)

    return re.sub(r"\x00H(\d+)\x00", restore, text or "")


def _wrap_unprotected(text: str) -> str:
    if not text:
        return text
    if "\n" in text:
        return "\n".join(_wrap_unprotected(line) for line in text.split("\n"))
    masked, held = _stash_text_env(text)
    pieces: list[str] = []
    for part in CJK_SPLIT.split(masked):
        raw = _unstash_text_env(part, held)
        pieces.append(_wrap_run(raw) if _is_math_run(raw) else raw)
    return "".join(pieces)


def _wrap_bare_math(text: str) -> str:
    raw = text or ""
    if not raw:
        return raw
    out: list[str] = []
    cursor = 0
    for match in PROTECTED_MATH.finditer(raw):
        out.append(_wrap_unprotected(raw[cursor : match.start()]))
        out.append(match.group(0))
        cursor = match.end()
    out.append(_wrap_unprotected(raw[cursor:]))
    return "".join(out)


TOC_NAV = re.compile(r'<nav class="kb-page-toc">[\s\S]*?</nav>', re.I)
TOC_ROW = re.compile(r'<li class="kb-page-toc-row[^"]*"[^>]*>([\s\S]*?)</li>', re.I)
TOC_TITLE = re.compile(r'<span class="kb-page-toc-title">([\s\S]*?)</span>', re.I)
TOC_PAGE = re.compile(r'<span class="kb-page-toc-page">([\s\S]*?)</span>', re.I)
TABLE_WRAP = re.compile(r'<div class="kb-table-wrap">([\s\S]*?)</div>', re.I)
TABLE_FOOT = re.compile(r"\$?<p class=\"kb-table-foot\">([\s\S]*?)</p>\$?", re.I)
HEADING_IN_MATH = re.compile(r"\$\$\s*#{1,6}\s+")
HEADING_DOLLAR = re.compile(r"^(#{1,6})\${1,2}\s*", re.M)
TRAILING_EXTRA_DOLLAR = re.compile(
    r"(?<!\$)(\$[^$\n]+)\$\$(?=[\u4e00-\u9fff]|\s*$)",
    re.M,
)
UNCLOSED_DISPLAY = re.compile(
    r"(?<=[\u4e00-\u9fff，。；：、）)])\$\$([^$\n]+?)(\n+)(?=#|[A-D]\.)"
)
OPTION_WRAPPED_CJK = re.compile(r"([A-D])\.\$([\u4e00-\u9fff]+?)\$")
OPTION_LEAD_DOLLAR = re.compile(r"^\$([A-D])\.(?=\s*(?:\\|[A-Za-z]))", re.M)
BROKEN_INTERVAL = re.compile(
    r"\$((?:[A-Za-z]\\in\s*)?[\(\[][^$\n\[\]]{0,80}\])(?!\$)(?=[，。；：、）)与,\s]|$)(?![^$\n]*\$)"
)


def _map_math(text: str, fn) -> str:
    def display(match: re.Match) -> str:
        return "$$" + fn(match.group(1)) + "$$"

    def inline(match: re.Match) -> str:
        return "$" + fn(match.group(1)) + "$"

    out = DISPLAY_MATH.sub(display, text or "")
    return INLINE_MATH.sub(inline, out)


def _fix_text_dollars(text: str) -> str:
    def repl(match: re.Match) -> str:
        return r"\text{" + (match.group(1) or "").strip() + "}"

    return re.sub(r"\\text\s*\{\s*\$+\s*([^$\\{}]*?)\s*\$*\s*\}", repl, text or "")


def _fix_sub_dollars(text: str) -> str:
    out = re.sub(
        r"\$([^$\n]*?)_\{\$([\u4e00-\u9fff]+)\}",
        r"$\1_{\\text{\2}}$",
        text or "",
    )
    return SUB_DOLLAR.sub(r"_{\\text{\1}}", out)


def _split_packed_math(text: str) -> str:
    def repl(match: re.Match) -> str:
        body = match.group(1)
        if r"\begin{" in body or not ITEM_SPLIT.search(body):
            return match.group(0)
        chunks = ITEM_SPLIT.split(body)
        if len(chunks) < 2:
            return match.group(0)
        out: list[str] = []
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            marked = re.match(r"^([（(]\s*\d+\s*[）)])\s*(.*)$", chunk)
            if marked:
                label, rest = marked.group(1), marked.group(2).strip()
                out.append(f"{label}${rest}$" if rest else label)
            else:
                out.append(f"${chunk}$")
        return " ".join(out)

    return INLINE_MATH.sub(repl, text or "")


def _normalize_math_body(body: str) -> str:
    out = re.sub(r"_{2,}", r"\\underline{\\quad}", body or "")
    out = SET_AFTER_BLANK.sub(
        lambda m: m.group(1) + r" \mathbf{" + (m.group(2) or m.group(3)) + "}",
        out,
    )
    matched = NUM_SET.match(out.strip())
    if matched:
        punct = matched.group(3) or ""
        out = f"{matched.group(1)} \\underline{{\\quad}} \\mathbf{{{matched.group(2)}}}{punct}"
    return out


def _escape_rel_body(body: str) -> str:
    if not body or ("<" not in body and ">" not in body):
        return body
    out: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        pair = body[i : i + 2]
        if pair == "<=":
            out.append(r"\leqslant ")
            i += 2
            continue
        if pair == ">=":
            out.append(r"\geqslant ")
            i += 2
            continue
        ch = body[i]
        if ch == "<":
            out.append(r"\lt ")
            i += 1
            continue
        if ch == ">":
            out.append(r"\gt ")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _has_raw_rel(text: str) -> bool:
    for match in list(DISPLAY_MATH.finditer(text or "")) + list(INLINE_MATH.finditer(text or "")):
        if re.search(r"[<>]", match.group(1)):
            return True
    return False


def _close_math_at_prose(text: str) -> str:
    raw = text or ""
    out: list[str] = []
    i = 0
    n = len(raw)
    in_math = False
    display = False
    brace = 0
    text_base = -1
    out_dollar = None
    while i < n:
        if raw.startswith("$$", i):
            in_math = not in_math
            display = in_math
            brace = 0
            text_base = -1
            out_dollar = len(out) if in_math else None
            out.append("$$")
            i += 2
            continue
        ch = raw[i]
        if ch == "$":
            in_math = not in_math
            display = False
            brace = 0
            text_base = -1
            out_dollar = len(out) if in_math else None
            out.append("$")
            i += 1
            continue
        if ch == "\n" and in_math and not display:
            opened = "".join(out[out_dollar:]) if out_dollar is not None else ""
            if BARE_CMD_WRAP.search(opened) or re.search(r"[A-Za-z0-9_=]", opened):
                out.append("$")
            in_math = False
            brace = 0
            text_base = -1
            out.append("\n")
            i += 1
            continue
        if not in_math:
            out.append(ch)
            i += 1
            continue
        if raw.startswith(r"\text", i) and (i + 5 >= n or not raw[i + 5].isalpha()):
            j = i + 5
            while j < n and raw[j] in " \t":
                j += 1
            if j < n and raw[j] == "{":
                out.append(raw[i : j + 1])
                text_base = brace
                brace += 1
                i = j + 1
                continue
        if ch == "{":
            brace += 1
            out.append(ch)
            i += 1
            continue
        if ch == "}":
            brace -= 1
            out.append(ch)
            i += 1
            if text_base >= 0 and brace == text_base:
                text_base = -1
            continue
        if text_base < 0 and brace == 0 and not display:
            if re.match(r"\s*=[\u4e00-\u9fff]", raw[i:]):
                out.append("$")
                in_math = False
                continue
            if ch in "，。；" or "\u4e00" <= ch <= "\u9fff":
                out.append("$")
                in_math = False
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _close_lone_math(text: str) -> str:
    out = _close_math_at_prose(text or "")
    lines: list[str] = []
    for line in out.splitlines():
        stripped = line.strip()
        if (
            stripped.startswith("$")
            and not stripped.startswith("$$")
            and stripped.count("$") == 1
            and (BARE_CMD_WRAP.search(stripped) or r"\underline" in stripped)
        ):
            lines.append(line.rstrip() + "$")
            continue
        lines.append(line)
    return "\n".join(lines)


def _repair_formulas(text: str) -> str:
    out = _fix_text_dollars(text or "")
    out = _fix_sub_dollars(out)
    out = _split_packed_math(out)
    out = _map_math(out, _normalize_math_body)
    out = _map_math(out, _escape_rel_body)
    out = _close_lone_math(out)
    return out


def _close_unclosed_display(text: str) -> str:
    def repl(match: re.Match) -> str:
        body = match.group(1).strip().strip("$").strip()
        gap = match.group(2)
        if not body:
            return gap
        if not gap.endswith("\n\n"):
            gap = "\n\n"
        return f"${body}${gap}"

    return UNCLOSED_DISPLAY.sub(repl, text or "")


def _fix_broken_intervals(text: str) -> str:
    return BROKEN_INTERVAL.sub(r"$\1$", text or "")


def _plain(raw: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", raw or "")).strip()


def _unwrap_toc(text: str) -> str:
    def repl(match: re.Match) -> str:
        lines: list[str] = []
        for row in TOC_ROW.finditer(match.group(0)):
            inner = row.group(1)
            title_m = TOC_TITLE.search(inner)
            page_m = TOC_PAGE.search(inner)
            title = _plain(title_m.group(1) if title_m else inner)
            page = _plain(page_m.group(1) if page_m else "")
            if title and page:
                lines.append(f"{title} {page}")
            elif title:
                lines.append(title)
        return "\n".join(lines)

    return TOC_NAV.sub(repl, text)


def _unwrap_table_chrome(text: str) -> str:
    out = TABLE_WRAP.sub(r"\1", text or "")

    def foot_repl(match: re.Match) -> str:
        body = _plain(match.group(1)).strip("$").strip()
        return f"\n\n{body}" if body else ""

    out = TABLE_FOOT.sub(foot_repl, out)
    out = re.sub(r'\s*class="kb-table--fit"', "", out)
    out = re.sub(r"\s*class='kb-table--fit'", "", out)
    return out


def _unwrap_kb(text: str) -> str:
    def repl(match: re.Match) -> str:
        inner = LABEL_SPAN.sub(lambda m: m.group(1).strip() + " ", match.group(2))
        inner = re.sub(r"<[^>]+>", "", inner)
        return html.unescape(inner).strip()

    out = KB_P.sub(repl, text)
    out = _unwrap_toc(out)
    out = _unwrap_table_chrome(out)
    out = HEADING_IN_MATH.sub("$$\n", out)
    out = HEADING_DOLLAR.sub(r"\1 ", out)
    return out


def _shrink_inline_display(text: str) -> str:
    out: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("$$") and stripped.endswith("$$") and stripped.count("$$") == 2:
            out.append(line)
            continue

        def repl(match: re.Match) -> str:
            body = match.group(1).strip()
            if "\\begin" in body:
                return match.group(0)
            return f"${body}$"

        out.append(re.sub(r"\$\$([^$\n]+?)\$\$", repl, line))
    return "\n".join(out)


def _polish_page(text: str, apply_level: str) -> str:
    out = text or ""
    out = _polish_mineru(out)
    out = _drop_formula_shots(out)
    out = DOLLAR_COLON.sub("$", out)
    out = EMPTY_MATH.sub("", out)
    out = ENTITY.sub(lambda m: html.unescape(m.group(0)), out)
    out = OCR_PARALLEL.sub(r"\1 \\parallel \2", out)
    out = _drop_noise_lines(out)
    out = _drop_table_shots(out)
    out = HEADING_IN_MATH.sub("$$\n", out)
    out = HEADING_DOLLAR.sub(r"\1 ", out)
    out = OPTION_WRAPPED_CJK.sub(r"\1. \2$", out)
    out = OPTION_LEAD_DOLLAR.sub(r"\1. $", out)
    out = _close_unclosed_display(out)
    out = _fix_broken_intervals(out)
    out = _repair_formulas(out)
    out = _wrap_bare_math(out)
    out = _repair_formulas(out)
    out = TRAILING_EXTRA_DOLLAR.sub(r"\1$", out)
    out = DOLLAR_COLON.sub("$", out)
    out = EMPTY_MATH.sub("", out)
    if apply_level in {"unwrap", "all"}:
        out = _unwrap_kb(out)
        out = _shrink_inline_display(out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip() + ("\n" if text.endswith("\n") else "")


def _apply_level(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in {"", "0", "false", "no", "scan"}:
        return ""
    if value in {"unwrap", "2"}:
        return "unwrap"
    if value in {"all", "full"}:
        return "all"
    return "safe"


def _next_steps(books: list[dict]) -> list[str]:
    steps: list[str] = []
    kinds = {row["kind"] for book in books for row in book.get("by_kind", [])}
    if kinds & {
        "mineru_span",
        "mineru_img_alt",
        "formula_shot",
        "broken_dollar_colon",
        "empty_math",
        "html_entity",
        "ocr_parallel",
        "bare_latex",
        "table_plus_shot",
        "noise_line",
        "text_dollar",
        "packed_math",
        "blank_in_math",
        "bare_blank",
        "math_html_rel",
    }:
        steps.append("源损伤多：improve_render(apply='safe') 做机械清洗。")
    if kinds & {"baked_html", "bare_in_kb", "display_in_p"}:
        steps.append("源里写回了 kb-* HTML：improve_render(apply='unwrap') 拆回正文，让画面重新画。")
    if kinds & {"broken_math", "odd_dollar"}:
        steps.append("破损定界符：对照原版 PDF 逐处改，工具不自动猜。")
    if not steps:
        steps.append("扫描未发现已知问题。若画面仍难看，打开具体页再改插槽。")
    return steps


def _write_report(base: Path, payload: dict) -> str:
    traces = base / "traces"
    traces.mkdir(exist_ok=True)
    path = traces / "improve-render-last.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _apply_book(book: Path, apply_level: str) -> dict:
    changed = 0
    pages_dir = book / "pages"
    if not pages_dir.is_dir():
        return {"pages_changed": 0}
    for path in sorted(pages_dir.glob("*.md")):
        before = path.read_text(encoding="utf-8", errors="ignore")
        after = _polish_page(before, apply_level)
        if after != before:
            path.write_text(after, encoding="utf-8")
            changed += 1
    return {"pages_changed": changed}


def improve_render(
    book_id: str = "",
    apply: str = "",
    max_samples: str = "4",
    root: str = "",
) -> dict:
    """Scan textbook pages for render issues; optionally apply safe source polish."""
    base = _agent_home(root)
    try:
        sample_cap = max(1, min(int(max_samples or 4), 12))
    except (TypeError, ValueError):
        sample_cap = 4
    books = _list_books(base, book_id)
    if not books:
        return {
            "ok": False,
            "error": "没有找到教材。检查 textbooks/<id>/pages，或换书名 / book_id。",
            "book_id": book_id,
        }
    level = _apply_level(apply)
    applied: list[dict] = []
    if level:
        for folder, meta in books:
            result = _apply_book(folder, level)
            applied.append(
                {
                    "book_id": folder.name,
                    "label": str(meta.get("label") or folder.name),
                    "apply": level,
                    **result,
                }
            )
    scanned = [_scan_book(folder, meta, sample_cap) for folder, meta in books]
    payload = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "apply": level or "scan",
        "applied": applied,
        "books": scanned,
        "totals": {
            "books": len(scanned),
            "pages": sum(int(b["page_count"]) for b in scanned),
            "pages_with_issues": sum(int(b["pages_with_issues"]) for b in scanned),
            "issue_count": sum(int(b["issue_count"]) for b in scanned),
        },
        "next_steps": _next_steps(scanned),
        "note": "完整报告已写入 traces/improve-render-last.json。画面问题改 surfaces/，不要靠改 Markdown 修渲染。",
    }
    payload["report_path"] = _write_report(base, payload)
    return payload
