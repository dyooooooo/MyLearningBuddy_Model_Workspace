"""MinerU cloud adapter — parses PDFs via the mineru.net v4 cloud API.

HTTP flow (ported from DeepTutor, Apache-2.0, original copyright retained):
  1. POST /api/v4/file-urls/batch  → batch_id + signed upload URL
  2. PUT  PDF bytes                → signed URL  (no auth header)
  3. GET  /api/v4/extract-results/batch/{id}  poll until done/failed
  4. Download full_zip_url archive → extract .md + *_content_list.json + images/
  5. Upload images/ to MinIO and rewrite links in markdown + blocks

This file contains only HTTP client code; the MinerU binary/model is never
installed locally, so no AGPL-3.0 code enters the project.
"""
from __future__ import annotations

# Original HTTP flow ported from:
#   DeepTutor deeptutor/services/parsing/engines/mineru/cloud.py
#   Copyright 2024-2025 HKUDS  (Apache-2.0)
# Adaptation: Learning Buddy project (glue layer, MinIO upload, progress_cb
# mapping).

import io
import json
import logging
import shutil
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.services.mineru_settings import MinerURuntimeConfig, get_effective_mineru_config
from app.services.parsing.types import ParsedDocument, ParserError

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Timing constants
# --------------------------------------------------------------------------
_SUBMIT_TIMEOUT_S = 60.0
_UPLOAD_TIMEOUT_S = 300.0
_DOWNLOAD_TIMEOUT_S = 300.0
_POLL_INTERVAL_S = 4.0

_TERMINAL_OK = "done"
_TERMINAL_FAIL = "failed"

# Zip-bomb guard
_MAX_TOTAL_BYTES = 500 * 1024 * 1024
_MAX_ENTRIES = 5_000


# --------------------------------------------------------------------------
# Adapter
# --------------------------------------------------------------------------

