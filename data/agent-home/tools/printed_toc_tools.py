"""Printed catalog tools. Facts first; the local 35B only where print has no text.

toc_locate    catalog pages: four signals scored, 35B on thumbnails
toc_facts     one page's text lines with position, size, weight
toc_assemble  rows and levels for the whole catalog (draft, no model)
toc_review    35B judges joins, splits and levels; body titles are reference
toc_fill      page numbers the text layer lacks: 35B reads small crops
toc_verify    harness on the body + missing_in_toc report
toc_golden    compare toc.print with frozen answers in eval/printed_toc/

Nothing here writes; ``printed_toc_agent`` writes textbooks/<id>/toc.print.
"""
from __future__ import annotations

import base64
import collections
import difflib
import hashlib
import io
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import printed_toc_layout as layout
import printed_toc_skeleton as skeleton

_SKIP_TYPES = {"header", "footer", "page_number", "page_footnote", "aside_text"}
_TAG = re.compile(r"<[^>]+>")
_NO_INDEX_WINDOWS = ((1, 16), (17, 32))
_THUMB_SIDE = 384
_CONFIRM_SIDE = 1280
_CONFIRM_KINDS = ("目录", "版权页", "前言或说明", "封面", "正文", "其他")
_CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {"kind": {"type": "string", "enum": list(_CONFIRM_KINDS)}},
    "required": ["kind"],
    "additionalProperties": False,
}
_READ_DPI = 120
_CROP_ZOOM = 4
_HEADER_BAND = 0.12
_HEADER_PAGES = 12
_HEADER_REPEAT = 3
_VERIFY_PAGES = 40
_OFFSET_VOTES = 3
_HEADER_DRIFT = 12.0
_TEXT_LAYER_MIN = 40
_TEXT_LAYER_LINES = 3
_PP_CONTENT = 0.3
_TAIL_ROWS = 5
_RUN_MAX = 4
_SCORE_NEED = 2
_LINE_INSIDE = 0.8
_CROPS_MIN_REGIONS = 4
_ASPECT_TOL = 0.05
_SIZE_TOL = 2.0
_LAYOUT_REVISION = "e4f489dc536556fc1be02b973ba06756e0d4a2ac"
_HEADING = re.compile(r"^第\s*\S{1,8}?\s*[章节单元课]")
_CROP_CHAPTER = re.compile(r"^第\s*\S{1,8}?\s*[章单元部分编]")
_CROP_SECTION = re.compile(r"^第\s*\S{1,8}?\s*[节课]")
_CROP_NUM = re.compile(r"^\d+\.\d+")
_LEVEL_MAX = 4
_TITLE_SKIP = re.compile(
    r"^(?:look at|before you|pre-reading|\(\d+\)|(?:how|why|what|when|does|do|is|are)\b)",
    re.I,
)
_NUMBERING = re.compile(
    r"^(?:第\s*\S{1,4}?\s*[章节单元课部分编]|附录\s*\d*|\d+(?:\.\d+)*|[A-H](?=\s)|"
    r"[一二三四五六七八九十]+[、\s]|[（(][^）)]{1,3}[）)]|\*)\s*"
)

_layout_cache: dict[tuple[str, tuple[int, ...]], dict[str, Any]] = {}
_layout_info: dict[str, Any] = {
    "ok": False, "revision": None, "torch": None, "transformers": None,
}


# ---------------------------------------------------------------- shared


def _vision_url() -> str:
    return os.environ.get("TOC_VISION_URL", "http://127.0.0.1:8081/v1/chat/completions")


def _vision_root() -> str:
    raw = _vision_url().rstrip("/")
    for suffix in ("/v1/chat/completions", "/v1/completions", "/chat/completions", "/completions", "/completion"):
        if raw.endswith(suffix):
            return raw[: -len(suffix)]
    return raw


_media_marker: str | None = None


def media_marker() -> str:
    """Placeholder this llama-server substitutes with the image. It changes on restart."""
    global _media_marker
    if _media_marker:
        return _media_marker
    with urllib.request.urlopen(_vision_root() + "/props", timeout=10) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    marker = str(body.get("media_marker") or "")
    if not marker:
        raise RuntimeError("llama-server props has no media_marker")
    _media_marker = marker
    return marker


def qwen_vl_prompt(text: str, marker: str, n_images: int) -> str:
    """Method B. One /props media marker per image, then the question."""
    marks = "\n".join(marker for _ in range(n_images))
    return f"<|im_start|>user\n{marks}\n{text}<|im_end|>\n<|im_start|>assistant\n"


def _layout_url() -> str:
    return (os.environ.get("LAYOUT_SVC_URL") or "").rstrip("/")


def _crops_on() -> bool:
    return os.environ.get("TOC_CROPS", "0").strip().lower() not in {"0", "false", "off"}


def _home() -> Path:
    return Path(__file__).resolve().parents[1]


def _prompt(name: str) -> str:
    return (_home() / "prompts" / name).read_text(encoding="utf-8")


def _file_sha(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def book_dir(book_id: str, root: str = "") -> Path:
    base = Path(root) if root else _home() / "textbooks"
    sid = (book_id or "").strip()
    if (base / sid).is_dir():
        return base / sid
    hits = [p for p in base.iterdir() if p.is_dir() and p.name.startswith(sid)] if sid else []
    if not hits:
        raise FileNotFoundError(sid)
    return hits[0]


def _load_json_list(path: Path | None) -> list:
    if path is None or not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _mineru_file(book: Path, suffix: str) -> Path | None:
    hits = sorted((book / "mineru").glob(f"*{suffix}"))
    return hits[0] if hits else None


def _split(text: object) -> list[str]:
    return [ln.strip() for ln in _TAG.sub("", str(text or "")).splitlines() if ln.strip()]


def collect_pages(book: Path) -> dict[str, Any]:
    """MinerU ``index`` lines, page text and printed page numbers per page. Read only."""
    model = _load_json_list(_mineru_file(book, "_model.json"))
    index: dict[int, list[str]] = {}
    for idx, page in enumerate(model):
        for item in page if isinstance(page, list) else []:
            if isinstance(item, dict) and item.get("type") == "index":
                index.setdefault(idx + 1, []).extend(_split(item.get("content")))
    text: dict[int, list[str]] = {}
    numbers: dict[int, list[str]] = {}
    for item in _load_json_list(_mineru_file(book, "_content_list.json")):
        if not isinstance(item, dict):
            continue
        page = int(item.get("page_idx", -1)) + 1
        if page <= 0:
            continue
        if item.get("type") == "page_number":
            numbers.setdefault(page, []).extend(_split(item.get("text")))
            continue
        if item.get("type") in _SKIP_TYPES:
            continue
        raw = item.get("list_items") if isinstance(item.get("list_items"), list) else item.get("text")
        lines = _split("\n".join(map(str, raw)) if isinstance(raw, list) else raw)
        if lines:
            text.setdefault(page, []).extend(lines)
    return {"page_count": len(model), "index": index, "text": text, "numbers": numbers}


def _open_pdf(book: Path):
    import fitz

    path = book / "book.pdf"
    return fitz.open(path) if path.is_file() else None


def page_image(book: Path, page: int, side: int = 0) -> bytes | None:
    """Page JPEG from images/, or rendered from book.pdf when missing."""
    raw, _ = page_raster(book, page, side)
    return raw


def page_raster(book: Path, page: int, side: int = 0) -> tuple[bytes | None, tuple[int, int] | None]:
    from PIL import Image

    path = book / "images" / f"p{page:04d}.jpg"
    if path.is_file():
        img = Image.open(path)
    else:
        doc = _open_pdf(book)
        if doc is None or page > doc.page_count:
            return None, None
        with doc:
            pix = doc[page - 1].get_pixmap(dpi=_READ_DPI)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    img = img.convert("RGB")
    if side:
        img.thumbnail((side, side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70 if side else 85)
    return buf.getvalue(), img.size


def _image_part(raw: bytes) -> dict[str, Any]:
    url = "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
    return {"type": "image_url", "image_url": {"url": url}}


def _post_json(url: str, payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    body = None
    last: Exception | None = None
    for attempt in range(2):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code < 500 or attempt:
                raise
            time.sleep(2)
    if body is None:
        raise last or RuntimeError("vision empty")
    return body, round(time.perf_counter() - started, 3)


def _content_parts(content: list[dict[str, Any]]) -> tuple[str, list[str]]:
    texts: list[str] = []
    images: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            texts.append(str(part.get("text") or ""))
        elif part.get("type") == "image_url":
            url = str((part.get("image_url") or {}).get("url") or "")
            images.append(url.split(",", 1)[1] if "," in url else url)
    return "\n".join(piece for piece in texts if piece), [img for img in images if img]


_SAMPLED = {"temperature": 0.7, "top_p": 0.8, "top_k": 20}


def _chat(
    content: list[dict[str, Any]],
    max_tokens: int,
    schema: dict[str, Any] | None = None,
    sample: bool = False,
) -> tuple[dict[str, Any], float]:
    # Method A: the server expands the chat template. Method B replies came
    # back as a bare JSON array inside a code fence; the reader only keeps a
    # {"rows": ...} object, so the whole review was dropped.
    # schema: llama.cpp decodes under this JSON schema, so the reply always parses.
    # sample: Qwen3-VL Instruct sampling, for votes that must be able to differ.
    payload: dict[str, Any] = {
        "model": "local",
        "temperature": 0,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": content}],
    }
    if sample:
        payload.update(_SAMPLED)
    if schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": schema},
        }
    body, elapsed = _post_json(_vision_root() + "/v1/chat/completions", payload)
    text = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start : end + 1]) if 0 <= start < end else {}
    except json.JSONDecodeError:
        data = {}
    return (data if isinstance(data, dict) else {}), elapsed


