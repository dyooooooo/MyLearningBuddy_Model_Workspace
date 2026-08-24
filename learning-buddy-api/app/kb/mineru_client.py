"""MinerU Cloud v4 client used only by the knowledge-base line."""

from __future__ import annotations

import io
import json
import logging
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.kb.assets import content_type_for_name, rewrite_local_image_paths, upload_bytes
from app.kb.settings import KbMineruRuntime, require_export_config

logger = logging.getLogger(__name__)

_SUBMIT_S = 60.0
_UPLOAD_S = 300.0
_DOWNLOAD_S = 300.0
_POLL_S = 4.0
_MAX_ZIP_BYTES = 500 * 1024 * 1024
_MAX_ZIP_FILES = 5_000


class KbMineruError(RuntimeError):
    pass


@dataclass(frozen=True)
class KbParseResult:
    markdown: str
    blocks: list[dict[str, Any]]


def parse_pdf(
    pdf_bytes: bytes,
    *,
    filename: str,
    book_id: str,
    cfg: KbMineruRuntime | None = None,
) -> KbParseResult:
    runtime = cfg or require_export_config()
    if runtime.model_version not in ("vlm", "pipeline"):
        raise KbMineruError("知识库拒绝 HTML 导出引擎")
    tmp = Path(tempfile.mkdtemp(prefix="kb-mineru-"))
    try:
        pdf_path = tmp / Path(filename or "book.pdf").name
        pdf_path.write_bytes(pdf_bytes)
        workdir = _run_cloud(pdf_path, tmp / "out", runtime)
        markdown, blocks = _read_workdir(workdir)
        markdown, blocks = _upload_figures(workdir, markdown, blocks, book_id)
        return KbParseResult(markdown=markdown, blocks=blocks)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_cloud(pdf_path: Path, output_base: Path, cfg: KbMineruRuntime) -> Path:
    headers = {
        "Authorization": f"Bearer {cfg.api_token}",
        "Accept": "application/json",
    }
    with httpx.Client(base_url=cfg.api_base_url.rstrip("/"), headers=headers) as client:
        batch_id, upload_url = _request_slot(client, pdf_path, cfg)
        _put_pdf(pdf_path, upload_url)
        zip_url = _wait_zip(client, batch_id, pdf_path.name, cfg.timeout_s)
        archive = _download(zip_url)
    workdir = output_base / pdf_path.stem
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    _safe_extract(archive, workdir)
    return workdir


def _request_slot(client: httpx.Client, pdf_path: Path, cfg: KbMineruRuntime) -> tuple[str, str]:
    body: dict[str, Any] = {
        "files": [{"name": pdf_path.name, "is_ocr": cfg.is_ocr}],
        "model_version": cfg.model_version,
        "enable_formula": cfg.enable_formula,
        "enable_table": cfg.enable_table,
    }
    if cfg.language:
        body["language"] = cfg.language
    payload = _json_post(client, "/api/v4/file-urls/batch", body)
    data = payload.get("data") or {}
    batch_id = str(data.get("batch_id") or "").strip()
    urls = data.get("file_urls") or []
    if not batch_id or not urls:
        raise KbMineruError("MinerU 未返回 batch_id / file_urls")
    return batch_id, str(urls[0])


def _put_pdf(pdf_path: Path, upload_url: str) -> None:
    try:
        response = httpx.put(upload_url, content=pdf_path.read_bytes(), timeout=_UPLOAD_S)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise KbMineruError(f"上传 PDF 失败: {exc}") from exc


def _wait_zip(client: httpx.Client, batch_id: str, file_name: str, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    last = ""
    while True:
        payload = _json_get(client, f"/api/v4/extract-results/batch/{batch_id}")
        rows = [r for r in ((payload.get("data") or {}).get("extract_result") or []) if isinstance(r, dict)]
        entry = next((r for r in rows if str(r.get("file_name") or "") == file_name), None)
        if entry is None and rows:
            entry = rows[0]
        if entry is not None:
            state = str(entry.get("state") or "").strip().lower()
            last = state or last
            if state == "done":
                zip_url = str(entry.get("full_zip_url") or "").strip()
                if not zip_url:
                    raise KbMineruError("MinerU 完成但没有 full_zip_url")
                return zip_url
            if state == "failed":
                raise KbMineruError(str(entry.get("err_msg") or "MinerU 解析失败"))
        if time.monotonic() >= deadline:
            raise KbMineruError(f"MinerU 解析超时（{int(timeout_s)}s，状态 {last or 'unknown'}）")
        time.sleep(_POLL_S)


def _download(zip_url: str) -> bytes:
    try:
        response = httpx.get(zip_url, timeout=_DOWNLOAD_S, follow_redirects=True)
        response.raise_for_status()
        return response.content
    except httpx.HTTPError as exc:
        raise KbMineruError(f"下载 MinerU 压缩包失败: {exc}") from exc


def _safe_extract(archive_bytes: bytes, target: Path) -> None:
    root = target.resolve()
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            if len(members) > _MAX_ZIP_FILES:
                raise KbMineruError("MinerU 压缩包条目过多")
            for member in members:
                rel = Path(member.filename.replace("\\", "/"))
                if rel.is_absolute() or ".." in rel.parts:
                    continue
                dest = (root / rel).resolve()
                if root not in dest.parents and dest != root:
                    continue
                total += member.file_size
                if total > _MAX_ZIP_BYTES:
                    raise KbMineruError("MinerU 压缩包超过 500MB")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as out:
                    out.write(src.read())
    except zipfile.BadZipFile as exc:
        raise KbMineruError(f"MinerU 压缩包损坏: {exc}") from exc


def _flatten_v2(pages: list[Any]) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
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


def _load_blocks(workdir: Path) -> list[dict[str, Any]]:
    for path in sorted(workdir.rglob("*_content_list_v2.json")):
        try:
            parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], list):
            return _flatten_v2(parsed)
        if isinstance(parsed, list) and all(isinstance(x, dict) for x in parsed):
            return [dict(x) for x in parsed]
    for path in sorted(workdir.rglob("*_content_list.json")):
        if path.name.endswith("_content_list_v2.json"):
            continue
        try:
            parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if isinstance(parsed, list):
            return [x for x in parsed if isinstance(x, dict)]
    return []


