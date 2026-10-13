"""Extract subsection TOC: read the printed/body catalog, then fill kids once.

目录规则归 agent（见 doc/toc-code-ownership.md）。`app/toc_extract.py` 只是 shim。
本文件既是库（`run_book` 给 `app/tasks.py` 用），也是 agent 工具（末尾的 `toc_extract()`）。

它与 app 跑在同一进程，所以 `from app import bus, jobs, ...` 照样可用；
但 `toc_lexicon` 走同目录的共享实现，不经过 app shim。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import bus, jobs, library, prompts_store, sdk_runner
from app.config import settings

try:  # 同目录共享词法；缺失时下面的调用点各自兜底
    import toc_lexicon as lexicon  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - 只在 tools/ 不在 sys.path 时发生
    lexicon = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)
_NUM = re.compile(r"^\*?(\d+)\b")
HEAD_LINES = 12
NESTED_KINDS = frozenset({"chat", "grade", "page_refine", "task"})
_builder_mod: Any = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_level(value: Any, default: int = 1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def cursor_path(book_id: str) -> Path:
    return library.book_dir(book_id) / "toc_extract.json"


def agent_path(book_id: str) -> Path:
    return library.book_dir(book_id) / "toc_agent.json"


def mineru_path(book_id: str) -> Path:
    return library.book_dir(book_id) / "mineru_toc.json"


def printed_path(book_id: str) -> Path:
    return library.book_dir(book_id) / "toc_printed.json"


def load_printed(book_id: str) -> dict[str, Any]:
    path = printed_path(book_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_cursor(book_id: str) -> dict[str, Any]:
    path = cursor_path(book_id)
    if not path.is_file():
        return {"book_id": book_id, "intervals": {}, "skeleton": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("book_id", book_id)
    data.setdefault("intervals", {})
    data.setdefault("skeleton", {})
    return data


def save_cursor(book_id: str, data: dict[str, Any]) -> None:
    path = cursor_path(book_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["book_id"] = book_id
    payload["updated_at"] = _now()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def parse_json_payload(text: str) -> Any:
    raw = (text or "").strip()
    fence = _FENCE.search(raw)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("[")
        end = raw.rfind("]")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
        raise


def _as_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("entries", "items", "skeleton_entries"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            return []
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def _label_num(label: str) -> int | None:
    match = _NUM.match((label or "").strip())
    return int(match.group(1)) if match else None


def validate_interval_entries(
    rows: list[dict[str, Any]],
    start_pdf: int,
    end_pdf: int,
) -> list[dict[str, Any]] | None:
    """Return cleaned kids, or None if the whole interval must be dropped."""
    cleaned: list[dict[str, Any]] = []
    last_pdf = 0
    nums: list[int | None] = []
    for row in rows:
        label = str(row.get("label") or "").strip()
        try:
            pdf_page = int(row.get("pdf_page") or 0)
        except (TypeError, ValueError):
            pdf_page = 0
        if not label or pdf_page < 1:
            return None
        if not (start_pdf <= pdf_page < end_pdf):
            return None
        if last_pdf and pdf_page < last_pdf:
            return None
        last_pdf = pdf_page
        print_page = row.get("print_page")
        if print_page is not None:
            try:
                print_page = int(print_page)
            except (TypeError, ValueError):
                print_page = None
        num = row.get("num")
        if num is None:
            num = _label_num(label)
        elif not isinstance(num, int):
            try:
                num = int(num)
            except (TypeError, ValueError):
                num = _label_num(label)
        nums.append(num)
        cleaned.append({"label": label, "pdf_page": pdf_page, "print_page": print_page, "num": num})
    present = [n for n in nums if n is not None]
    if present and len(present) != len(nums):
        return None
    if present and present != list(range(1, len(present) + 1)):
        return None
    return cleaned


def validate_skeleton_entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    cleaned: list[dict[str, Any]] = []
    last_pdf = 0
    for row in rows:
        label = str(row.get("label") or "").strip()
        if not label:
            return None
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            return None
        if level not in (0, 1):
            continue
        try:
            pdf_page = int(row.get("pdf_page") or 0)
        except (TypeError, ValueError):
            pdf_page = 0
        if pdf_page < 1:
            return None
        if last_pdf and pdf_page < last_pdf:
            return None
        last_pdf = pdf_page
        print_page = row.get("print_page")
        if print_page is not None:
            try:
                print_page = int(print_page)
            except (TypeError, ValueError):
                print_page = None
        cleaned.append(
            {
                "label": label,
                "level": level,
                "is_chapter": bool(row.get("is_chapter") or level == 0),
                "pdf_page": pdf_page,
                "print_page": print_page,
            }
        )
    return cleaned or None


def sibling_intervals(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    backbone = [row for row in entries if int(row.get("level") or 0) in (0, 1)]
    out: list[dict[str, Any]] = []
    for index, item in enumerate(backbone):
        if int(item.get("level") or 0) != 1:
            continue
        if str(item.get("label") or "") in {"内容提要", "复习题"}:
            continue
        try:
            start = int(item.get("pdf_page") or 0)
        except (TypeError, ValueError):
            start = 0
        if start < 1:
            continue
        end = 10**9
        for later in backbone[index + 1 :]:
            try:
                later_pdf = int(later.get("pdf_page") or 0)
            except (TypeError, ValueError):
                later_pdf = 0
            if later_pdf:
                end = later_pdf
                break
        if end <= start:
            continue
        out.append(
            {
                "key": f"{start}-{end}",
                "start_pdf": start,
                "end_pdf": end,
                "section_label": str(item.get("label") or ""),
                "section_level": _as_level(item.get("level"), 1),
            }
        )
    return out


def page_hash(texts: list[str]) -> str:
    blob = "\n\x1e\n".join(texts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# 抽取规则的版本号。改了 scan_interval_entries 的取舍逻辑就 +1，
# 否则老书的区间缓存（按页文本哈希命中）会让新规则完全不生效。
SCAN_RULES_VERSION = 2


def rules_stamp() -> str:
    """Fingerprint of everything that decides which headings survive.

    区间缓存原来只按页文本哈希命中：正文没变就直接复用上一轮结果。于是往
    `configs/toc_lexicon.yaml` 里加停用词、或收紧 UNNUMBERED，重跑也纹丝不动 ——
    看着像「改了没用」，实际是压根没重算。把词法表指纹并进 key 就能自动失效。
    """
    stamp = ""
    if lexicon is not None:
        try:
            stamp = str(lexicon.lexicon_stamp())
        except Exception:
            stamp = ""
    return f"v{SCAN_RULES_VERSION}|{stamp}"


def interval_hash(texts: list[str]) -> str:
    """Cache key for one sibling interval: page text + current extraction rules."""
    return page_hash([*texts, "\x1frules\x1f" + rules_stamp()])


def _page_text(book_id: str, page_no: int) -> str:
    text = library.get_page_markdown(book_id, page_no)
    return text if text is not None else ""


def _heading_digest(book_id: str, page_count: int) -> str:
    chunks: list[str] = []
    for page_no in range(1, page_count + 1):
        lines = [ln.strip() for ln in _page_text(book_id, page_no).splitlines() if ln.strip()]
        head = "\n".join(lines[:HEAD_LINES])
        chunks.append(f"## PDF p{page_no:04d}\n{head}")
    return "\n\n".join(chunks)


def _interval_texts(book_id: str, start_pdf: int, end_pdf: int) -> list[str]:
    return [_page_text(book_id, page_no) for page_no in range(start_pdf, end_pdf)]


def _job_kind(job_id: str) -> str:
    row = jobs.get_job(job_id) if job_id else None
    return str((row or {}).get("kind") or "")


def holds_foreign_bridge(job_id: str) -> bool:
    """True when this call sits inside another Cursor turn (do not open a second agent).

    Tool callbacks and workspace Shell see an empty ContextVar. A running chat
    job on disk is enough — do not start a second local bridge.
    """
    if _job_kind(job_id) in NESTED_KINDS:
        return True
    return jobs.count_sdk_active(exclude=job_id) > 0


def format_catalog(entries: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in entries:
        if not isinstance(row, dict):
            continue
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        if level not in (0, 1):
            continue
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        try:
            pdf_page = int(row.get("pdf_page") or 0)
        except (TypeError, ValueError):
            pdf_page = 0
        indent = "" if level == 0 else "  "
        try:
            print_page = int(row["print_page"]) if row.get("print_page") is not None else 0
        except (TypeError, ValueError):
            print_page = 0
        if pdf_page:
            page = f"  p{pdf_page}"
        elif print_page:
            page = f"  印{print_page}"
        else:
            page = ""
        lines.append(f"{indent}{label}{page}")
    return "\n".join(lines) if lines else "（空）"


def _pdf_text_only(book_id: str, page_no: int) -> str:
    path = library.pdf_path(book_id)
    if not path.is_file():
        return ""
    try:
        from toc_pdf import page_layout_text

        text = page_layout_text(path, page_no)
        if text.strip():
            return text
    except Exception:
        logger.debug("pdf layout extract failed book=%s page=%s", book_id, page_no, exc_info=True)
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        index = int(page_no) - 1
        if 0 <= index < len(reader.pages):
            return reader.pages[index].extract_text() or ""
    except Exception:
        logger.debug("pdf extract failed book=%s page=%s", book_id, page_no, exc_info=True)
    return ""


def _catalog_hits(lines: list[str]) -> int:
    try:
        mod = load_builder()
    except FileNotFoundError:
        return 0
    check = getattr(mod, "_is_cn_catalog_line", None)
    if check is None:
        return 0
    return sum(1 for line in lines if check(line))


def extract_pdf_page(book_id: str, page_no: int) -> str:
    """Read one original PDF catalog page. Prefer the cleaner of PDF text vs that page's Markdown."""
    pdf_text = _pdf_text_only(book_id, page_no)
    md_text = _page_text(book_id, page_no)
    try:
        clean = load_builder()._clean_lines
    except FileNotFoundError:
        clean = lambda text: [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    pdf_lines = clean(pdf_text)
    md_lines = clean(md_text)
    prepare = None
    if lexicon is not None:
        prepare = getattr(lexicon, "prepare_catalog_lines", None)
    if callable(prepare):
        try:
            pdf_lines = list(prepare(pdf_lines))
            md_lines = list(prepare(md_lines))
        except Exception:
            pass
    if pdf_lines and _catalog_hits(pdf_lines) >= max(_catalog_hits(md_lines), 1):
        return pdf_text
    return md_text or pdf_text


def _printed_toc_pages(book_id: str) -> list[int]:
    data = load_printed(book_id)
    out: list[int] = []
    for raw in data.get("toc_pages") or []:
        try:
            page = int(raw)
        except (TypeError, ValueError):
            continue
        if page > 0:
            out.append(page)
    return sorted(set(out))


def _entries_from_printed(rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        try:
            level = int(row.get("level") or (0 if row.get("is_chapter") else 1))
        except (TypeError, ValueError):
            level = 1
        is_chapter = bool(row.get("is_chapter")) or level <= 0
        out.append(
            {
                "label": label,
                "level": 0 if is_chapter else 1,
                "is_chapter": is_chapter,
                "pdf_page": int(row.get("pdf_page") or 0) or 0,
                "print_page": row.get("print_page"),
            }
        )
    return out


def locate_toc_pages(book_id: str, page_count: int) -> list[int]:
    """PDF page numbers that carry the printed catalog (sidecar, MinerU hint, then detect)."""
    found = list(_printed_toc_pages(book_id))
    if found:
        return found
    found = list(_load_mineru_pages(book_id))
    if not found:
        toc = library.read_toc(book_id)
        for raw in toc.get("toc_pages") or []:
            try:
                page = int(raw)
            except (TypeError, ValueError):
                continue
            if page > 0:
                found.append(page)
    if found:
        return sorted(set(found))
    try:
        is_toc = load_builder()._is_toc_page
        clean = load_builder()._clean_lines
        norm = load_builder()._norm_title
    except FileNotFoundError:
        is_toc = None
        clean = lambda text: [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        norm = lambda text: re.sub(r"\s+", "", text or "")
    try:
        from ir_lib import FRONT_PAGES
    except ImportError:
        FRONT_PAGES = 24
    limit = min(max(int(page_count or 0), 0), FRONT_PAGES)
    detected: list[int] = []
    for page_no in range(1, limit + 1):
        text = extract_pdf_page(book_id, page_no)
        lines = clean(text) if callable(clean) else []
        if not lines:
            continue
        title = norm(lines[0])
        if title in {"目录", "目 录"} or (is_toc and is_toc(lines)):
            detected.append(page_no)
    return detected


def _entries_from_pdf_skeleton(skeleton: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in skeleton:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        kind = str(item.get("kind") or "")
        is_chapter = kind == "chapter" or bool(item.get("is_chapter"))
        out.append(
            {
                "label": label,
                "level": 0 if is_chapter else 1,
                "is_chapter": is_chapter,
                "pdf_page": int(item.get("pdf_page") or 0) or 0,
                "print_page": item.get("print_page"),
            }
        )
    return out


def read_pdf_catalog(book_id: str, page_count: int) -> dict[str, Any]:
    """Printed catalog: toc_printed.json wins; otherwise parse catalog page text."""
    printed = load_printed(book_id)
    pdf = library.pdf_path(book_id)
    pdf_s = str(pdf) if pdf.is_file() else ""
    if printed:
        toc_pages = _printed_toc_pages(book_id)
        entries = _entries_from_printed(list(printed.get("entries") or []))
        return {
            "pdf": pdf_s,
            "toc_pages": toc_pages,
            "text": "",
            "skeleton": [],
            "entries": entries,
            "brief": format_catalog(entries) if entries else "（印刷目录侧车为空）",
            "source": "printed_sidecar",
        }
    toc_pages = locate_toc_pages(book_id, page_count)
    blobs: list[str] = []
    parsed_pages: list[tuple[int, Any, list[str]]] = []
    try:
        mod = load_builder()
    except FileNotFoundError:
        mod = None
    for page_no in toc_pages:
        text = extract_pdf_page(book_id, page_no)
        blobs.append(f"--- PDF p{page_no:04d} ---\n{text}")
        lines = mod._clean_lines(text) if mod else [ln.strip() for ln in text.splitlines() if ln.strip()]
        parsed_pages.append((page_no, None, lines))
    skeleton: list[dict[str, Any]] = []
    if mod and parsed_pages:
        _pages, skeleton, _subs = mod._parse_catalog(parsed_pages, known_toc_pages=toc_pages or None)
        if toc_pages and not skeleton:
            _pages, skeleton, _subs = mod._parse_catalog(parsed_pages, known_toc_pages=None)
    entries = _entries_from_pdf_skeleton(skeleton)
    return {
        "pdf": pdf_s,
        "toc_pages": toc_pages,
        "text": "\n".join(blobs),
        "skeleton": skeleton,
        "entries": entries,
        "brief": format_catalog(entries),
        "source": "pdf_text",
    }


def merge_catalog(
    pdf_entries: list[dict[str, Any]],
    toc_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """PDF catalog labels win; toc.json fills pdf_page and kept extras (前言/部头)."""
    try:
        norm = load_builder()._norm_title
    except FileNotFoundError:
        norm = lambda text: re.sub(r"\s+", "", text or "")
    if not pdf_entries:
        return [row for row in toc_entries if isinstance(row, dict)]
    by_norm: dict[str, dict[str, Any]] = {}
    extras: list[dict[str, Any]] = []
    pdf_norms = {norm(str(row.get("label") or "")) for row in pdf_entries}
    for row in toc_entries:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "")
        key = norm(label)
        if key in pdf_norms:
            by_norm[key] = row
            continue
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        if level == 0 and (
            label in {"前言", "后记", "引论", "附录"} or "部分" in label
        ):
            extras.append(row)
    merged: list[dict[str, Any]] = []
    for row in pdf_entries:
        hit = by_norm.get(norm(str(row.get("label") or "")))
        item = dict(row)
        if hit:
            try:
                pdf_page = int(hit.get("pdf_page") or 0)
            except (TypeError, ValueError):
                pdf_page = 0
            if pdf_page:
                item["pdf_page"] = pdf_page
            if item.get("print_page") is None and hit.get("print_page") is not None:
                item["print_page"] = hit.get("print_page")
        merged.append(item)
    merged.extend(extras)

    def _sort_key(row: dict[str, Any]) -> tuple[int, int]:
        try:
            pdf_page = int(row.get("pdf_page") or 0)
        except (TypeError, ValueError):
            pdf_page = 0
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        return (pdf_page or 10**9, level)

    merged.sort(key=_sort_key)
    return merged


def load_builder() -> Any:
    global _builder_mod
    path = settings.tools_dir / "build_toc.py"
    if _builder_mod is not None and getattr(_builder_mod, "__file__", None) == str(path):
        return _builder_mod
    if not path.is_file():
        raise FileNotFoundError("build_toc missing")
    spec = importlib.util.spec_from_file_location("toc_extract_build_toc", path)
    if spec is None or spec.loader is None:
        raise FileNotFoundError("cannot load build_toc")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _builder_mod = module
    return module


def _chapter_titles(entries: list[dict[str, Any]]) -> set[str]:
    titles: set[str] = set()
    try:
        norm = load_builder()._norm_title
    except FileNotFoundError:
        norm = lambda text: re.sub(r"\s+", "", text or "")
    for row in entries:
        if not isinstance(row, dict):
            continue
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        if level != 0:
            continue
        label = str(row.get("label") or "")
        if not label:
            continue
        titles.add(norm(label))
        titles.add(norm(re.sub(r"^第\d+(?:章|部分)\s*", "", label)))
        try:
            titles.add(load_builder()._chapter_key(label))
        except Exception:
            pass
    return titles


def accept_scan_entries(
    rows: list[dict[str, Any]],
    start_pdf: int,
    end_pdf: int,
) -> list[dict[str, Any]]:
    """Keep in-range monotonic headings. Do not require 1..n numbering."""
    cleaned: list[dict[str, Any]] = []
    last_pdf = 0
    for row in rows:
        label = str(row.get("label") or "").strip()
        try:
            pdf_page = int(row.get("pdf_page") or 0)
        except (TypeError, ValueError):
            pdf_page = 0
        if not label or pdf_page < 1:
            continue
        if not (start_pdf <= pdf_page < end_pdf):
            continue
        if last_pdf and pdf_page < last_pdf:
            continue
        last_pdf = pdf_page
        print_page = row.get("print_page")
        if print_page is not None:
            try:
                print_page = int(print_page)
            except (TypeError, ValueError):
                print_page = None
        num = row.get("num")
        if num is None:
            num = _label_num(label)
        elif not isinstance(num, int):
            try:
                num = int(num)
            except (TypeError, ValueError):
                num = _label_num(label)
        cleaned.append(
            {
                "label": label,
                "pdf_page": pdf_page,
                "print_page": print_page,
                "num": num,
                "level": _as_level(row.get("level"), 2),
            }
        )
    return cleaned


def scan_interval_entries(
    book_id: str,
    start_pdf: int,
    end_pdf: int,
    section_label: str,
    chapter_titles: set[str] | None = None,
    section_level: int = 1,
) -> list[dict[str, Any]]:
    """Pull subsection headings from pages in one sibling interval (no extra agent)."""
    try:
        mod = load_builder()
    except FileNotFoundError:
        return []
    titles = chapter_titles or set()
    section_norm = mod._norm_title(section_label)
    kid_level = _as_level(section_level, 1) + 1
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    jie = getattr(mod, "SECTION_JIE_LINE", None)
    marked = getattr(mod, "_clean_lines_marked", None)

    def add(label: str, page_no: int, num: int | None = None) -> None:
        text = (label or "").strip()
        if not text or mod._norm_title(text) == section_norm:
            return
        if lexicon is not None:
            try:
                if lexicon.is_column_label(text, book_id) or lexicon.has_skip_prefix(text):
                    return
            except Exception:
                pass
        key = (mod._norm_title(text), page_no)
        if key in seen:
            return
        seen.add(key)
        found.append(
            {
                "label": text,
                "pdf_page": page_no,
                "print_page": None,
                "num": num,
                "level": kid_level,
            }
        )

    for page_no in range(start_pdf, end_pdf):
        page_text = _page_text(book_id, page_no)
        # 无编号的小节必须同时是 ATX 标题（`## 标题`）。不加这一条，UNNUMBERED 会把
        # 「也可用等式表示为」「得飞机起飞阶段所需的最短时间」这类正文短句也收进目录。
        atx_heads: set[str] = set()
        if callable(marked):
            atx_heads = {text for text, depth in marked(page_text) if depth >= 1}
        for line in mod._clean_lines(page_text):
            label = ""
            num: int | None = None
            dot3 = mod.BODY_DOT3.match(line)
            if dot3:
                label = f"{dot3.group(2)} {mod._plain_heading(dot3.group(3) or '')}".strip()
            else:
                sub = mod.SUBSECTION.match(line)
                if sub:
                    label = f"{sub.group(1)}{sub.group(2)} {mod._plain_heading(sub.group(3) or '')}".strip()
                    try:
                        num = int(sub.group(2))
                    except (TypeError, ValueError):
                        num = None
                else:
                    plain = mod.UNNUMBERED.match(line)
                    if (
                        plain
                        and line in atx_heads
                        and not mod._skip_heading(plain.group(1), titles)
                    ):
                        label = plain.group(1)
            add(label, page_no, num)
        if not callable(marked):
            continue
        for line, depth in marked(page_text):
            if depth < 1:
                continue
            if jie is not None and jie.match(line):
                continue
            if mod.CHAPTER_LINE.match(line) or mod.PART_LINE.match(line):
                continue
            if mod.BODY_SECTION.match(line) or mod.BODY_DOT3.match(line):
                continue
            if titles and (
                mod._norm_title(line) in titles
                or (hasattr(mod, "_chapter_key") and mod._chapter_key(line) in titles)
            ):
                continue
            if lexicon is not None:
                try:
                    if lexicon.is_column_label(line, book_id) or lexicon.has_skip_prefix(line):
                        continue
                except Exception:
                    pass
            if re.search(r"[。．.!！]$", line):
                continue
            add(mod._plain_heading(line), page_no)
    return accept_scan_entries(found, start_pdf, end_pdf)


def interval_has_leftover(
    book_id: str,
    start_pdf: int,
    end_pdf: int,
    section_label: str,
) -> bool:
    """True when the interval has body lines that are not the section/chapter title."""
    try:
        mod = load_builder()
    except FileNotFoundError:
        return any(_page_text(book_id, page_no).strip() for page_no in range(start_pdf, end_pdf))
    section_norm = mod._norm_title(section_label)
    for page_no in range(start_pdf, end_pdf):
        for line in mod._clean_lines(_page_text(book_id, page_no)):
            if mod._norm_title(line) == section_norm:
                continue
            jie = getattr(mod, "SECTION_JIE_LINE", None)
            if (
                mod.CHAPTER_LINE.match(line)
                or mod.PART_LINE.match(line)
                or (jie is not None and jie.match(line))
                or (mod.BODY_SECTION.match(line) and not mod.TOC_SECTION.match(line))
                or mod.NAMED_HEADING.match(line)
                or mod.FRONT.match(line)
                or mod.BACK.match(line)
                or mod.INTRO_LINE.match(line)
                or mod.APPENDIX_HEAD.match(line)
            ):
                continue
            return True
    return False


def scan_skeleton_entries(book_id: str, page_count: int) -> list[dict[str, Any]]:
    """Chapter/section backbone from body headings when there is no printed catalog."""
    try:
        mod = load_builder()
    except FileNotFoundError:
        return []
    skip = set(_load_mineru_pages(book_id))
    rows: list[dict[str, Any]] = []
    for page_no in range(1, page_count + 1):
        if page_no in skip:
            continue
        for line in mod._clean_lines(_page_text(book_id, page_no)):
            chapter = mod.CHAPTER_LINE.match(line)
            if chapter:
                rows.append(
                    {
                        "label": f"第{chapter.group(1)}章 {chapter.group(2).strip()}",
                        "level": 0,
                        "is_chapter": True,
                        "pdf_page": page_no,
                        "print_page": None,
                    }
                )
                continue
            part = mod.PART_LINE.match(line)
            if part:
                title = (part.group(2) or "").strip()
                label = f"第{part.group(1)}部分 {title}".strip()
                rows.append(
                    {
                        "label": label,
                        "level": 0,
                        "is_chapter": True,
                        "pdf_page": page_no,
                        "print_page": None,
                    }
                )
                continue
            intro = mod.INTRO_LINE.match(line)
            if intro:
                rows.append(
                    {
                        "label": "引论",
                        "level": 0,
                        "is_chapter": True,
                        "pdf_page": page_no,
                        "print_page": int(intro.group(1)) if intro.group(1) else None,
                    }
                )
                continue
            if mod.APPENDIX_HEAD.match(line):
                rows.append(
                    {
                        "label": "附录",
                        "level": 0,
                        "is_chapter": True,
                        "pdf_page": page_no,
                        "print_page": None,
                    }
                )
                continue
            section = mod.BODY_SECTION.match(line)
            if section and not mod.TOC_SECTION.match(line):
                title = (section.group(3) or "").strip()
                rows.append(
                    {
                        "label": f"{section.group(2)} {title}".strip(),
                        "level": 1,
                        "is_chapter": False,
                        "pdf_page": page_no,
                        "print_page": None,
                    }
                )
                continue
            jie = getattr(mod, "SECTION_JIE_LINE", None)
            jie_hit = jie.match(line) if jie is not None else None
            if jie_hit:
                rows.append(
                    {
                        "label": line,
                        "level": 1,
                        "is_chapter": False,
                        "pdf_page": page_no,
                        "print_page": None,
                    }
                )
    return validate_skeleton_entries(rows) or []


def parse_book_payload(text: str) -> dict[str, Any]:
    payload = parse_json_payload(text)
    if isinstance(payload, list):
        return {"skeleton_entries": [], "intervals": []}
    if not isinstance(payload, dict):
        return {"skeleton_entries": [], "intervals": []}
    raw_skel = payload.get("skeleton_entries")
    if raw_skel is None:
        raw_skel = payload.get("skeleton")
    skeleton = [row for row in raw_skel if isinstance(row, dict)] if isinstance(raw_skel, list) else []
    raw_iv = payload.get("intervals")
    intervals = [row for row in raw_iv if isinstance(row, dict)] if isinstance(raw_iv, list) else []
    return {"skeleton_entries": skeleton, "intervals": intervals}


def _match_interval(
    planned: list[dict[str, Any]],
    row: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        start = int(row.get("start_pdf") or 0)
    except (TypeError, ValueError):
        start = 0
    want = re.sub(r"\s+", "", str(row.get("section_label") or ""))
    for item in planned:
        if start and start == int(item["start_pdf"]):
            return item
        if want and re.sub(r"\s+", "", str(item.get("section_label") or "")) == want:
            return item
    return None


def _book_prompt(
    *,
    catalog_text: str,
    digest: str,
    intervals: list[dict[str, Any]],
    need_skeleton: bool,
) -> str:
    parts = [
        "先看原文 PDF 目录页读出的目录（权威，不要另造章或节）：",
        catalog_text,
        "",
    ]
    if need_skeleton:
        parts.extend(
            [
                "上面目录为空或不完整。请根据每页开头补章（level=0）和节（level=1）。不要把小节写进骨架。",
                digest,
                "",
            ]
        )
    if intervals:
        parts.append("再按下面每个兄弟节区间抽出小节。一次给出全部区间，不要每个区间另开对话。")
        parts.append("只抽二级/三级小标题。pdf_page 必须落在该区间内，且按阅读顺序递增。")
        parts.append("")
        for item in intervals:
            start = int(item["start_pdf"])
            end = int(item["end_pdf"])
            parts.append(f"## {item['section_label']}  PDF p{start}（含）– p{end}（不含）")
            for offset, text in enumerate(item.get("texts") or []):
                parts.append(f"--- p{start + offset:04d} ---\n{text}")
            parts.append("")
    parts.append(
        "只输出一个 JSON 对象："
        '{"skeleton_entries":[{"label","level","is_chapter","pdf_page","print_page"}],'
        '"intervals":[{"start_pdf","end_pdf","section_label","entries":[{"label","pdf_page"}]}]}。'
        "不需要骨架时 skeleton_entries 用 []。没有小节的区间 entries 用 []。"
    )
    return "\n".join(parts)


def _ask(system: str, user: str, job_id: str) -> str:
    body = f"{system.rstrip()}\n\n{user}" if system else user
    text, _sdk_id = sdk_runner._prompt_sdk(
        body,
        conversation_id="",
        resume_id="",
        job_id=job_id,
        mode="ask",
    )
    return text or ""


def _load_mineru_pages(book_id: str) -> list[int]:
    path = mineru_path(book_id)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[int] = []
    for raw in data.get("toc_pages") or []:
        try:
            page = int(raw)
        except (TypeError, ValueError):
            continue
        if page > 0:
            out.append(page)
    return out


def has_printed_catalog(book_id: str) -> bool:
    printed = load_printed(book_id)
    if printed.get("entries"):
        return True
    if _load_mineru_pages(book_id):
        return True
    toc = library.read_toc(book_id)
    pages = toc.get("toc_pages") if isinstance(toc.get("toc_pages"), list) else []
    if pages:
        return True
    for row in toc.get("entries") or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "")
        if int(row.get("level") or 0) in (0, 1) and label not in {"前言", "后记"}:
            return True
    return False


def write_agent_sidecar(
    book_id: str,
    *,
    source: str,
    skeleton_entries: list[dict[str, Any]],
    intervals: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "source": source,
        "skeleton_entries": skeleton_entries,
        "intervals": intervals,
    }
    path = agent_path(book_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return payload


def run_workspace_toc(book_id: str) -> dict[str, Any]:
    path = settings.tools_dir / "build_toc.py"
    if not path.is_file():
        return {"ok": False, "error": "build_toc missing"}
    spec = importlib.util.spec_from_file_location("toc_extract_build_toc", path)
    if spec is None or spec.loader is None:
        return {"ok": False, "error": "cannot load build_toc"}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    builder = getattr(module, "build_toc", None)
    if builder is None:
        return {"ok": False, "error": "build_toc() missing"}
    result = builder(str(settings.home), book_id)
    return result if isinstance(result, dict) else {"ok": False}


def _trace(job_id: str, title: str, detail: str) -> None:
    if job_id:
        jobs.append_trace(job_id, "tool", title, detail)


def _apply_llm_intervals(
    planned: list[dict[str, Any]],
    raw_intervals: list[dict[str, Any]],
    by_key: dict[str, list[dict[str, Any]]],
) -> int:
    dropped = 0
    for row in raw_intervals:
        matched = _match_interval(planned, row)
        if matched is None:
            continue
        start = int(matched["start_pdf"])
        end = int(matched["end_pdf"])
        try:
            parsed = validate_interval_entries(_as_rows(row.get("entries")), start, end)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
        if parsed is None:
            dropped += 1
            continue
        by_key[str(matched["key"])] = parsed
    return dropped


def run_book(book_id: str, job_id: str, prompt: str = "prompts/toc_extract.md") -> dict[str, Any]:
    meta = library.get_book(book_id)
    if meta is None:
        raise ValueError(f"book not found: {book_id}")
    page_count = int(meta.get("page_count") or 0)
    if page_count < 1:
        pages_dir = library.book_dir(book_id) / "pages"
        page_count = len(list(pages_dir.glob("*.md"))) if pages_dir.is_dir() else 0
    if page_count < 1:
        return {"book_id": book_id, "intervals": 0, "page_count": 0}

    try:
        system = prompts_store.read_file(prompt or "prompts/toc_extract.md")
    except (FileNotFoundError, ValueError):
        system = ""

    nested = holds_foreign_bridge(job_id)
    asked = False
    llm_payload = {"skeleton_entries": [], "intervals": []}
    cursor = load_cursor(book_id)
    source = "printed"
    skeleton_entries: list[dict[str, Any]] = []
    if isinstance(cursor.get("skeleton"), dict):
        cached_skel = cursor["skeleton"].get("entries")
        if isinstance(cached_skel, list):
            skeleton_entries = [row for row in cached_skel if isinstance(row, dict)]

    pdf_cat = read_pdf_catalog(book_id, page_count)
    pdf_pages = pdf_cat.get("toc_pages") or []
    pdf_hint = f"{pdf_cat.get('pdf') or 'book.pdf'} 页 {pdf_pages}" if pdf_pages else "未找到目录页"
    if pdf_cat.get("source") == "printed_sidecar":
        _trace(
            job_id,
            "toc_extract",
            f"印刷目录侧车（{pdf_hint}）\n{pdf_cat.get('brief') or '（空）'}",
        )
    else:
        _trace(
            job_id,
            "toc_extract",
            f"先看原文 PDF 目录页（{pdf_hint}）\n{pdf_cat.get('brief') or '（空）'}\n{str(pdf_cat.get('text') or '')[:3000]}",
        )
    toc = library.read_toc(book_id)
    toc_entries = toc.get("entries") if isinstance(toc.get("entries"), list) else []
    _trace(job_id, "toc_extract", f"再读 toc.json\n{format_catalog(toc_entries)}")
    catalog = merge_catalog(pdf_cat.get("entries") or [], toc_entries)
    if pdf_cat.get("entries"):
        source = "printed"
        if not skeleton_entries:
            skeleton_entries = [
                {
                    "label": str(row.get("label") or ""),
                    "level": int(row.get("level") or 0),
                    "is_chapter": bool(row.get("is_chapter") or int(row.get("level") or 0) == 0),
                    "pdf_page": int(row.get("pdf_page") or 0) or None,
                    "print_page": row.get("print_page"),
                }
                for row in pdf_cat["entries"]
                if str(row.get("label") or "").strip()
            ]

    if not pdf_cat.get("entries") and not has_printed_catalog(book_id):
        source = "agent"
        digest = _heading_digest(book_id, page_count)
        digest_hash = page_hash([digest])
        cached = cursor.get("skeleton") if isinstance(cursor.get("skeleton"), dict) else {}
        if cached.get("hash") == digest_hash and skeleton_entries:
            _trace(job_id, "toc_extract", "骨架命中缓存")
        else:
            if job_id and jobs.is_cancelled(job_id):
                raise jobs.JobCancelled()
            scanned = scan_skeleton_entries(book_id, page_count)
            if scanned:
                skeleton_entries = scanned
                _trace(job_id, "toc_extract", f"无印刷目录，正文标题建骨架 {len(scanned)}")
            elif nested:
                skeleton_entries = []
                _trace(job_id, "toc_extract", "对话内不另开桥，骨架留空")
            else:
                _trace(job_id, "toc_extract", "无印刷目录，一次问整书骨架")
                reply = _ask(system, _book_prompt(
                    catalog_text=format_catalog(catalog),
                    digest=digest,
                    intervals=[],
                    need_skeleton=True,
                ), job_id)
                asked = True
                try:
                    llm_payload = parse_book_payload(reply)
                    parsed = validate_skeleton_entries(llm_payload["skeleton_entries"])
                except (json.JSONDecodeError, TypeError, ValueError):
                    parsed = None
                skeleton_entries = parsed or []
            cursor["skeleton"] = {"hash": digest_hash, "entries": skeleton_entries}
            save_cursor(book_id, cursor)
        write_agent_sidecar(book_id, source=source, skeleton_entries=skeleton_entries, intervals=[])
    elif pdf_cat.get("entries"):
        write_agent_sidecar(book_id, source=source, skeleton_entries=skeleton_entries, intervals=[])

    run_workspace_toc(book_id)
    toc = library.read_toc(book_id)
    toc_entries = toc.get("entries") if isinstance(toc.get("entries"), list) else []
    catalog = merge_catalog(pdf_cat.get("entries") or [], toc_entries)
    _trace(job_id, "toc_extract", f"对照 toc.json 后\n{format_catalog(catalog)}")

    planned = sibling_intervals(catalog)
    chapter_titles = _chapter_titles(catalog)
    cache = cursor.get("intervals") if isinstance(cursor.get("intervals"), dict) else {}
    by_key: dict[str, list[dict[str, Any]]] = {}
    hashes: dict[str, str] = {}
    texts_by_key: dict[str, list[str]] = {}
    done = 0
    reused = 0
    dropped = 0
    missing: list[dict[str, Any]] = []

    for item in planned:
        if job_id and jobs.is_cancelled(job_id):
            raise jobs.JobCancelled()
        start = int(item["start_pdf"])
        end = min(int(item["end_pdf"]), page_count + 1)
        item["end_pdf"] = end
        texts = _interval_texts(book_id, start, end)
        digest = interval_hash(texts)
        key = str(item["key"])
        hashes[key] = digest
        texts_by_key[key] = texts
        cached = cache.get(key) if isinstance(cache.get(key), dict) else {}
        if cached.get("hash") == digest and isinstance(cached.get("entries"), list):
            by_key[key] = [row for row in cached["entries"] if isinstance(row, dict)]
            reused += 1
            continue
        kids = scan_interval_entries(
            book_id,
            start,
            end,
            str(item["section_label"]),
            chapter_titles,
            section_level=_as_level(item.get("section_level"), 1),
        )
        by_key[key] = kids
        done += 1
        _trace(
            job_id,
            "toc_extract",
            f"扫 {item['section_label']}（p{start}-p{end}）{len(kids)} 条",
        )
        if not kids and interval_has_leftover(book_id, start, end, str(item["section_label"])):
            missing.append({**item, "texts": texts})

    if missing and asked:
        dropped += _apply_llm_intervals(planned, llm_payload.get("intervals") or [], by_key)
    elif missing and nested:
        _trace(job_id, "toc_extract", "对话内不另开桥，小节用正文标题扫描")
    elif missing:
        _trace(job_id, "toc_extract", f"目录看完，一次抽 {len(missing)} 个区间")
        reply = _ask(system, _book_prompt(
            catalog_text=format_catalog(catalog),
            digest="",
            intervals=missing,
            need_skeleton=False,
        ), job_id)
        asked = True
        try:
            llm_payload = parse_book_payload(reply)
            dropped += _apply_llm_intervals(planned, llm_payload.get("intervals") or [], by_key)
        except (json.JSONDecodeError, TypeError, ValueError):
            dropped += len(missing)

    interval_rows: list[dict[str, Any]] = []
    for item in planned:
        key = str(item["key"])
        kids = by_key.get(key) or []
        cache[key] = {"hash": hashes[key], "entries": kids}
        interval_rows.append(
            {
                "start_pdf": int(item["start_pdf"]),
                "end_pdf": int(item["end_pdf"]),
                "section_label": item["section_label"],
                "entries": kids,
            }
        )
        jobs.patch_result(
            job_id,
            persist=True,
            interval=key,
            intervals_done=done,
            intervals_total=len(planned),
        )
        bus.publish("toc_extract", book_id=book_id, interval=key, job_id=job_id)

    cursor["intervals"] = cache
    save_cursor(book_id, cursor)
    write_agent_sidecar(
        book_id,
        source=source,
        skeleton_entries=skeleton_entries,
        intervals=interval_rows,
    )
    built = run_workspace_toc(book_id)
    return {
        "book_id": book_id,
        "source": source,
        "page_count": page_count,
        "intervals": len(planned),
        "extracted": done,
        "reused": reused,
        "dropped": dropped,
        "skeleton": len(skeleton_entries),
        "llm": "once" if asked else ("skipped_nested" if nested else ""),
        "pdf": str(pdf_cat.get("pdf") or ""),
        "pdf_toc_pages": list(pdf_cat.get("toc_pages") or []),
        "toc_entries": int(built.get("entries") or 0) if isinstance(built, dict) else 0,
    }


def toc_extract(book_id: str, root: str = "") -> dict:
    """只扫正文子层。有 toc_printed.json 时不再把目录页字当权威。对话里不另开 Cursor 会话。"""
    sid = (book_id or "").strip()
    if not sid:
        raise ValueError("book_id required")
    try:
        from app.context import live_sdk_job

        job_id = live_sdk_job()
    except ImportError:
        from app.context import current_job_id

        job_id = current_job_id.get()
    return run_book(sid, job_id)