# ---------------------------------------------------------------- layout service


def layout_health() -> dict[str, Any]:
    """Cheap GET /health. Missing service -> ok=False, later steps equal v2."""
    global _layout_info
    url = _layout_url()
    if not url:
        _layout_info = {
            "ok": False, "revision": None, "torch": None, "transformers": None,
        }
        return dict(_layout_info)
    try:
        req = urllib.request.Request(url + "/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        _layout_info = {
            "ok": True,
            "revision": data.get("revision") or _LAYOUT_REVISION,
            "torch": data.get("torch"),
            "transformers": data.get("transformers"),
            "model": data.get("model"),
        }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        _layout_info = {
            "ok": False, "revision": None, "torch": None, "transformers": None,
        }
    return dict(_layout_info)


def layout_regions(book: Path, pages: list[int], threshold: float = _PP_CONTENT) -> dict[str, Any]:
    """Send page images to the layout service. Unavailable -> {ok: False}."""
    key = (str(book.resolve()), tuple(pages), round(threshold, 3))
    cached = _layout_cache.get(key)
    if cached is not None:
        return cached
    url = _layout_url()
    empty = {"ok": False, "pages": {}, "revision": None, "torch": None, "transformers": None}
    if not url or not pages:
        _layout_cache[key] = empty
        return empty
    payload_pages = []
    sizes: dict[int, tuple[int, int]] = {}
    for page in pages:
        raw, size = page_raster(book, page)
        if raw is None or size is None:
            continue
        payload_pages.append({
            "page": page,
            "image_b64": base64.b64encode(raw).decode("ascii"),
        })
        sizes[page] = size
    if not payload_pages:
        _layout_cache[key] = empty
        return empty
    body = {"pages": payload_pages, "threshold": threshold}
    try:
        req = urllib.request.Request(
            url + "/v1/layout/pages",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        _layout_cache[key] = empty
        return empty
    per: dict[int, dict[str, Any]] = {}
    for item in data.get("pages") or []:
        page = int(item.get("page") or 0)
        if page <= 0:
            continue
        per[page] = {
            "width": item.get("width") or (sizes.get(page) or (0, 0))[0],
            "height": item.get("height") or (sizes.get(page) or (0, 0))[1],
            "regions": item.get("regions") or [],
            "seconds": item.get("seconds") or 0.0,
        }
    out = {
        "ok": True,
        "pages": per,
        "revision": data.get("revision") or _LAYOUT_REVISION,
        "torch": data.get("torch"),
        "transformers": data.get("transformers"),
        "threshold": data.get("threshold", threshold),
    }
    _layout_info.update({
        "ok": True,
        "revision": out["revision"],
        "torch": out["torch"],
        "transformers": out["transformers"],
    })
    _layout_cache[key] = out
    return out


def content_score(regions: list[dict[str, Any]]) -> float:
    best = 0.0
    for region in regions:
        if region.get("label") == "content":
            best = max(best, float(region.get("score") or 0.0))
    return best


# ---------------------------------------------------------------- scoring (pure)


def score_signals(
    index_lines: int,
    tail_rows: int,
    pp_score: float,
    vision_yes: bool | None,
    *,
    pp_thr: float = _PP_CONTENT,
    tail_thr: int = _TAIL_ROWS,
) -> dict[str, Any]:
    """Four add-only signals. None/False vision is 0, never a veto."""
    index = 2 if index_lines >= 3 else 0
    tails = 1 if tail_rows >= tail_thr else 0
    pp = 1 if pp_score >= pp_thr else 0
    vision = 2 if vision_yes is True else 0
    pre = index + tails + pp
    return {
        "index": index,
        "tails": tails,
        "pp": pp,
        "vision": vision,
        "pre": pre,
        "points": pre + vision,
        "pp_score": round(pp_score, 4),
        "tail_rows": tail_rows,
        "index_lines": index_lines,
        "disagree": False,
    }


def pick_run(pages: list[int], cap: int = _RUN_MAX) -> list[int]:
    """Longest consecutive run, capped; ties keep the earlier run."""
    pages = sorted(set(int(p) for p in pages if p and p > 0))
    if not pages:
        return []
    runs: list[list[int]] = []
    cur = [pages[0]]
    for page in pages[1:]:
        if page == cur[-1] + 1:
            cur.append(page)
        else:
            runs.append(cur)
            cur = [page]
    runs.append(cur)
    best = max(runs, key=lambda run: (len(run), -run[0]))
    return best[:cap]


def should_fallback(check: dict[str, Any], readable: bool, rotation: int = 0) -> bool:
    """Look at the image only when the text layer is thin or the page geometry is broken.

    A pass_rate between 0.3 and 0.8 (rows sitting outside a MinerU blob) is
    recorded but not enough to abandon a readable text layer.
    """
    if rotation:
        return True
    if not readable:
        return True
    if not check.get("size_ok", True) or not check.get("aspect_ok", True):
        return True
    return float(check.get("pass_rate") or 1.0) < 0.3


def self_check_lines(
    lines: list[dict[str, Any]],
    pdf_size: tuple[float, float] | None,
    mineru_size: tuple[float, float] | None,
    image_size: tuple[float, float] | None,
    boxes: list[list[float]],
    *,
    inside: float = _LINE_INSIDE,
) -> dict[str, Any]:
    """Three geometry checks. Missing MinerU size/boxes are skipped, not failed."""
    reasons: list[str] = []
    size_ok = True
    aspect_ok = True
    inside_ok = True
    pass_rate = 1.0
    if pdf_size and mineru_size:
        size_ok = (
            abs(pdf_size[0] - mineru_size[0]) <= _SIZE_TOL
            and abs(pdf_size[1] - mineru_size[1]) <= _SIZE_TOL
        )
        if not size_ok:
            reasons.append("size_mismatch")
    if pdf_size and image_size and min(*pdf_size, *image_size) > 0:
        sx = image_size[0] / pdf_size[0]
        sy = image_size[1] / pdf_size[1]
        aspect_ok = abs(sx - sy) / max(sx, 1e-6) <= _ASPECT_TOL
        if not aspect_ok:
            reasons.append("aspect_mismatch")
    if boxes and lines:
        hit = 0
        for ln in lines:
            cx = (float(ln["x0"]) + float(ln["x1"])) / 2
            cy = (float(ln["y0"]) + float(ln["y1"])) / 2
            if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes):
                hit += 1
        pass_rate = hit / len(lines)
        inside_ok = pass_rate >= inside
        if not inside_ok:
            reasons.append("outside_mineru")
    elif lines and mineru_size is not None and not boxes:
        inside_ok = False
        pass_rate = 0.0
        reasons.append("no_mineru_boxes")
    ok = size_ok and aspect_ok and inside_ok
    return {
        "ok": ok,
        "reasons": reasons,
        "pass_rate": round(pass_rate, 3),
        "size_ok": size_ok,
        "aspect_ok": aspect_ok,
        "inside_ok": inside_ok,
    }


def cache_usable(old: dict[str, Any] | None, new_inputs: dict[str, Any], force: bool) -> bool:
    if force or not old or not isinstance(old.get("inputs"), dict):
        return False
    return json.dumps(old["inputs"], sort_keys=True) == json.dumps(new_inputs, sort_keys=True)