def _read_workdir(workdir: Path) -> tuple[str, list[dict[str, Any]]]:
    md_files = sorted(workdir.rglob("*.md"))
    preferred = [p for p in md_files if p.name.lower() == "full.md"]
    markdown = ""
    if preferred or md_files:
        markdown = (preferred[0] if preferred else md_files[0]).read_text(
            encoding="utf-8", errors="replace"
        )
    return markdown, _load_blocks(workdir)


def _map_image_path(val: str, url_map: dict[str, str]) -> str | None:
    if not isinstance(val, str) or not val.strip():
        return None
    if val.startswith(("/", "http")):
        return None
    normalized = val.replace("\\", "/")
    for local, remote in url_map.items():
        if normalized == local or normalized.endswith("/" + local):
            return remote
    return None


def _rewrite_block(block: dict[str, Any], url_map: dict[str, str]) -> dict[str, Any]:
    out = dict(block)
    for key in ("img_path", "image_path", "src"):
        mapped = _map_image_path(str(out.get(key) or ""), url_map)
        if mapped:
            out[key] = mapped
    for key in ("table_body", "html", "table_html"):
        val = out.get(key)
        if isinstance(val, str) and val:
            mapped = rewrite_local_image_paths(val, url_map)
            if mapped != val:
                out[key] = mapped
    content = out.get("content")
    if not isinstance(content, dict):
        return out
    cd = dict(content)
    changed = False
    for key in ("img_path", "image_path", "src", "path"):
        mapped = _map_image_path(str(cd.get(key) or ""), url_map)
        if mapped:
            cd[key] = mapped
            out.setdefault("img_path", mapped)
            changed = True
    image_source = cd.get("image_source")
    if isinstance(image_source, dict):
        isrc = dict(image_source)
        for key in ("path", "img_path", "image_path", "src"):
            mapped = _map_image_path(str(isrc.get(key) or ""), url_map)
            if mapped:
                isrc[key] = mapped
                out["img_path"] = mapped
                changed = True
        if changed:
            cd["image_source"] = isrc
    elif isinstance(image_source, str):
        mapped = _map_image_path(image_source, url_map)
        if mapped:
            cd["image_source"] = mapped
            out["img_path"] = mapped
            changed = True
    for key in ("table_body", "html", "table_html"):
        val = cd.get(key)
        if isinstance(val, str) and val:
            mapped = rewrite_local_image_paths(val, url_map)
            if mapped != val:
                cd[key] = mapped
                changed = True
    if changed:
        out["content"] = cd
    return out


def _upload_figures(
    workdir: Path,
    markdown: str,
    blocks: list[dict[str, Any]],
    book_id: str,
) -> tuple[str, list[dict[str, Any]]]:
    images_dir = workdir / "images"
    if not images_dir.is_dir():
        nested = next((p for p in workdir.rglob("images") if p.is_dir()), None)
        images_dir = nested or images_dir
    if not images_dir.is_dir():
        return markdown, blocks
    url_map: dict[str, str] = {}
    for img_path in sorted(images_dir.iterdir()):
        if not img_path.is_file():
            continue
        key = f"kb/pdfs/{book_id}/assets/mineru/{img_path.name}"
        try:
            url = upload_bytes(
                key,
                img_path.read_bytes(),
                content_type=content_type_for_name(img_path.name),
            )
            url_map[f"images/{img_path.name}"] = url
        except Exception as exc:
            logger.warning("kb figure upload failed name=%s: %s", img_path.name, exc)
    if not url_map:
        return markdown, blocks
    rewritten = [_rewrite_block(b, url_map) for b in blocks]
    return rewrite_local_image_paths(markdown, url_map), rewritten


def _json_post(client: httpx.Client, path: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        response = client.post(path, json=body, timeout=_SUBMIT_S)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise KbMineruError("MinerU Token 被拒绝") from exc
        raise KbMineruError(f"MinerU API HTTP {status}") from exc
    except httpx.HTTPError as exc:
        raise KbMineruError(f"MinerU API 请求失败: {exc}") from exc
    _check_code(payload)
    return payload


def _json_get(client: httpx.Client, path: str) -> dict[str, Any]:
    try:
        response = client.get(path, timeout=_SUBMIT_S)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise KbMineruError(f"MinerU API HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise KbMineruError(f"MinerU API 请求失败: {exc}") from exc
    _check_code(payload)
    return payload


def _check_code(payload: object) -> None:
    if not isinstance(payload, dict):
        raise KbMineruError("MinerU API 返回非 JSON")
    code = payload.get("code")
    if code not in (0, None):
        raise KbMineruError(str(payload.get("msg") or f"MinerU code {code}"))