class MinerUCloudAdapter:
    name = "mineru_cloud"

    def is_ready(self) -> bool:
        return bool(get_effective_mineru_config().api_token)

    def parse(
        self,
        pdf_bytes: bytes,
        filename: str,
        progress_cb: Callable[[dict], None] | None = None,
        *,
        page_done_cb: Callable[[int, str, int], None] | None = None,
        asset_prefix: str = "",
        page_limit: int | None = None,
        page_ranges: str | None = None,
    ) -> ParsedDocument:
        cfg = get_effective_mineru_config()
        if not cfg.api_token:
            raise ParserError(
                "MinerU 精准解析 API Token 未配置。"
                "请在 Studio「MinerU 设置」中填写 Token，或改用「35B Vision OCR」。"
            )

        page_ranges = resolve_page_ranges(page_ranges, page_limit)

        def _report(msg: str) -> None:
            if progress_cb:
                try:
                    progress_cb({"ocr_progress": msg, "converter": "mineru_cloud"})
                except Exception:
                    pass

        tmp_dir = Path(tempfile.mkdtemp(prefix="mineru_"))
        try:
            tmp_pdf = tmp_dir / Path(filename).name
            tmp_pdf.write_bytes(pdf_bytes)

            workdir = _run_cloud_api(
                tmp_pdf,
                tmp_dir / "out",
                _report,
                cfg,
                progress_cb=progress_cb,
                page_ranges=page_ranges,
            )
            markdown, blocks, content_source = _read_workdir(workdir)
            blocks = remap_relative_page_idx(blocks, page_ranges)
            raw_keys = _persist_workdir_artifacts(workdir, asset_prefix)
            markdown, blocks = _rewrite_images(
                workdir, markdown, blocks, filename, _report, asset_prefix=asset_prefix
            )

            if page_done_cb:
                from app.services.corpus_import_incremental import (
                    group_mineru_blocks_by_page,
                    mineru_blocks_to_page_markdown,
                )

                if blocks:
                    grouped = group_mineru_blocks_by_page(blocks)
                    total = len(grouped) or _count_pages(blocks) or 1
                    for idx, page_no in enumerate(sorted(grouped.keys()), start=1):
                        page_md = mineru_blocks_to_page_markdown(grouped[page_no])
                        if page_md.strip():
                            page_done_cb(page_no, page_md, total)
                        if progress_cb:
                            try:
                                progress_cb(
                                    {
                                        "ocr_progress": f"{idx}/{total}",
                                        "ocr_page": idx,
                                        "ocr_total": total,
                                        "converter": "mineru_cloud",
                                    }
                                )
                            except Exception:
                                pass
                elif markdown.strip():
                    page_done_cb(1, markdown.strip(), 1)

            page_count = _count_pages(blocks)
            meta: dict[str, Any] = {
                "converter": "mineru_cloud",
                "filename": filename,
                "page_count": page_count,
                "total_pages": page_count,
                "ocr_total": page_count,
                "char_count": len(markdown),
                "pdf_kind": "mineru",
                "content_list_source": content_source,
                "page_ranges": page_ranges,
                "page_limit": page_limit,
                "mineru_raw_keys": raw_keys,
            }
            logger.info(
                "mineru_cloud adapter done filename=%s chars=%d blocks=%s "
                "source=%s page_ranges=%s",
                filename,
                len(markdown),
                len(blocks) if blocks else 0,
                content_source,
                page_ranges,
            )
            return ParsedDocument(
                markdown=markdown,
                blocks=blocks,
                engine="mineru_cloud",
                meta=meta,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# --------------------------------------------------------------------------
# Core HTTP flow (ported from DeepTutor cloud.py)
# --------------------------------------------------------------------------

def _page_limit_to_ranges(page_limit: int | None) -> str | None:
    """Map Studio page_limit (first N pages) to Cloud ``page_ranges`` string.

    Official format (mineru.net): comma-separated ranges, e.g. ``"1-3"``, ``"2,4-6"``.
    """
    if page_limit is None:
        return None
    try:
        n = int(page_limit)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    return f"1-{n}"


def resolve_page_ranges(
    page_ranges: str | None = None,
    page_limit: int | None = None,
) -> str | None:
    """Prefer explicit ``page_ranges``; else map ``page_limit`` → ``1-N``."""
    pr = (page_ranges or "").strip()
    if pr:
        return pr
    return _page_limit_to_ranges(page_limit)


def page_ranges_start(page_ranges: str | None) -> int | None:
    """First 1-based page in a Cloud page_ranges string, or None."""
    pr = (page_ranges or "").strip()
    if not pr:
        return None
    first = pr.split(",")[0].strip()
    try:
        if "-" in first:
            return int(first.split("-", 1)[0])
        return int(first)
    except ValueError:
        return None


def remap_relative_page_idx(
    blocks: list[dict[str, Any]] | None,
    page_ranges: str | None,
) -> list[dict[str, Any]] | None:
    """If Cloud returned relative page_idx (0..n-1) for a mid-book range, shift to absolute.

    Absolute already (min page_idx >= start-1) → unchanged.
    """
    if not blocks or not page_ranges:
        return blocks
    start = page_ranges_start(page_ranges)
    if start is None or start < 1:
        return blocks
    idxs: list[int] = []
    for b in blocks:
        try:
            idxs.append(int(b.get("page_idx", 0)))
        except (TypeError, ValueError):
            idxs.append(0)
    if not idxs:
        return blocks
    min_idx = min(idxs)
    # Already absolute 0-based from document start
    if min_idx >= start - 1:
        return blocks
    offset = (start - 1) - min_idx
    out: list[dict[str, Any]] = []
    for b in blocks:
        nb = dict(b)
        try:
            nb["page_idx"] = int(nb.get("page_idx", 0)) + offset
        except (TypeError, ValueError):
            nb["page_idx"] = offset
        out.append(nb)
    return out


def _run_cloud_api(
    pdf_path: Path,
    output_base: Path,
    report: Callable[[str], None],
    cfg: MinerURuntimeConfig,
    *,
    progress_cb: Callable[[dict], None] | None = None,
    page_ranges: str | None = None,
) -> Path:
    """Upload, process, download and extract via MinerU cloud v4 API.

    Returns the extracted working directory (contains .md, content_list JSON,
    layout.json ≈ middle.json, images/).
    """
    base_url = cfg.api_base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {cfg.api_token}",
        "Accept": "application/json",
    }

    with httpx.Client(base_url=base_url, headers=headers) as client:
        range_note = f" pages={page_ranges}" if page_ranges else ""
        report(f"MinerU cloud: 申请上传槽 {pdf_path.name}{range_note}")
        batch_id, upload_url = _request_upload(
            client, pdf_path, cfg, page_ranges=page_ranges
        )

        size_mb = pdf_path.stat().st_size / (1024 * 1024)
        report(f"MinerU cloud: 上传 {pdf_path.name} ({size_mb:.1f} MB)")
        _upload_file(pdf_path, upload_url)

        zip_url = _poll_for_zip(
            client,
            batch_id,
            pdf_path.name,
            report=report,
            timeout_s=cfg.timeout_s,
            progress_cb=progress_cb,
        )
        report("MinerU cloud: 下载解析结果压缩包")
        archive_bytes = _download(zip_url)

    report("MinerU cloud: 解压结果")
    workdir = output_base / pdf_path.stem
    _reset_dir(workdir)
    _extract_archive(archive_bytes, workdir)
    return workdir