def inputs_fingerprint(book: Path, layout_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = layout_info if layout_info is not None else _layout_info
    fitz_ver = ""
    try:
        import fitz

        fitz_ver = getattr(fitz, "VersionBind", None) or (fitz.version[0] if fitz.version else "")
    except Exception:  # noqa: BLE001
        fitz_ver = ""
    return {
        "pdf_sha256": _file_sha(book / "book.pdf"),
        "pymupdf": str(fitz_ver),
        "layout_revision": info.get("revision"),
        "layout_threshold": _PP_CONTENT,
        "tail_rows": _TAIL_ROWS,
        "run_max": _RUN_MAX,
        "torch": info.get("torch"),
        "transformers": info.get("transformers"),
        "prompts": {
            "locate": _file_sha(_home() / "prompts" / "printed_toc_locate.md"),
            "confirm": _file_sha(_home() / "prompts" / "printed_toc_confirm.md"),
            "lines": _file_sha(_home() / "prompts" / "printed_toc_lines.md"),
            "review": _file_sha(_home() / "prompts" / "printed_toc_review.md"),
            "fill": _file_sha(_home() / "prompts" / "printed_toc_fill.md"),
        },
    }


# ---------------------------------------------------------------- mineru geometry


def mineru_page_geom(book: Path, page: int) -> dict[str, Any]:
    """page_size + block boxes from mineru/layout.json, PDF-point coordinates."""
    path = book / "mineru" / "layout.json"
    if not path.is_file():
        return {"size": None, "boxes": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"size": None, "boxes": []}
    pages = data.get("pdf_info") if isinstance(data, dict) else None
    if not isinstance(pages, list):
        return {"size": None, "boxes": []}
    hit = None
    for item in pages:
        if isinstance(item, dict) and int(item.get("page_idx", -1)) + 1 == page:
            hit = item
            break
    if hit is None:
        return {"size": None, "boxes": []}
    raw_size = hit.get("page_size") or []
    size = (float(raw_size[0]), float(raw_size[1])) if len(raw_size) == 2 else None
    boxes: list[list[float]] = []
    for key in ("para_blocks", "preproc_blocks"):
        for block in hit.get(key) or []:
            box = block.get("bbox") if isinstance(block, dict) else None
            if isinstance(box, list) and len(box) == 4:
                boxes.append([float(v) for v in box])
    return {"size": size, "boxes": boxes}


# ---------------------------------------------------------------- 1 locate


def _plan(collected: dict[str, Any]) -> tuple[str, list[list[int]]]:
    count = int(collected["page_count"] or 0)
    front = sorted(p for p in collected["index"] if p <= _NO_INDEX_WINDOWS[0][1])
    if front:
        around = set(front) | {front[0] - 1, front[-1] + 1}
        return "index", [sorted(p for p in around if 2 <= p <= count)]
    windows = [list(range(a, min(b, count) + 1)) for a, b in _NO_INDEX_WINDOWS if a <= count]
    return "model", windows


def _locate_window(book: Path, candidates: list[int]) -> tuple[list[int], float, bool]:
    content: list[dict[str, Any]] = [{"type": "text", "text": _prompt("printed_toc_locate.md")}]
    shown: list[int] = []
    for page in candidates:
        raw = page_image(book, page, _THUMB_SIDE)
        if raw is None:
            continue
        content.append({"type": "text", "text": f"第 {page} 页："})
        content.append(_image_part(raw))
        shown.append(page)
    if not shown:
        return [], 0.0, False
    try:
        data, elapsed = _chat(content, 60)
        ok = True
    except (urllib.error.URLError, TimeoutError, OSError):
        return [], 0.0, False
    pages: list[int] = []
    for raw in data.get("toc_pages") or []:
        try:
            page = int(raw)
        except (TypeError, ValueError):
            continue
        if page in shown and page not in pages:
            pages.append(page)
    return sorted(pages), elapsed, ok


def confirm_page(book: Path, page: int) -> tuple[str | None, float]:
    """Full-page kind for a page only the thumbnail vote picked. None = no answer."""
    raw = page_image(book, page, _CONFIRM_SIDE)
    if raw is None:
        return None, 0.0
    text = _prompt("printed_toc_confirm.md").replace("{page}", str(page))
    try:
        data, elapsed = _chat([{"type": "text", "text": text}, _image_part(raw)], 40, schema=_CONFIRM_SCHEMA)
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError, json.JSONDecodeError):
        return None, 0.0
    kind = data.get("kind")
    return (kind if kind in _CONFIRM_KINDS else None), elapsed


def _confirm_vision_only(book: Path, scores: dict[int, dict[str, Any]]) -> list[float]:
    """Thumbnail vote is the only signal: look at the whole page once; a non-catalog kind drops the vote."""
    seconds: list[float] = []
    for page, item in sorted(scores.items()):
        if item["pre"] != 0 or not item["vision"]:
            continue
        kind, elapsed = confirm_page(book, page)
        seconds.append(elapsed)
        item["confirm"] = kind
        if kind is not None and kind != _CONFIRM_KINDS[0]:
            item["vision"] = 0
            item["points"] = item["pre"]
    return seconds


def _pre_scores(
    book: Path, collected: dict[str, Any], pages: list[int], laid: dict[str, Any]
) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    per = laid.get("pages") or {}
    doc = _open_pdf(book)
    try:
        for page in pages:
            index_n = len(collected["index"].get(page) or [])
            tails = 0
            if doc is not None and page <= doc.page_count:
                tails = layout.page_tail_count(layout.text_layer_lines(doc[page - 1], edge=0))
            pp = content_score((per.get(page) or {}).get("regions") or [])
            out[page] = score_signals(index_n, tails, pp, None)
    finally:
        if doc is not None:
            doc.close()
    return out


def _expand(pages: list[int], count: int) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for page in pages:
        for cand in (page - 1, page, page + 1):
            if 2 <= cand <= count and cand not in seen:
                seen.add(cand)
                out.append(cand)
    return sorted(out)


def _front_windows(collected: dict[str, Any], scores: dict[int, dict[str, Any]], count: int) -> tuple[str, list[list[int]]]:
    """35B windows: front-band signals first. A later-page PP hit must not steal them."""
    front = [p for p in scores if p <= _NO_INDEX_WINDOWS[0][1] and scores[p]["pre"] > 0]
    if front:
        route = "index" if any(scores[p]["index"] for p in front) else "vote"
        return route, [_expand(front, count)]
    return _plan(collected)


def locate(book: Path, collected: dict[str, Any]) -> dict[str, Any]:
    count = int(collected["page_count"] or 0)
    w1 = [p for p in range(1, min(16, count) + 1)]
    w2 = [p for p in range(17, min(32, count) + 1)]
    laid = layout_regions(book, [p for p in w1 if p >= 2] or w1)
    scores = _pre_scores(book, collected, w1, laid)
    route, windows = _front_windows(collected, scores, count)
    if not windows:
        windows = [w1]
    toc_pages: list[int] = []
    seconds: list[float] = []
    shown: list[int] = []
    vision_ok = False
    vision_hit: list[int] = []
    for window in windows:
        found, elapsed, ok = _locate_window(book, window)
        seconds.append(elapsed)
        shown.extend(p for p in window if p not in shown)
        vision_ok = vision_ok or ok
        if found:
            vision_hit = found
            break
    if not vision_hit and w2 and not any(
        p <= 16 and item["pre"] > 0 for p, item in scores.items()
    ):
        laid2 = layout_regions(book, w2)
        scores.update(_pre_scores(book, collected, w2, laid2))
        back = [p for p in w2 if scores.get(p, {}).get("pre", 0) > 0]
        extra = [_expand(back, count)] if back else [w2]
        for window in extra:
            found, elapsed, ok = _locate_window(book, window)
            seconds.append(elapsed)
            shown.extend(p for p in window if p not in shown)
            vision_ok = vision_ok or ok
            if found:
                vision_hit = found
                windows = list(windows) + extra
                break
    for page in shown:
        item = scores.setdefault(
            page, score_signals(len(collected["index"].get(page) or []), 0, 0.0, None)
        )
        if vision_ok:
            item["vision"] = 2 if page in vision_hit else 0
            item["points"] = item["pre"] + item["vision"]
            item["disagree"] = item["vision"] == 0 and item["pre"] >= _SCORE_NEED
        else:
            item["vision"] = 0
            item["points"] = item["pre"]
            item["disagree"] = False
    if vision_ok:
        seconds.extend(_confirm_vision_only(book, scores))
    picked = [p for p, item in scores.items() if item["points"] >= _SCORE_NEED]
    toc_pages = pick_run(picked, _RUN_MAX)
    kept = [p for p in vision_hit if scores.get(p, {}).get("vision")]
    if not toc_pages and kept:
        toc_pages = pick_run(kept, _RUN_MAX)
    disagree = [p for p, item in scores.items() if item.get("disagree")]
    return {
        "route": route,
        "candidates": windows,
        "toc_pages": toc_pages,
        "seconds": seconds,
        "scores": {str(p): item for p, item in sorted(scores.items())},
        "disagree": disagree,
    }


def toc_locate(book_id: str, root: str = "") -> dict[str, Any]:
    """Find the printed catalog pages (scored signals + 35B thumbnails). Read only."""
    book = book_dir(book_id, root)
    return locate(book, collect_pages(book))


# ---------------------------------------------------------------- 2 facts


def _in_band(ln: dict[str, Any], height: float) -> bool:
    middle = (ln["y0"] + ln["y1"]) / 2
    return middle < height * _HEADER_BAND or middle > height * (1 - _HEADER_BAND)


