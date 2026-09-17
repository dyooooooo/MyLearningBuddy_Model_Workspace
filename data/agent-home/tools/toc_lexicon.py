"""Shared TOC lexicon: peel page suffix, then classify the leftover label.

单一实现，三处共用（见 doc/toc-code-ownership.md）：

- textbook-agent: `app/toc_lexicon.py` 是 shim，转调本文件
- textbook-agent 工具: `build_toc.py` / `toc_extract.py` 直接 `import toc_lexicon`
- learning-buddy-api: `app/kb/toc.py` 按 `/data/agent/tools/toc_lexicon.py` 路径加载

所以这里**不能** import `app.*`：buddy-api 容器里没有 textbook-agent 的 app 包。
词法数据本身在 `../configs/toc_lexicon.yaml`。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_TAIL = re.compile(r"[ .\u3000．…·•、]+$")

_cache_key = ""
_cache_data: dict[str, Any] = {}
_compiled: dict[str, re.Pattern[str]] = {}
_suffixes: list[re.Pattern[str]] = []
_line_rules: list[dict[str, Any]] = []
_body_rules: list[dict[str, Any]] = []
_column: set[str] = set()
_skip_prefixes: tuple[str, ...] = ()
_rejects: list[re.Pattern[str]] = []
_raw_for_js: dict[str, Any] = {}


@dataclass(frozen=True)
class LineHit:
    id: str
    kind: str
    level: int
    match: re.Match[str]


def _candidates() -> list[Path]:
    """`tools/` 的同级 `configs/`，两种上下文都成立：

    - `/data/agent/tools/`   -> `/data/agent/configs/`      （agent 与 buddy-api 运行态）
    - `workspace/tools/`     -> `workspace/configs/`        （镜像 seed 与本地测试）
    """
    here = Path(__file__).resolve()
    out = [here.parent.parent / "configs" / "toc_lexicon.yaml"]
    seed = Path("/data/agent/configs/toc_lexicon.yaml")
    if seed not in out:
        out.append(seed)
    return out


def lexicon_path() -> Path:
    override = os.getenv("TOC_LEXICON_PATH", "").strip()
    if override and Path(override).is_file():
        return Path(override)
    found = [p for p in _candidates() if p.is_file()]
    return found[0] if found else _candidates()[0]


def reset_for_tests() -> None:
    global _cache_key
    _cache_key = ""


def _stamp(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return f"{path}:{path.stat().st_mtime_ns}:{digest}"


def lexicon_stamp() -> str:
    _ensure()
    return _cache_key


def _compile(pattern: str, flags: str = "") -> re.Pattern[str]:
    bits = re.IGNORECASE if "i" in (flags or "").lower() else 0
    return re.compile(pattern, bits)


def _ensure() -> None:
    global _cache_key, _cache_data, _compiled, _suffixes, _line_rules, _body_rules
    global _column, _skip_prefixes, _rejects, _raw_for_js
    path = lexicon_path()
    key = _stamp(path)
    if key and key == _cache_key and _cache_data:
        return
    data: dict[str, Any] = {}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data = loaded
    compiled: dict[str, re.Pattern[str]] = {}
    suffixes: list[re.Pattern[str]] = []
    line_rules: list[dict[str, Any]] = []
    body_rules: list[dict[str, Any]] = []
    for row in data.get("page_suffix") or []:
        if not isinstance(row, dict) or not row.get("pattern"):
            continue
        suffixes.append(_compile(str(row["pattern"])))
    for row in data.get("lines") or []:
        if not isinstance(row, dict) or not row.get("pattern"):
            continue
        rid = str(row.get("id") or "")
        regex = _compile(str(row["pattern"]), str(row.get("flags") or ""))
        if rid:
            compiled[rid] = regex
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        line_rules.append(
            {
                "id": rid,
                "kind": str(row.get("kind") or "section"),
                "level": level,
                "regex": regex,
            }
        )
    for row in data.get("body") or []:
        if not isinstance(row, dict) or not row.get("pattern"):
            continue
        rid = str(row.get("id") or "")
        regex = _compile(str(row["pattern"]), str(row.get("flags") or ""))
        compiled.setdefault(rid, regex)
        body_rules.append({"id": rid, "kind": str(row.get("kind") or ""), "regex": regex})
    labels = {str(x).strip() for x in (data.get("column_labels") or []) if str(x).strip()}
    prefixes = tuple(str(x) for x in (data.get("skip_prefixes") or []) if str(x).strip())
    rejects: list[re.Pattern[str]] = []
    for row in data.get("reject_titles") or []:
        if not isinstance(row, dict) or not row.get("pattern"):
            continue
        rejects.append(_compile(str(row["pattern"]), str(row.get("flags") or "")))
    _cache_key = key
    _cache_data = data
    _compiled = compiled
    _suffixes = suffixes
    _line_rules = line_rules
    _body_rules = body_rules
    _column = labels
    _skip_prefixes = prefixes
    _rejects = rejects
    _raw_for_js = {
        "page_suffix": [
            {"id": str(row.get("id") or ""), "pattern": str(row.get("pattern") or "")}
            for row in (data.get("page_suffix") or [])
            if isinstance(row, dict)
        ],
        "lines": [
            {
                "id": str(row.get("id") or ""),
                "pattern": str(row.get("pattern") or ""),
                "kind": str(row.get("kind") or "section"),
                "level": row.get("level") if row.get("level") is not None else 1,
                "flags": str(row.get("flags") or ""),
            }
            for row in (data.get("lines") or [])
            if isinstance(row, dict)
        ],
        "column_labels": sorted(labels),
        "skip_prefixes": list(prefixes),
    }


def compiled(name: str) -> re.Pattern[str] | None:
    _ensure()
    return _compiled.get(name)


def export_for_js() -> dict[str, Any]:
    _ensure()
    return dict(_raw_for_js)


def column_labels(book_id: str = "") -> set[str]:
    _ensure()
    extra: set[str] = set()
    per = _cache_data.get("per_book")
    if isinstance(per, dict) and book_id:
        raw = per.get(book_id) or per.get(str(book_id))
        if isinstance(raw, list):
            extra = {str(x).strip() for x in raw if str(x).strip()}
    return set(_column) | extra


def skip_prefixes() -> tuple[str, ...]:
    _ensure()
    return _skip_prefixes


def rejected_title(label: str) -> bool:
    """True when the peeled title is a shared reject shape (timeline / leader-only)."""
    _ensure()
    raw = (label or "").strip()
    if not raw:
        return False
    return any(rule.match(raw) for rule in _rejects)


def has_skip_prefix(text: str) -> bool:
    """项目符号开头的行（`·基本方法`、`•知识结构图`）永远不是目录条目。"""
    raw = (text or "").strip()
    if not raw:
        return False
    return any(prefix and raw.startswith(prefix) for prefix in skip_prefixes())


def strip_page(text: str) -> tuple[str, int | None]:
    _ensure()
    raw = (text or "").strip()
    if not raw:
        return "", None
    for regex in _suffixes:
        match = regex.search(raw)
        if not match:
            continue
        try:
            page = int(re.sub(r"\s+", "", str(match.group(1))))
        except (TypeError, ValueError, IndexError):
            continue
        label = _TAIL.sub("", raw[: match.start()]).strip()
        if label:
            return label, page
    return raw, None


def match_line(text: str) -> LineHit | None:
    _ensure()
    raw = (text or "").strip()
    if not raw:
        return None
    for rule in _line_rules:
        match = rule["regex"].match(raw)
        if match:
            return LineHit(id=rule["id"], kind=rule["kind"], level=int(rule["level"]), match=match)
    return None


def match_body(text: str) -> LineHit | None:
    _ensure()
    raw = (text or "").strip()
    if not raw:
        return None
    for rule in _body_rules:
        match = rule["regex"].match(raw)
        if match:
            kind = str(rule["kind"] or "section")
            level = 0 if kind == "chapter" else (2 if kind == "dotted" else 1)
            return LineHit(id=str(rule["id"] or ""), kind=kind, level=level, match=match)
    return None


def parse_toc_line(text: str) -> dict[str, Any]:
    """Peel print page, then classify. Used by tests and the Python builder."""
    label, page = strip_page(text)
    hit = match_line(label)
    if hit is None:
        return {
            "label": label,
            "print_page": page,
            "level": 1,
            "is_chapter": False,
            "kind": "",
            "id": "",
        }
    return {
        "label": label,
        "print_page": page,
        "level": hit.level,
        "is_chapter": hit.kind == "chapter" or hit.level == 0,
        "kind": hit.kind,
        "id": hit.id,
    }


def is_column_label(text: str, book_id: str = "") -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return False
    for label in column_labels(book_id):
        if compact == re.sub(r"\s+", "", label) or compact.startswith(re.sub(r"\s+", "", label)):
            return True
    return False


_GLUE_SPLIT = re.compile(
    r"(?=第\s*(?:\d+|[一二三四五六七八九十两零百]+)\s*节)"
    r"|(?=探究\s*[·.•．]?\s*(?:实验|建模|设计))"
)
_ONE_CJK = re.compile(r"^[\u4e00-\u9fff]$")
_STARTS_CJK = re.compile(r"^[\u4e00-\u9fff]")
_KEEP_CJK = frozenset("第章节部")
_MARK_SPACE = re.compile(
    r"(第\s*(?:\d+|[一二三四五六七八九十两零百]+)\s*(?:章|节))(?=\S)"
)
_GLUED_PAGE = re.compile(r"([\u4e00-\u9fff])(\d{1,4})\s*$")
_SPACED_PAGE = re.compile(r"(\d)\s+(\d{1,2})\s*$")
_DECOR_JIE = re.compile(r"^第\s*节\s*(\d+)\s*(.+)$")
_BARE_ZHANG = re.compile(r"^第\s*章$")
_LONE_SHEET = re.compile(r"^\d{1,2}$")
_NEW_HEAD = re.compile(
    r"^(?:第\s*(?:\d+|[一二三四五六七八九十两零百]+)\s*(?:章|节)|探究)"
)


def normalize_catalog_line(text: str) -> str:
    """Collapse InDesign letter-spacing and glue page digits back onto the title."""
    raw = (text or "").replace("\u00a0", " ").replace("\u3000", " ").strip()
    if not raw:
        return ""
    raw = re.sub(r"\s+", " ", raw)
    raw = re.sub(r"\s*([、·.•．])\s*", r"\1", raw)
    bits = raw.split(" ")
    merged: list[str] = []
    for part in bits:
        if (
            merged
            and _ONE_CJK.match(merged[-1])
            and merged[-1] not in _KEEP_CJK
            and _STARTS_CJK.match(part)
        ):
            merged[-1] = merged[-1] + part
        else:
            merged.append(part)
    raw = " ".join(merged)
    for _ in range(2):
        raw = _SPACED_PAGE.sub(r"\1\2", raw)
    raw = _GLUED_PAGE.sub(r"\1 \2", raw)
    raw = _MARK_SPACE.sub(r"\1 ", raw)
    return raw.strip()


def split_glued_catalog_line(text: str) -> list[str]:
    """Cut MinerU blobs that mashed several 第N节 / 探究 rows into one line."""
    raw = normalize_catalog_line(text)
    if not raw:
        return []
    bits = [part.strip() for part in _GLUE_SPLIT.split(raw) if part and part.strip()]
    return bits or [raw]


def repair_jie_heading(text: str) -> str:
    """「第 节 2 标题」← 装饰节号印在「节」后面。"""
    raw = (text or "").strip()
    match = _DECOR_JIE.match(raw)
    if match:
        return f"第{match.group(1)}节 {match.group(2).strip()}"
    return raw


def join_wrapped_catalog_lines(lines: list[str]) -> list[str]:
    """Join a heading that lost its page onto the next short remainder line."""
    out: list[str] = []
    index = 0
    while index < len(lines):
        current = lines[index]
        _label, page = strip_page(current)
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        nlabel, npage = strip_page(nxt) if nxt else ("", None)
        open_row = page is None and bool(current) and (
            _NEW_HEAD.match(current) or match_line(_label) is not None
        )
        if (
            open_row
            and nxt
            and npage is not None
            and not _NEW_HEAD.match(nxt)
            and not _BARE_ZHANG.match(nlabel)
        ):
            out.append(f"{current} {nxt}".strip())
            index += 2
            continue
        out.append(current)
        index += 1
    return out


def repair_decorative_chapters(lines: list[str]) -> list[str]:
    """「第 章」+ 旁边大号数字 + 下一行标题 → 「第N章 标题」。"""
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        label, page = strip_page(line)
        if _BARE_ZHANG.match(label):
            num = out.pop() if out and _LONE_SHEET.match(out[-1]) else ""
            title = ""
            print_page = page
            nxt = lines[index + 1] if index + 1 < len(lines) else ""
            if nxt:
                nlabel, npage = strip_page(nxt)
                if nlabel and not _NEW_HEAD.match(nxt) and not _BARE_ZHANG.match(nlabel):
                    title = nlabel
                    print_page = npage if npage is not None else page
                    index += 1
            if not num and page is not None and title:
                num = str(page)
            if num and title:
                suffix = f" {print_page}" if print_page is not None else ""
                out.append(f"第{num}章 {title}{suffix}")
                index += 1
                continue
        out.append(line)
        index += 1
    return [line for line in out if not _LONE_SHEET.match(line)]


def prepare_catalog_lines(lines: list[str]) -> list[str]:
    """One-page catalog cleanup used by build_toc / toc_extract."""
    exploded: list[str] = []
    for line in lines:
        exploded.extend(split_glued_catalog_line(line))
    repaired = [repair_jie_heading(line) for line in exploded if line]
    joined = join_wrapped_catalog_lines(repaired)
    return repair_decorative_chapters(joined)