def _request_upload(
    client: httpx.Client,
    pdf_path: Path,
    cfg: MinerURuntimeConfig,
    *,
    page_ranges: str | None = None,
) -> tuple[str, str]:
    file_entry: dict[str, Any] = {"name": pdf_path.name, "is_ocr": cfg.is_ocr}
    # Official Cloud API: page_ranges lives on each file object
    # (POST /api/v4/file-urls/batch). See https://mineru.net/apiManage/docs
    if page_ranges:
        file_entry["page_ranges"] = page_ranges

    body: dict[str, Any] = {
        "files": [file_entry],
        "model_version": cfg.model_version,
        "enable_formula": cfg.enable_formula,
        "enable_table": cfg.enable_table,
    }
    if cfg.language:
        body["language"] = cfg.language

    payload = _post_json(client, "/api/v4/file-urls/batch", body)
    data = payload.get("data") or {}
    batch_id = str(data.get("batch_id") or "").strip()
    file_urls = data.get("file_urls") or []
    if not batch_id or not isinstance(file_urls, list) or not file_urls:
        raise ParserError(
            "MinerU API 未返回上传 URL（缺少 batch_id/file_urls）。"
        )
    return batch_id, str(file_urls[0])


def _upload_file(pdf_path: Path, upload_url: str) -> None:
    data = pdf_path.read_bytes()
    try:
        response = httpx.put(
            upload_url, content=data, timeout=_UPLOAD_TIMEOUT_S
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ParserError(f"上传 PDF 到 MinerU 失败: {exc}") from exc


def _poll_for_zip(
    client: httpx.Client,
    batch_id: str,
    file_name: str,
    *,
    report: Callable[[str], None],
    timeout_s: float,
    progress_cb: Callable[[dict], None] | None = None,
) -> str:
    deadline = time.monotonic() + timeout_s
    last_state = ""
    last_report = ""

    while True:
        payload = _get_json(client, f"/api/v4/extract-results/batch/{batch_id}")
        results = (payload.get("data") or {}).get("extract_result") or []
        entry = _match_entry(results, file_name)

        if entry is not None:
            state = str(entry.get("state") or "").strip().lower()
            last_state = state or last_state
            progress = entry.get("extract_progress") or {}
            total_pages = progress.get("total_pages")
            msg = f"MinerU cloud: {state or 'queued'}"
            if total_pages:
                extracted = progress.get("extracted_pages") or 0
                msg += f" ({extracted}/{total_pages} 页)"
                if progress_cb:
                    try:
                        progress_cb(
                            {
                                "ocr_progress": f"{extracted}/{total_pages}",
                                "ocr_page": int(extracted),
                                "ocr_total": int(total_pages),
                                "converter": "mineru_cloud",
                            }
                        )
                    except Exception:
                        pass
            if msg != last_report:
                last_report = msg
                report(msg)

            if state == _TERMINAL_OK:
                zip_url = str(entry.get("full_zip_url") or "").strip()
                if not zip_url:
                    raise ParserError(
                        "MinerU 报告完成但未返回 full_zip_url。"
                    )
                return zip_url
            if state == _TERMINAL_FAIL:
                err = str(entry.get("err_msg") or "未知错误")
                raise ParserError(f"MinerU 解析失败: {err}")

        if time.monotonic() >= deadline:
            raise ParserError(
                f"MinerU 解析超时（{int(timeout_s)}s，"
                f"最后状态: {last_state or 'unknown'}）。"
                "可在 Studio「MinerU 设置」调大超时秒数。"
            )
        time.sleep(_POLL_INTERVAL_S)


def _download(zip_url: str) -> bytes:
    try:
        response = httpx.get(
            zip_url,
            timeout=_DOWNLOAD_TIMEOUT_S,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.content
    except httpx.HTTPError as exc:
        raise ParserError(f"下载 MinerU 结果压缩包失败: {exc}") from exc


# --------------------------------------------------------------------------
# Workdir reading
# --------------------------------------------------------------------------

def _flatten_content_list_v2(pages: list[Any]) -> list[dict]:
    """Flatten page-grouped content_list_v2 into v1-compatible flat blocks.

    Official v2 shape (MinerU ≥3.0): ``[[page0_blocks...], [page1_blocks...]]``
    where each block is ``{type, content, bbox}``. We inject ``page_idx`` so
    downstream IR / page grouping keep working.
    """
    flat: list[dict] = []
    for page_idx, page_blocks in enumerate(pages):
        if not isinstance(page_blocks, list):
            continue
        for item in page_blocks:
            if not isinstance(item, dict):
                continue
            block = dict(item)
            block.setdefault("page_idx", page_idx)
            flat.append(block)
    return flat


def _load_content_list(workdir: Path) -> tuple[list[dict] | None, str]:
    """Prefer ``*_content_list_v2.json``, fall back to legacy content_list.

    Returns ``(blocks, source)`` where source is ``v2`` / ``v1`` / ``none``.
    """
    v2_files = sorted(workdir.rglob("*_content_list_v2.json"))
    for path in v2_files:
        try:
            parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            logger.warning("mineru_cloud: failed to parse %s: %s", path.name, exc)
            continue
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], list):
            return _flatten_content_list_v2(parsed), "v2"
        if isinstance(parsed, list) and all(isinstance(x, dict) for x in parsed):
            # Some cloud builds may already emit a flat list under the v2 name.
            return [dict(x) for x in parsed], "v2_flat"

    v1_files = sorted(workdir.rglob("*_content_list.json"))
    # Exclude accidental matches of *_content_list_v2.json already handled above.
    v1_files = [p for p in v1_files if not p.name.endswith("_content_list_v2.json")]
    for path in v1_files:
        try:
            parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            logger.warning("mineru_cloud: failed to parse %s: %s", path.name, exc)
            continue
        if isinstance(parsed, list):
            return [x for x in parsed if isinstance(x, dict)], "v1"

    return None, "none"