def running_headers(doc: Any, toc_pages: list[int]) -> dict[str, float]:
    """Header / footer texts repeated on the pages after the catalog -> their usual height."""
    if doc is None or not toc_pages:
        return {}
    seen: dict[str, list[float]] = {}
    last = max(toc_pages)
    for page in range(last + 1, min(doc.page_count, last + _HEADER_PAGES) + 1):
        height = doc[page - 1].rect.height
        keys: dict[str, float] = {}
        for ln in layout.text_layer_lines(doc[page - 1], edge=0):
            key = layout.header_key(ln["text"])
            if len(key) >= 2 and _in_band(ln, height):
                keys.setdefault(key, (ln["y0"] + ln["y1"]) / 2)
        for key, middle in keys.items():
            seen.setdefault(key, []).append(middle)
    return {
        key: sorted(ys)[len(ys) // 2] for key, ys in seen.items() if len(ys) >= _HEADER_REPEAT
    }


def drop_headers(item: dict[str, Any], headers: dict[str, float]) -> None:
    """A catalog line is a running header only at the edge and at the header's usual height."""
    kept = []
    for ln in item["lines"]:
        usual = headers.get(layout.header_key(ln["text"]))
        middle = (ln["y0"] + ln["y1"]) / 2
        if usual is not None and _in_band(ln, item["height"]) and abs(middle - usual) <= _HEADER_DRIFT:
            continue
        kept.append(ln)
    item["lines"] = kept


def has_text_layer(doc: Any, pages: list[int]) -> bool:
    if doc is None:
        return False
    chars = sum(len(doc[p - 1].get_text("text").strip()) for p in pages if p <= doc.page_count)
    return chars >= _TEXT_LAYER_MIN * max(1, len(pages))


def _model_lines(book: Path, page: int) -> tuple[list[dict[str, Any]], float, float, float]:
    raw, size = page_raster(book, page)
    if raw is None or size is None:
        return [], 0.0, 0.0, 0.0
    width, height = size
    content = [_image_part(raw), {"type": "text", "text": _prompt("printed_toc_lines.md")}]
    data, elapsed = _chat(content, 4000)
    rows = data.get("lines") if isinstance(data.get("lines"), list) else []
    return layout.model_lines(rows, float(width), float(height)), elapsed, float(width), float(height)


def _column_boxes(regions: list[dict[str, Any]], width: float) -> list[list[float]]:
    useful = [
        r for r in regions
        if r.get("label") in {"content", "text", "paragraph_title", "doc_title"}
        and isinstance(r.get("box"), list) and len(r["box"]) == 4
    ]
    if len(useful) < _CROPS_MIN_REGIONS:
        return []
    items = []
    for region in useful:
        x0, y0, x1, y1 = (float(v) for v in region["box"])
        items.append({"box": [x0, y0, x1, y1], "cx": (x0 + x1) / 2})
    xs = sorted(item["cx"] for item in items)
    cuts = []
    for a, b in zip(xs, xs[1:]):
        if b - a > width * 0.12:
            cuts.append((a + b) / 2)
    bounds = [-1.0] + cuts + [width + 1.0]
    cols: list[list[float]] = []
    for lo, hi in zip(bounds, bounds[1:]):
        group = [item for item in items if lo <= item["cx"] < hi]
        if not group:
            continue
        cols.append([
            min(item["box"][0] for item in group),
            min(item["box"][1] for item in group),
            max(item["box"][2] for item in group),
            max(item["box"][3] for item in group),
        ])
    return cols


def _crop_columns(
    book: Path, page: int, regions: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], float, float, float]:
    raw, size = page_raster(book, page)
    if raw is None or size is None:
        return [], 0.0, 0.0, 0.0
    from PIL import Image

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    width, height = img.size
    cols = _column_boxes(regions, float(width))
    if not cols:
        return [], 0.0, float(width), float(height)
    lines: list[dict[str, Any]] = []
    seconds = 0.0
    for box in cols:
        pad = 8
        x0 = max(0, int(box[0]) - pad)
        y0 = max(0, int(box[1]) - pad)
        x1 = min(width, int(box[2]) + pad)
        y1 = min(height, int(box[3]) + pad)
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        crop = img.crop((x0, y0, x1, y1))
        buf = io.BytesIO()
        crop.save(buf, "JPEG", quality=85)
        content = [
            _image_part(buf.getvalue()),
            {"type": "text", "text": _prompt("printed_toc_lines.md")},
        ]
        data, elapsed = _chat(content, 4000)
        seconds += elapsed
        rows = data.get("lines") if isinstance(data.get("lines"), list) else []
        chunk = layout.model_lines(rows, float(crop.size[0]), float(crop.size[1]))
        for ln in chunk:
            ln["x0"] += x0
            ln["x1"] += x0
            ln["y0"] += y0
            ln["y1"] += y0
        lines.extend(chunk)
    return lines, seconds, float(width), float(height)


def _page_chars(doc: Any, page: int) -> int:
    if doc is None or page > doc.page_count:
        return 0
    return len(doc[page - 1].get_text("text").strip())