def _read_workdir(workdir: Path) -> tuple[str, list[dict] | None, str]:
    """Read .md + structured content list from the extracted archive dir."""
    md_files = sorted(workdir.rglob("*.md"))
    # Prefer full.md when present (official zip layout).
    preferred = [p for p in md_files if p.name.lower() == "full.md"]
    if not preferred and not md_files:
        raise ParserError(
            f"MinerU 压缩包解压后未找到 .md 文件（目录: {workdir}）。"
        )
    md_path = preferred[0] if preferred else md_files[0]
    markdown = md_path.read_text(encoding="utf-8", errors="replace")

    blocks, source = _load_content_list(workdir)
    return markdown, blocks, source


def _persist_workdir_artifacts(
    workdir: Path,
    asset_prefix: str,
) -> dict[str, str]:
    """Upload key MinerU JSON artifacts under corpus/imports/.../mineru/ for debug.

    Official zip (non-HTML): content_list / content_list_v2 / layout.json
    (≈ middle.json) / model.json. See opendatalab.github.io/MinerU/reference/output_files/
    and mineru.net zip notes.
    """
    if not asset_prefix:
        return {}

    from app.services.import_assets import upload_import_asset

    patterns = (
        ("content_list_v2", "*_content_list_v2.json"),
        ("content_list", "*_content_list.json"),
        ("layout", "layout.json"),
        ("layout", "*_layout.json"),
        ("middle", "*_middle.json"),
        ("model", "*_model.json"),
        ("model", "model.json"),
    )
    keys: dict[str, str] = {}
    storage = f"{asset_prefix.rstrip('/')}/mineru"
    for label, pattern in patterns:
        if label in keys:
            continue
        matches = sorted(workdir.rglob(pattern))
        # Avoid treating v2 as v1 when globbing *_content_list.json
        if label == "content_list":
            matches = [
                p for p in matches if not p.name.endswith("_content_list_v2.json")
            ]
        if not matches:
            continue
        src = matches[0]
        try:
            minio_key = f"{storage}/{src.name}"
            upload_import_asset(
                minio_key,
                src.read_bytes(),
                content_type="application/json",
            )
            keys[label] = minio_key
        except Exception as exc:
            logger.warning(
                "mineru_cloud: failed to persist %s (%s): %s",
                label,
                src.name,
                exc,
            )
    return keys


# --------------------------------------------------------------------------
# Image upload + link rewriting
# --------------------------------------------------------------------------

def _rewrite_images(
    workdir: Path,
    markdown: str,
    blocks: list[dict] | None,
    filename: str,
    report: Callable[[str], None],
    *,
    asset_prefix: str = "",
) -> tuple[str, list[dict] | None]:
    """Upload images/ to MinIO and rewrite local paths to Studio proxy URLs."""
    images_dir = workdir / "images"
    if not images_dir.is_dir():
        return markdown, blocks

    image_files = sorted(images_dir.iterdir())
    if not image_files:
        return markdown, blocks

    from app.services.import_assets import (
        rewrite_local_image_refs,
        upload_import_asset,
    )

    stem = Path(filename).stem
    base_prefix = asset_prefix or f"corpus/import/{stem}/img"
    if asset_prefix:
        img_storage_prefix = f"{asset_prefix.rstrip('/')}/mineru"
    else:
        img_storage_prefix = base_prefix

    url_map: dict[str, str] = {}
    uploaded = 0

    for img_path in image_files:
        if not img_path.is_file():
            continue
        minio_key = f"{img_storage_prefix}/{img_path.name}"
        try:
            img_bytes = img_path.read_bytes()
            content_type = _image_content_type(img_path.name)
            public_url = upload_import_asset(minio_key, img_bytes, content_type=content_type)
            url_map[f"images/{img_path.name}"] = public_url
            uploaded += 1
        except Exception as exc:
            logger.warning(
                "mineru_cloud: failed to upload image %s: %s",
                img_path.name,
                exc,
            )

    if not url_map:
        return markdown, blocks

    report(f"MinerU cloud: 已上传 {uploaded} 张插图到 MinIO")

    markdown = rewrite_local_image_refs(markdown, url_map)

    if blocks:
        import copy

        blocks = copy.deepcopy(blocks)
        blocks = [_rewrite_block(b, url_map) for b in blocks]

    return markdown, blocks


def _rewrite_block(block: dict, url_map: dict[str, str]) -> dict:
    def _map_path(val: str) -> str | None:
        if not isinstance(val, str) or not val.strip():
            return None
        if val.startswith(("/", "http")):
            return None
        normalized = val.replace("\\", "/")
        for local, remote in url_map.items():
            if not local.startswith("images/"):
                continue
            if normalized == local or normalized.endswith("/" + local):
                return remote
        return None

    out = dict(block)
    for key in ("img_path", "image_path", "src"):
        mapped = _map_path(str(out.get(key) or ""))
        if mapped:
            out[key] = mapped

    content = out.get("content")
    if isinstance(content, dict):
        cd = dict(content)
        changed = False
        for key in ("img_path", "image_path", "src", "path"):
            mapped = _map_path(str(cd.get(key) or ""))
            if mapped:
                cd[key] = mapped
                changed = True
                out.setdefault("img_path", mapped)
        # content_list_v2: content.image_source.path = "images/….jpg"
        image_source = cd.get("image_source")
        if isinstance(image_source, dict):
            isrc = dict(image_source)
            isrc_changed = False
            for key in ("path", "img_path", "image_path", "src"):
                mapped = _map_path(str(isrc.get(key) or ""))
                if mapped:
                    isrc[key] = mapped
                    isrc_changed = True
                    out["img_path"] = mapped
            if isrc_changed:
                cd["image_source"] = isrc
                changed = True
        elif isinstance(image_source, str):
            mapped = _map_path(image_source)
            if mapped:
                cd["image_source"] = mapped
                out["img_path"] = mapped
                changed = True
        if changed:
            out["content"] = cd
    return out