def page_facts(
    book: Path,
    doc: Any,
    page: int,
    text_layer: bool | None = None,
    *,
    regions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Per-page facts. text_layer=None means decide here (self-check + crops)."""
    meta: dict[str, Any] = {
        "page": page,
        "method": "none",
        "reason": "",
        "rotation": 0,
        "scale": None,
        "self_check": None,
    }
    pdf_size = None
    if doc is not None and page <= doc.page_count:
        rect = doc[page - 1].rect
        pdf_size = (float(rect.width), float(rect.height))
        meta["rotation"] = int(getattr(doc[page - 1], "rotation", 0) or 0)
    raw, image_size = page_raster(book, page)
    if image_size and pdf_size and min(*pdf_size) > 0:
        meta["scale"] = [
            round(image_size[0] / pdf_size[0], 4),
            round(image_size[1] / pdf_size[1], 4),
        ]
    geom = mineru_page_geom(book, page)
    force_layer = text_layer is True
    force_model = text_layer is False
    if not force_model and doc is not None and page <= doc.page_count:
        lines = layout.text_layer_lines(doc[page - 1])
        chars = _page_chars(doc, page)
        readable = chars >= _TEXT_LAYER_MIN and len(lines) >= _TEXT_LAYER_LINES
        check = self_check_lines(
            lines, pdf_size, geom["size"],
            (float(image_size[0]), float(image_size[1])) if image_size else None,
            geom["boxes"],
        )
        meta["self_check"] = check
        if meta["rotation"]:
            check = dict(check, ok=False, reasons=check["reasons"] + ["rotated"])
            meta["self_check"] = check
        if force_layer or (readable and not should_fallback(check, True, meta["rotation"])):
            meta["method"] = "text_layer"
            return {
                "page": page, "method": "text_layer", "seconds": 0.0,
                "width": pdf_size[0] if pdf_size else 0.0,
                "height": pdf_size[1] if pdf_size else 0.0,
                "lines": lines, "meta": meta,
            }
        meta["reason"] = ",".join(check["reasons"]) if check["reasons"] else (
            "thin_text_layer" if not readable else "fallback"
        )
    if _crops_on() and regions and len(regions) >= _CROPS_MIN_REGIONS:
        lines, elapsed, width, height = _crop_columns(book, page, regions)
        if lines:
            meta["method"] = "crops"
            meta["reason"] = meta["reason"] or "no_text_layer"
            return {
                "page": page, "method": "crops", "seconds": elapsed,
                "width": width, "height": height, "lines": lines, "meta": meta,
            }
    lines, elapsed, width, height = _model_lines(book, page)
    meta["method"] = "model_lines"
    meta["reason"] = meta["reason"] or "no_text_layer"
    return {
        "page": page, "method": "model_lines", "seconds": elapsed,
        "width": width, "height": height, "lines": lines, "meta": meta,
    }


def _public_line(ln: dict[str, Any]) -> dict[str, Any]:
    return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in ln.items() if k != "chars"}


def toc_facts(book_id: str, page: int, root: str = "") -> dict[str, Any]:
    """One page's text lines with box, size, weight (text layer, else 35B). Read only."""
    book = book_dir(book_id, root)
    doc = _open_pdf(book)
    try:
        facts = page_facts(book, doc, page)
    finally:
        if doc is not None:
            doc.close()
    facts["lines"] = [_public_line(ln) for ln in facts["lines"]]
    return facts


# ---------------------------------------------------------------- 3 assemble


def _toc_repeat_headers(facts: list[dict[str, Any]]) -> dict[str, float]:
    """Short edge lines repeated on the catalog pages themselves, such as a decorated 目录."""
    seen: dict[str, list[float]] = {}
    for item in facts:
        height = float(item.get("height") or 0)
        if height <= 0:
            continue
        keys: dict[str, float] = {}
        for ln in item.get("lines") or []:
            if not _in_band(ln, height):
                continue
            key = layout.header_key(ln["text"])
            if len(key) < 2 or len(key) > 12:
                continue
            if layout._STARTS_ITEM.match(ln["text"]) or layout._LABEL_PREFIX.match(ln["text"]):
                continue
            keys.setdefault(key, (float(ln["y0"]) + float(ln["y1"])) / 2)
        for key, mid in keys.items():
            seen.setdefault(key, []).append(mid)
    return {key: sorted(ys)[len(ys) // 2] for key, ys in seen.items() if len(ys) >= 2}


def assemble(
    facts: list[dict[str, Any]], headers: dict[str, float], model_wraps: bool = True
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    learned = dict(headers)
    learned.update(_toc_repeat_headers(facts))
    for item in facts:
        drop_headers(item, learned)
        chunk = layout.page_rows(item["lines"], item["page"], model_wraps)
        for row in chunk:
            row["fact_method"] = item["method"]
        rows.extend(chunk)
    layout.assign_levels(rows)
    _tag_levels(rows)
    return rows


def _tag_levels(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        if row.get("fact_method") == "crops":
            text = row["text"]
            if _CROP_CHAPTER.match(text):
                row["level"] = 0
            elif _CROP_SECTION.match(text):
                row["level"] = 1
            elif _CROP_NUM.match(text):
                row["level"] = min(row.get("level", 2), 2)
            row["level_from"] = "crop"
            row["level_conf"] = "low"
        else:
            row["level_from"] = "style"
            row["level_conf"] = "high"


def _catalog(
    book: Path, pages: list[int], model_wraps: bool = True
) -> tuple[list[dict[str, Any]], str, float, list[dict[str, Any]]]:
    laid = layout_regions(book, pages) if pages else {"ok": False, "pages": {}}
    per = laid.get("pages") or {}
    doc = _open_pdf(book)
    try:
        facts = [
            page_facts(book, doc, page, regions=(per.get(page) or {}).get("regions"))
            for page in pages
        ]
        use_headers = any(item["method"] == "text_layer" for item in facts)
        headers = running_headers(doc, pages) if use_headers else {}
    finally:
        if doc is not None:
            doc.close()
    seconds = round(sum(item["seconds"] for item in facts), 3)
    methods = {item["method"] for item in facts}
    if methods == {"text_layer"}:
        method = "text_layer"
    elif methods == {"model_lines"}:
        method = "model_lines"
    elif methods == {"crops"}:
        method = "crops"
    elif not methods:
        method = "none"
    else:
        method = "mixed"
    pages_meta = [item.get("meta") or {"page": item["page"], "method": item["method"]} for item in facts]
    return assemble(facts, headers, model_wraps), method, seconds, pages_meta


def _pages_arg(pages: str, book: Path) -> list[int]:
    found = [int(p) for p in re.findall(r"\d+", pages or "")]
    if found:
        return found
    old = book / "toc.print"
    if old.is_file():
        return list(json.loads(old.read_text(encoding="utf-8")).get("toc_pages") or [])
    return []


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    keep = ("pdf_page", "col", "text", "level", "print_page", "hole", "size", "bold", "indent")
    return {k: row.get(k) for k in keep}


def toc_assemble(book_id: str, pages: str = "", root: str = "") -> dict[str, Any]:
    """Rows and levels of the catalog pages (default: toc_pages of toc.print). No model."""
    book = book_dir(book_id, root)
    rows, method, _, _ = _catalog(book, _pages_arg(pages, book))
    return {"method": method, "rows": [_public_row(r) for r in rows]}


# ---------------------------------------------------------------- 3b review


def _title_line(text: str) -> bool:
    """A short body line that can be a heading. Questions and instructions are not."""
    text = text.strip()
    if len(text) < 2 or len(text) > 72 or text[-1] in ".?!。？！":
        return False
    if _TITLE_SKIP.match(text):
        return False
    letters = sum(1 for ch in text if unicodedata.category(ch).startswith("L"))
    return letters >= 2 and len(text.split()) <= 14


def _title_hits_row(key: str, rows: list[dict[str, Any]]) -> bool:
    if len(key) < 4:
        return False
    for row in rows:
        body = _plain(str(row.get("text") or ""))
        if key in body or (len(body) >= 4 and body in key):
            return True
    return False


def body_titles(
    collected: dict[str, Any],
    toc_pages: list[int],
    rows: list[dict[str, Any]],
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Short body lines after the catalog that overlap a draft row. Reference only."""
    after = max(toc_pages or [0])
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages = collected.get("text") or {}
    for page in sorted(pages):
        if page <= after:
            continue
        for line in (pages[page] or [])[:8]:
            text = layout.tidy(str(line))
            if not _title_line(text):
                continue
            key = _plain(text)
            if key in seen or not _title_hits_row(key, rows):
                continue
            seen.add(key)
            found.append({"pdf_page": page, "text": text})
            if len(found) >= limit:
                return found
    return found


def _kept_page(row: dict[str, Any]) -> int | None:
    page = row.get("print_page")
    return page if isinstance(page, int) else None


def _mark_review(row: dict[str, Any], level: int) -> dict[str, Any]:
    row["level"] = level
    row["level_from"] = "review"
    row["level_conf"] = "high"
    return row


def _join_group(group: list[dict[str, Any]], level: int) -> dict[str, Any]:
    merged = dict(group[0])
    page = None
    hole = None
    hole_box = None
    for row in group:
        if _kept_page(row) is not None:
            page = row["print_page"]
        elif page is None and row.get("hole"):
            hole = row.get("hole")
            hole_box = row.get("hole_box")
    for nxt in group[1:]:
        merged = layout._absorb(merged, nxt, layout._join(merged["text"], nxt["text"]))
    merged["print_page"] = page
    if page is not None:
        merged["hole"] = None
        merged["hole_box"] = None
    else:
        merged["hole"] = hole
        merged["hole_box"] = hole_box
    return _mark_review(merged, level)


def _split_group(row: dict[str, Any], parts: list[Any]) -> list[dict[str, Any]] | None:
    parent = _plain(row["text"])
    parsed: list[tuple[str, int, bool]] = []
    for part in parts:
        if not isinstance(part, dict):
            return None
        text = layout.tidy(str(part.get("text") or ""))
        level = part.get("level")
        if not text or not isinstance(level, int) or not 0 <= level <= _LEVEL_MAX:
            return None
        key = _plain(text)
        if len(key) < 2 or key not in parent:
            return None
        parsed.append((text, level, bool(part.get("keep_page"))))
    if len(parsed) < 2:
        return None
    keepers = [i for i, item in enumerate(parsed) if item[2]]
    page = _kept_page(row)
    if page is not None and len(keepers) > 1:
        return None
    keep_at = (keepers[0] if keepers else len(parsed) - 1) if page is not None else None
    y0, y1 = float(row["y0"]), float(row["y1"])
    span = (y1 - y0) / len(parsed)
    out: list[dict[str, Any]] = []
    for i, (text, level, _) in enumerate(parsed):
        item = dict(row)
        item["text"] = text
        item["chars"] = None
        item["y0"] = y0 + span * i
        item["y1"] = y0 + span * (i + 1)
        if keep_at is None or i != keep_at:
            item["print_page"] = None
            item["hole"] = None
            item["hole_box"] = None
            item.pop("filled", None)
        out.append(_mark_review(item, level))
    return out


def _level_ok(level: object) -> bool:
    return isinstance(level, int) and not isinstance(level, bool) and 0 <= level <= _LEVEL_MAX


def apply_review(rows: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Apply one page of review JSON. None means the draft stays: bad shape, gap, or rewritten text."""
    items = spec.get("rows") if isinstance(spec, dict) else None
    if not isinstance(items, list):
        return None
    expect = 1
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            return None
        src = item.get("from")
        if (
            not isinstance(src, list)
            or not src
            or not all(isinstance(i, int) and not isinstance(i, bool) for i in src)
        ):
            return None
        if src != list(range(src[0], src[-1] + 1)) or src[0] != expect:
            return None
        if src[-1] > len(rows):
            return None
        expect = src[-1] + 1
        group = [rows[i - 1] for i in src]
        parts = item.get("parts")
        if parts is not None:
            if len(src) != 1 or not isinstance(parts, list):
                return None
            built = _split_group(group[0], parts)
            if built is None:
                return None
            out.extend(built)
            continue
        if not _level_ok(item.get("level")):
            return None
        if len(group) == 1:
            out.append(_mark_review(dict(group[0]), int(item["level"])))
        else:
            out.append(_join_group(group, int(item["level"])))
    if expect != len(rows) + 1:
        return None
    return out


_CONT = re.compile(r"^(?:与|和|及|并|或)|^[a-z]")
_PREFIX = re.compile(
    r"^(?:(?P<label>Reading|Listening|Speaking|Writing|Video|Mini-project|Focus on language)\s*:|"
    r"[（(](?P<paren>[一二三四五六七八九十]+)[）)]|(?P<num>\d+)\s|(?P<cn>[一二三四五六七八九十]+)[、\s]|(?P<letter>[A-E])\s)"
)
_PART = re.compile(r"^第\s*[\d一二三四五六七八九十]+\s*部分")


def _item_head(text: str, titles: list[dict[str, Any]]) -> bool:
    stripped = text.strip()
    if layout._STARTS_ITEM.match(stripped) or layout._LABEL_PREFIX.match(stripped):
        return True
    if _HEADING.match(stripped):
        return True
    key = _plain(stripped)
    if len(key) < 4:
        return False
    return any(_plain(str(title.get("text") or "")) == key for title in titles)


def _has_page_tail(row: dict[str, Any]) -> bool:
    if row.get("print_page") is not None:
        return True
    text = str(row.get("text") or "")
    return layout._PAGE_TAIL.match(text) is not None or layout._PAGE_ONLY.match(text) is not None


def _prefix_key(text: str) -> str | None:
    match = _PREFIX.match(text.strip())
    if not match:
        return None
    for name, value in match.groupdict().items():
        if value:
            return name if name != "label" else value.lower()
    return None


def _sibling_veto(left: str, right: str, page_rows: list[dict[str, Any]]) -> bool:
    key = _prefix_key(left)
    if not key or key != _prefix_key(right):
        return False
    count = sum(1 for row in page_rows if _prefix_key(str(row.get("text") or "")) == key)
    return count >= 3


def _merge_veto(group: list[dict[str, Any]], page_rows: list[dict[str, Any]], titles: list[dict[str, Any]]) -> str | None:
    for prev, nxt in zip(group, group[1:]):
        if _sibling_veto(str(prev["text"]), str(nxt["text"]), page_rows):
            return "v3_sibling"
        if _item_head(str(nxt["text"]), titles):
            return "v1_item_head"
        if _has_page_tail(prev) and _has_page_tail(nxt):
            return "v2_page_tail"
    return None


def _text_merge_block(group: list[dict[str, Any]]) -> str | None:
    """Text-layer merges need a continuation, no page on the first line, same column, compatible indent."""
    for prev, nxt in zip(group, group[1:]):
        if prev.get("col") != nxt.get("col"):
            return "not_same_column"
        if _has_page_tail(prev):
            return "page_tail"
        if float(nxt["x0"]) < float(prev["x0"]) - 4:
            return "indent"
        if not _CONT.match(str(nxt["text"]).strip()):
            return "no_continuation"
    return None


def _level_reason(
    row: dict[str, Any],
    new_level: int,
    page_rows: list[dict[str, Any]],
    titles: list[dict[str, Any]],
) -> str | None:
    if _PART.match(str(row["text"])):
        peers = [other["level"] for other in page_rows if other is not row and _PART.match(str(other["text"]))]
        if peers and new_level in peers:
            return "numbering"
        return None
    key = _plain(str(row["text"]))
    if new_level == 0 and len(key) >= 2 and any(_plain(str(title.get("text") or "")) == key for title in titles):
        return "body"
    if layout._LABEL_PREFIX.match(str(row["text"])):
        letters = [other["level"] for other in page_rows if re.match(r"^[A-E]\s", str(other["text"]))]
        base = min(letters) if letters else 1
        if new_level == base + 1:
            return "body"
    if (row.get("fact_method") or "text_layer") != "text_layer" or new_level != 0:
        return None
    indent = float(row.get("indent") if row.get("indent") is not None else row.get("x_text") or row["x0"])
    for other in page_rows:
        if other.get("level") != 0:
            continue
        other_indent = float(other.get("indent") if other.get("indent") is not None else other.get("x0") or 0)
        if abs(indent - other_indent) <= 4:
            return "indent"
    return None


def _note(kind: str, lines: list[int], reason: str, texts: list[str], **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"kind": kind, "lines": lines, "reason": reason, "text": texts}
    item.update(extra)
    return item


def gate_page(
    rows: list[dict[str, Any]],
    spec: dict[str, Any],
    titles: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Turn one page of review JSON into accepted rows plus rejected proposals.

    The model does not write structure. Text-layer rows stay apart unless the
    merge is a real continuation. Image rows may split when a veto would refuse
    to keep them together. Level changes need numbering, a body title, or indent.
    """
    titles = titles or []
    items = spec.get("rows") if isinstance(spec, dict) else None
    if not isinstance(items, list):
        return [dict(row) for row in rows], [_note("parse", [], "bad_json", [])]
    expect = 1
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            return [dict(row) for row in rows], [_note("parse", [], "bad_json", [])]
        src = item.get("from")
        if (
            not isinstance(src, list)
            or not src
            or not all(isinstance(i, int) and not isinstance(i, bool) for i in src)
            or src != list(range(src[0], src[-1] + 1))
            or src[0] != expect
            or src[-1] > len(rows)
        ):
            return [dict(row) for row in rows], [_note("parse", [], "bad_json", [])]
        expect = src[-1] + 1
        group = [rows[i - 1] for i in src]
        texts = [str(row["text"]) for row in group]
        parts = item.get("parts")
        if parts is not None:
            if len(src) != 1 or not isinstance(parts, list):
                return [dict(row) for row in rows], [_note("parse", [], "bad_json", [])]
            built = _split_group(group[0], parts)
            if built is None:
                rejected.append(_note("split", src, "bad_split", texts))
                accepted.append(dict(group[0]))
                continue
            why = _merge_veto(built, rows + built, titles)
            if why is None:
                rejected.append(_note("split", src, "split_no_veto", [row["text"] for row in built]))
                accepted.append(dict(group[0]))
                continue
            parent_level = int(group[0]["level"])
            for part in built:
                wanted = int(part["level"])
                if wanted == parent_level:
                    continue
                cited = _level_reason(part, wanted, rows, titles)
                detail = bool(layout._LABEL_PREFIX.match(str(part["text"]))) and wanted == parent_level + 1
                if cited or detail:
                    if cited:
                        part["level_reason"] = cited
                    continue
                part["level"] = parent_level
                part["level_from"] = group[0].get("level_from") or "style"
            accepted.extend(built)
            continue
        if not _level_ok(item.get("level")):
            return [dict(row) for row in rows], [_note("parse", [], "bad_json", [])]
        if len(group) > 1:
            veto = _merge_veto(group, rows, titles)
            text_layer = all((row.get("fact_method") or "text_layer") == "text_layer" for row in group)
            block = _text_merge_block(group) if text_layer else None
            if veto or block:
                rejected.append(_note("merge", src, veto or block or "rejected", texts))
                accepted.extend(dict(row) for row in group)
                continue
            merged = _join_group(group, int(group[0]["level"]))
            wanted = int(item["level"])
            if wanted != merged["level"]:
                why = _level_reason(merged, wanted, rows, titles)
                if why:
                    _mark_review(merged, wanted)
                    merged["level_reason"] = why
                else:
                    rejected.append(_note("level", src, "no_citation", texts, level=wanted, draft_level=group[0]["level"]))
            accepted.append(merged)
            continue
        row = dict(group[0])
        wanted = int(item["level"])
        if wanted != row["level"]:
            why = _level_reason(row, wanted, rows, titles)
            if why:
                old = int(row["level"])
                _mark_review(row, wanted)
                row["level_reason"] = why
                group[0]["level"] = wanted
                if wanted < old:
                    indent = float(group[0].get("indent") if group[0].get("indent") is not None else group[0]["x0"])
                    for nxt in rows[src[-1]:]:
                        nxt_indent = float(nxt.get("indent") if nxt.get("indent") is not None else nxt["x0"])
                        if int(nxt["level"]) <= old and nxt_indent <= indent + 4:
                            break
                        nxt["level"] = max(0, int(nxt["level"]) - (old - wanted))
                        nxt["level_draft"] = nxt["level"]
            else:
                rejected.append(_note("level", src, "no_citation", texts, level=wanted, draft_level=row["level"]))
        accepted.append(row)
    if expect != len(rows) + 1:
        return [dict(row) for row in rows], [_note("parse", [], "bad_json", [])]
    return accepted, rejected


def review(
    book: Path,
    rows: list[dict[str, Any]],
    collected: dict[str, Any],
    toc_pages: list[int],
) -> tuple[float, dict[int, list[dict[str, Any]]]]:
    """One 35B call per catalog page. Proposals are accepted or stored, never applied raw."""
    rejected_by_page: dict[int, list[dict[str, Any]]] = {}
    if not rows:
        return 0.0, rejected_by_page
    for row in rows:
        row["level_draft"] = row["level"]
    titles = body_titles(collected, toc_pages, rows)
    groups: dict[int, list[int]] = {}
    for index, row in enumerate(rows):
        groups.setdefault(int(row["pdf_page"]), []).append(index)
    seconds = 0.0
    structure_changed = False
    for idxs in reversed(list(groups.values())):
        page = int(rows[idxs[0]]["pdf_page"])
        if idxs[-1] - idxs[0] + 1 != len(idxs):
            continue
        raw = page_image(book, page)
        if raw is None:
            continue
        draft = [
            {
                "n": number,
                "text": rows[index]["text"],
                "print_page": rows[index]["print_page"],
                "level": rows[index]["level"],
            }
            for number, index in enumerate(idxs, 1)
        ]
        content = [
            {"type": "text", "text": _prompt("printed_toc_review.md")},
            {"type": "text", "text": "草稿条目：\n" + json.dumps(draft, ensure_ascii=False)},
            {"type": "text", "text": "正文标题：\n" + json.dumps(titles, ensure_ascii=False)},
            _image_part(raw),
        ]
        try:
            data, elapsed = _chat(content, 4000)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError):
            rejected_by_page[page] = [_note("parse", [], "vision_failed", [])]
            continue
        seconds += elapsed
        applied, rejected = gate_page([rows[index] for index in idxs], data, titles)
        if rejected:
            rejected_by_page[page] = rejected
        if any(item.get("reason") == "bad_json" for item in rejected):
            continue
        before = [rows[index]["text"] for index in idxs]
        after = [row["text"] for row in applied]
        if before != after:
            structure_changed = True
        rows[idxs[0] : idxs[-1] + 1] = applied
    if structure_changed and rows and all((row.get("fact_method") or "text_layer") == "text_layer" for row in rows):
        cited = [(index, row["level"]) for index, row in enumerate(rows) if row.get("level_reason")]
        layout.assign_levels(rows)
        for index, level in cited:
            rows[index]["level"] = level
        for row in rows:
            if not row.get("level_reason"):
                row["level_draft"] = row["level"]
    return round(seconds, 3), rejected_by_page


def deepen_model_details(rows: list[dict[str, Any]]) -> None:
    """Image rows have no real bold. A line under A–E that is not itself a section is one level down."""
    last: dict[str, Any] | None = None
    for row in rows:
        if row.get("fact_method") != "model_lines":
            last = None
            continue
        text = str(row.get("text") or "")
        if re.match(r"^[A-E]\s", text):
            last = row
            continue
        if re.match(r"^\d+\s", text) or text.startswith("Self-assessment") or text in {"Appendices", "附录"}:
            last = None
            continue
        if last is not None and int(row.get("level") or 0) == int(last.get("level") or 0):
            row["level"] = int(last["level"]) + 1
            row["level_draft"] = row["level"]
            row["level_reason"] = "body"


_PAGE_BAR = re.compile(r"^(?:[A-E]\s|Self-assessment|Exploring literature)")


def mark_neighbor_holes(rows: list[dict[str, Any]]) -> None:
    """A bar with no page becomes a hole when another bar at the same column and level has one.

    Detail lines stay empty. A crop of the blank margin beside them invents a page,
    and that invented page then wipes the real number on the bar above.
    """
    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("fact_method") != "model_lines":
            continue
        groups.setdefault((int(row.get("col") or 0), int(row.get("level") or 0)), []).append(row)
    for group in groups.values():
        if not any(row.get("print_page") is not None for row in group):
            continue
        for row in group:
            if row.get("print_page") is not None or row.get("hole"):
                continue
            if not _PAGE_BAR.match(str(row.get("text") or "")):
                continue
            height = max(8.0, float(row["y1"]) - float(row["y0"]))
            row["hole"] = "neighbor"
            row["hole_box"] = [
                float(row["x1"]) + 4,
                float(row["y0"]) - 4,
                float(row["x1"]) + height * 8,
                float(row["y1"]) + 4,
            ]


def book_skeletons(book: Path, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Order skeleton includes footers. Expect skeleton drops pure chrome and keeps slots."""
    items: list[dict[str, Any]] = []
    for item in _load_json_list(_mineru_file(book, "_content_list.json")):
        if not isinstance(item, dict):
            continue
        page = int(item.get("page_idx", -1)) + 1
        if page <= 0:
            continue
        raw = item.get("list_items") if isinstance(item.get("list_items"), list) else item.get("text")
        lines = _split("\n".join(map(str, raw)) if isinstance(raw, list) else raw)
        chrome = item.get("type") in _SKIP_TYPES or item.get("type") == "page_number"
        for line in lines:
            if len(line) > 80:
                continue
            items.append({"page": page, "text": layout.tidy(line), "chrome": chrome})
    built = skeleton.build_skeletons(items)
    built["expect"] = skeleton.add_seen_slots(built["expect"], rows)
    return built


def apply_skeleton_splits(
    rows: list[dict[str, Any]], sk: dict[str, list[dict[str, Any]]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One image line that contains two skeleton titles is split. Text-layer rows stay."""
    titles = list(sk.get("order") or []) + list(sk.get("expect") or [])
    out: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        if row.get("fact_method") != "model_lines":
            out.append(row)
            continue
        parts = skeleton.split_on_hits(str(row.get("text") or ""), titles)
        if not parts:
            out.append(row)
            continue
        kept = []
        for part in parts:
            head = layout._STARTS_ITEM.match(part) or layout._LABEL_PREFIX.match(part)
            if head or skeleton.title_hits(part, titles):
                kept.append(part)
        if len(kept) < 2:
            rejected.append({"kind": "split", "reason": "split_no_head", "text": [row.get("text")], "lines": []})
            out.append(row)
            continue
        y0, y1 = float(row["y0"]), float(row["y1"])
        span = (y1 - y0) / len(kept)
        for index, part in enumerate(kept):
            item = dict(row)
            item["text"] = part
            item["chars"] = None
            item["y0"] = y0 + span * index
            item["y1"] = y0 + span * (index + 1)
            if index != len(kept) - 1:
                item["print_page"] = None
                item["hole"] = None
                item["hole_box"] = None
                item.pop("filled", None)
            out.append(item)
    return out, rejected


def reread_slot_gaps(
    book: Path, rows: list[dict[str, Any]]
) -> tuple[float, list[dict[str, Any]]]:
    """Missing unit×slot: read that corner once. Still missing → absent_in_print, do not invent."""
    gaps = skeleton.slot_gaps(rows)
    if not gaps:
        return 0.0, []
    seconds = 0.0
    absent: list[dict[str, Any]] = []
    for gap in gaps:
        slot = str(gap["slot"])
        sib = next((row for row in rows if skeleton.norm(slot) in skeleton.norm(str(row.get("text") or ""))), None)
        if sib is None or gap.get("pdf_page") is None:
            absent.append({**gap, "reason": "absent_in_print"})
            continue
        height = max(8.0, float(sib["y1"]) - float(sib["y0"]))
        probe = {
            "pdf_page": gap["pdf_page"],
            "hole_box": [
                float(gap["x0"]),
                float(sib["y0"]) - 6,
                max(float(gap["x1"]), float(sib["x1"])) + height * 6,
                float(sib["y1"]) + 6,
            ],
        }
        raw = _crop(book, None, probe)
        if raw is None:
            absent.append({**gap, "reason": "absent_in_print"})
            continue
        prompt = (
            f"这一小块是目录栏的一角。如果印着「{slot}」，照抄那一行，page 写右边的页码，没有就 null。"
            f"如果没有这几个字，text 写 null。只回 JSON："
            '{"text": null, "page": null}'
        )
        try:
            data, elapsed = _chat(
                [_image_part(raw), {"type": "text", "text": prompt}],
                200,
            )
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError):
            absent.append({**gap, "reason": "absent_in_print"})
            continue
        seconds += elapsed
        text = str(data.get("text") or "").strip()
        if skeleton.norm(slot) not in skeleton.norm(text):
            absent.append({**gap, "reason": "absent_in_print"})
            continue
        level = skeleton.slot_level(rows, slot)
        page = layout._digits(str(data.get("page"))) if data.get("page") not in (None, "") else None
        made = dict(sib)
        made["text"] = text
        made["pdf_page"] = gap["pdf_page"]
        made["col"] = gap.get("col")
        made["x0"] = gap.get("x0")
        made["x1"] = gap.get("x1")
        made["y0"] = sib["y0"]
        made["y1"] = sib["y1"]
        made["print_page"] = page
        made["level"] = level if level is not None else sib.get("level")
        made["level_draft"] = made["level"]
        made["level_reason"] = "slot"
        made["hole"] = None
        made["hole_box"] = None
        made.pop("filled", None)
        anchor = gap.get("y1")
        at = 0
        for index, row in enumerate(rows):
            if row.get("pdf_page") == gap.get("pdf_page") and row.get("y1") == anchor:
                at = index + 1
        rows.insert(at, made)
    return round(seconds, 3), absent


# ---------------------------------------------------------------- 4 fill


def _crop(book: Path, doc: Any, row: dict[str, Any]) -> bytes | None:
    """Hole box in PDF points when ``doc`` is given, else in page-image pixels."""
    from PIL import Image

    x0, y0, x1, y1 = row["hole_box"]
    if doc is not None:
        import fitz

        page = doc[row["pdf_page"] - 1]
        clip = fitz.Rect(x0, y0, x1, y1) & page.rect
        if clip.is_empty:
            return None
        pix = page.get_pixmap(matrix=fitz.Matrix(_CROP_ZOOM, _CROP_ZOOM), clip=clip)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    else:
        raw = page_image(book, row["pdf_page"])
        if raw is None:
            return None
        img = Image.open(io.BytesIO(raw)).crop(tuple(int(v) for v in (x0, y0, x1, y1)))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=90)
    return buf.getvalue()


def fill(book: Path, rows: list[dict[str, Any]], method: str) -> float:
    """One 35B call per page: read the page number in each hole crop."""
    holes: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("hole") and row.get("hole_box") and row.get("print_page") is None:
            holes.setdefault(row["pdf_page"], []).append(row)
    if not holes:
        return 0.0
    seconds = 0.0
    doc = _open_pdf(book)
    try:
        for page, items in holes.items():
            use_pdf = all(
                (row.get("fact_method") or method) == "text_layer" for row in items
            )
            page_doc = doc if use_pdf and doc is not None else None
            content: list[dict[str, Any]] = [{"type": "text", "text": _prompt("printed_toc_fill.md")}]
            shown: list[dict[str, Any]] = []
            for row in items:
                raw = _crop(book, page_doc, row)
                if raw is None:
                    continue
                content.append({"type": "text", "text": f"第 {len(shown) + 1} 块（{row['text'][:30]}）："})
                content.append(_image_part(raw))
                shown.append(row)
            if not shown:
                continue
            data, elapsed = _chat(content, 20 + 8 * len(shown))
            seconds += elapsed
            values = data.get("pages") if isinstance(data.get("pages"), list) else []
            if len(values) != len(shown):
                continue
            for row, value in zip(shown, values):
                number = layout._digits(str(value)) if value is not None else None
                if number is not None:
                    row["print_page"] = number
                    row["filled"] = True
    finally:
        if doc is not None:
            doc.close()
    return round(seconds, 3)


def toc_fill(book_id: str, pages: str = "", root: str = "") -> dict[str, Any]:
    """Assemble, then let the 35B read the missing page numbers from crops. Read only."""
    book = book_dir(book_id, root)
    rows, method, _, _ = _catalog(book, _pages_arg(pages, book))
    seconds = fill(book, rows, method)
    filled = [_public_row(r) for r in rows if r.get("filled") or r["hole"]]
    return {"method": method, "seconds": seconds, "holes": filled}


# ---------------------------------------------------------------- 5 verify


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[\W_]", "", text)


def _core(label: str) -> str:
    title = label.split("/")[0]
    title = _NUMBERING.sub("", title.strip(), count=1)
    title = re.sub(r"[(（](节选|其[一二三四])[)）]", "", title)
    return _plain(title)[:8]


def page_offset(doc: Any, collected: dict[str, Any], after: int) -> int | None:
    """PDF page minus printed page, the most common one over running page numbers."""
    printed: collections.Counter[int] = collections.Counter()
    if doc is not None:
        for page in range(after + 1, min(doc.page_count, after + _VERIFY_PAGES) + 1):
            height = doc[page - 1].rect.height
            for ln in layout.text_layer_lines(doc[page - 1], edge=0):
                middle = (ln["y0"] + ln["y1"]) / 2
                if height * 0.08 < middle < height * 0.92:
                    continue
                if re.fullmatch(r"\d{1,3}", ln["text"]):
                    printed[page - int(ln["text"])] += 1
    mineru: collections.Counter[int] = collections.Counter()
    for page, texts in collected["numbers"].items():
        for text in texts:
            if after < page <= after + _VERIFY_PAGES and re.fullmatch(r"\d{1,3}", text.strip()):
                mineru[page - int(text)] += 1
    for votes in (printed, mineru):
        if votes:
            offset, count = votes.most_common(1)[0]
            if count >= _OFFSET_VOTES:
                return offset
    return None


def _body_text(doc: Any, collected: dict[str, Any], page: int) -> str:
    if doc is not None and 1 <= page <= doc.page_count:
        text = doc[page - 1].get_text("text")
        if text.strip():
            return _plain(text)
    return _plain("".join(collected["text"].get(page, [])))


def missing_in_toc(
    rows: list[dict[str, Any]], collected: dict[str, Any], toc_pages: list[int]
) -> list[dict[str, Any]]:
    """Body 章/节/单元/课 headings that never appear in the printed catalog."""
    have = {_core(row["text"]) for row in rows if len(_core(row["text"])) >= 2}
    after = max(toc_pages or [0])
    missing: list[dict[str, Any]] = []
    for page, lines in collected["text"].items():
        if page <= after:
            continue
        for line in lines[:6]:
            text = layout.tidy(line)
            if "练习" in text or not _HEADING.match(text):
                continue
            key = _core(text)
            if len(key) < 2 or key in have:
                continue
            have.add(key)
            missing.append({"label": text, "pdf_page": page})
    return missing


def verify(
    book: Path, rows: list[dict[str, Any]], collected: dict[str, Any], toc_pages: list[int]
) -> tuple[int | None, list[dict[str, Any]]]:
    doc = _open_pdf(book)
    try:
        offset = page_offset(doc, collected, max(toc_pages or [0]))
        cache: dict[int, str] = {}

        def body(page: int) -> str:
            if page not in cache:
                cache[page] = _body_text(doc, collected, page)
            return cache[page]

        for row in rows:
            key = _core(row["text"])
            if row["print_page"] is None or offset is None or len(key) < 2:
                row["verified"] = "no_page" if row["print_page"] is None else "unchecked"
                continue
            target = row["print_page"] + offset
            if key in body(target):
                row["verified"] = "body"
            elif any(key in body(target + d) for d in (-1, 1, 2)):
                row["verified"] = "body_near"
            else:
                row["verified"] = "not_found"
    finally:
        if doc is not None:
            doc.close()
    return offset, missing_in_toc(rows, collected, toc_pages)


def check_text(rows: list[dict[str, Any]], collected: dict[str, Any]) -> None:
    """Letters in a label that MinerU never read on that page: glyphs the text layer maps wrongly."""
    for row in rows:
        lines = collected["text"].get(row["pdf_page"], []) + collected["index"].get(row["pdf_page"], [])
        seen = set(_plain("".join(lines)))
        if not seen:
            continue
        odd = "".join(ch for ch in dict.fromkeys(_plain(row["text"])) if ch not in seen)
        if not odd:
            continue
        row["text_suspect"] = odd
        best = max(lines, key=lambda ln: difflib.SequenceMatcher(None, row["text"], ln).ratio())
        row["mineru_text"] = best


def toc_verify(book_id: str, root: str = "") -> dict[str, Any]:
    """Check the entries of toc.print against the body pages and MinerU. Read only."""
    book = book_dir(book_id, root)
    doc = json.loads((book / "toc.print").read_text(encoding="utf-8"))
    rows = [dict(e, text=e["label"]) for e in doc.get("entries") or []]
    collected = collect_pages(book)
    offset, missing = verify(book, rows, collected, doc.get("toc_pages") or [])
    check_text(rows, collected)
    counts = collections.Counter(r["verified"] for r in rows)
    odd = [{"label": r["label"], "text_suspect": r["text_suspect"], "mineru_text": r["mineru_text"]}
           for r in rows if r.get("text_suspect")]
    return {
        "page_offset": offset,
        "verified": dict(counts),
        "text_suspect": odd,
        "missing_in_toc": {"count": len(missing), "items": missing},
    }


# ---------------------------------------------------------------- 6 golden


def golden_dir() -> Path:
    env = os.environ.get("PRINTED_TOC_GOLDEN")
    if env:
        return Path(env)
    mounted = Path("/data/agent/eval/printed_toc")
    if mounted.is_dir():
        return mounted
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "agents" / "textbook-agent" / "eval" / "printed_toc"
        if cand.is_dir():
            return cand
        cand = parent / "eval" / "printed_toc"
        if cand.is_dir():
            return cand
    return _home() / "eval" / "printed_toc"


def _gold_key(label: str) -> str:
    return _plain(label).lower()


def compare(gold: list[dict[str, Any]], got: list[dict[str, Any]]) -> dict[str, Any]:
    a = [_gold_key(e["label"]) for e in gold]
    b = [_gold_key(e["label"]) for e in got]
    out: dict[str, list] = {
        "missing": [], "extra": [], "label": [], "level": [], "page": [],
        "over_merge": [], "level_edit": [],
    }
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            pairs = zip(gold[i1:i2], got[j1:j2])
        elif tag == "replace" and i2 - i1 == j2 - j1:
            pairs = zip(gold[i1:i2], got[j1:j2])
            for g, e in zip(gold[i1:i2], got[j1:j2]):
                out["label"].append([g["label"], e["label"]])
        else:
            out["missing"] += [g["label"] for g in gold[i1:i2]]
            out["extra"] += [e["label"] for e in got[j1:j2]]
            continue
        for g, e in pairs:
            if g["level"] != e["level"]:
                out["level"].append([g["label"], g["level"], e["level"]])
            if g.get("print_page") != e.get("print_page"):
                out["page"].append([g["label"], g.get("print_page"), e.get("print_page")])
    got_keys = [_gold_key(e["label"]) for e in got]
    for index in range(len(gold) - 1):
        joined = _gold_key(gold[index]["label"] + gold[index + 1]["label"])
        if len(joined) < 4:
            continue
        for entry, key in zip(got, got_keys):
            if key == joined:
                out["over_merge"].append([gold[index]["label"], gold[index + 1]["label"], entry["label"]])
                break
    for entry in got:
        draft = entry.get("level_draft")
        if isinstance(draft, int) and draft != entry.get("level"):
            out["level_edit"].append([entry["label"], draft, entry.get("level")])
    out["pass"] = not any(out[k] for k in ("missing", "extra", "label", "level", "page", "over_merge"))
    return out


def toc_golden(book_id: str = "", root: str = "") -> dict[str, Any]:
    """Compare toc.print with eval/printed_toc/<book>.json (all frozen books when empty). Read only."""
    files = sorted(golden_dir().glob("*.json"))
    if book_id:
        files = [f for f in files if f.stem.startswith(book_id.strip())]
    report: dict[str, Any] = {}
    for path in files:
        gold = json.loads(path.read_text(encoding="utf-8"))
        try:
            book = book_dir(path.stem, root)
            got = json.loads((book / "toc.print").read_text(encoding="utf-8")).get("entries") or []
        except FileNotFoundError:
            report[path.stem] = {"pass": False, "error": "no toc.print"}
            continue
        report[path.stem] = compare(gold.get("entries") or [], got)
    return {"books": len(report), "passed": sum(1 for r in report.values() if r["pass"]), "report": report}