def _image_content_type(name: str) -> str:
    ext = Path(name).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")


def _count_pages(blocks: list[dict] | None) -> int:
    if not blocks:
        return 0
    pages = {b.get("page_idx") for b in blocks if isinstance(b.get("page_idx"), int)}
    return len(pages) or len(blocks)


# --------------------------------------------------------------------------
# HTTP helpers (ported from DeepTutor cloud.py — Apache-2.0)
# --------------------------------------------------------------------------

def _match_entry(results: list, file_name: str) -> dict | None:
    rows = [r for r in results if isinstance(r, dict)]
    if not rows:
        return None
    for row in rows:
        if str(row.get("file_name") or "") == file_name:
            return row
    return rows[0]


def _post_json(client: httpx.Client, path: str, body: dict) -> dict:
    try:
        response = client.post(path, json=body, timeout=_SUBMIT_TIMEOUT_S)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise ParserError(_http_error_message(exc)) from exc
    except httpx.HTTPError as exc:
        raise ParserError(f"MinerU API 请求失败: {exc}") from exc
    _check_code(payload)
    return payload


def _get_json(client: httpx.Client, path: str) -> dict:
    try:
        response = client.get(path, timeout=_SUBMIT_TIMEOUT_S)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise ParserError(_http_error_message(exc)) from exc
    except httpx.HTTPError as exc:
        raise ParserError(f"MinerU API 请求失败: {exc}") from exc
    _check_code(payload)
    return payload


def _check_code(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ParserError("MinerU API 返回了非预期（非 JSON）响应。")
    code = payload.get("code")
    if code not in (0, None):
        msg = str(payload.get("msg") or "未知错误")
        raise ParserError(f"MinerU API 错误（code {code}）: {msg}")


def _http_error_message(exc: httpx.HTTPStatusError) -> str:
    status = exc.response.status_code
    if status in (401, 403):
        return "MinerU API token 被拒绝（401/403）。请在 Studio「MinerU 设置」更新 Token。"
    if status == 429:
        return "MinerU API 限流（429）。请稍后重试或减少并发请求。"
    return f"MinerU API 返回 HTTP {status}。"


def _reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _extract_archive(archive_bytes: bytes, target_dir: Path) -> None:
    """Extract MinerU zip with Zip Slip and bomb protection."""
    target_root = target_dir.resolve()
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            if len(members) > _MAX_ENTRIES:
                raise ParserError(
                    f"MinerU 压缩包条目过多（{len(members)}）。"
                )
            for member in members:
                rel = Path(member.filename.replace("\\", "/"))
                if rel.is_absolute() or ".." in rel.parts:
                    logger.warning("跳过不安全 zip 条目: %s", member.filename)
                    continue
                dest = (target_root / rel).resolve()
                if target_root not in dest.parents and dest != target_root:
                    logger.warning(
                        "跳过逃逸根目录的 zip 条目: %s", member.filename
                    )
                    continue
                total += member.file_size
                if total > _MAX_TOTAL_BYTES:
                    raise ParserError("MinerU 压缩包超过大小限制（500 MB）。")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, open(dest, "wb") as out:
                    out.write(src.read())
    except zipfile.BadZipFile as exc:
        raise ParserError(f"MinerU 压缩包损坏: {exc}") from exc
