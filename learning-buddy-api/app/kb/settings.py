"""Knowledge-base MinerU Cloud settings — own file, not studio mineru.json."""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

_SETTINGS_FILE = "kb_mineru.json"
ALLOWED_MODELS = ("pipeline", "vlm")
TEXTBOOK_PRESET: dict[str, Any] = {
    "api_base_url": "https://mineru.net",
    "model_version": "vlm",
    "language": "ch",
    "enable_formula": True,
    "enable_table": True,
    "is_ocr": False,
    "timeout_s": 600.0,
}


class KbMineruPublic(BaseModel):
    api_base_url: str = "https://mineru.net"
    model_version: str = "vlm"
    language: str = "ch"
    enable_formula: bool = True
    enable_table: bool = True
    is_ocr: bool = False
    timeout_s: float = Field(default=600.0, ge=60.0, le=3600.0)
    updated_at: str = ""


class KbMineruOut(BaseModel):
    settings: KbMineruPublic
    api_token_set: bool = False
    api_token: str = ""


class KbMineruUpdate(BaseModel):
    api_token: str | None = None
    api_base_url: str | None = None
    model_version: str | None = None
    language: str | None = None
    enable_formula: bool | None = None
    enable_table: bool | None = None
    is_ocr: bool | None = None
    timeout_s: float | None = Field(default=None, ge=60.0, le=3600.0)
    apply_textbook_preset: bool = False


class KbMineruTestRequest(BaseModel):
    api_token: str | None = None
    api_base_url: str | None = None
    model_version: str | None = None
    language: str | None = None
    enable_formula: bool | None = None
    enable_table: bool | None = None
    is_ocr: bool | None = None


class KbMineruTestOut(BaseModel):
    ok: bool
    message: str = ""


@dataclass(frozen=True)
class KbMineruRuntime:
    api_token: str
    api_base_url: str
    model_version: str
    language: str
    enable_formula: bool
    enable_table: bool
    is_ocr: bool
    timeout_s: float


class KbMineruConfigError(ValueError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _settings_path() -> Path:
    return Path(settings.buddy_data_dir) / "settings" / _SETTINGS_FILE


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    merged = {**TEXTBOOK_PRESET, **raw}
    mv = str(merged.get("model_version") or "vlm").strip()
    if mv == "MinerU-HTML" or mv not in ALLOWED_MODELS:
        mv = "vlm"
    return {
        "api_token": str(merged.get("api_token") or "").strip(),
        "api_base_url": str(merged.get("api_base_url") or "https://mineru.net").rstrip("/"),
        "model_version": mv,
        "language": str(merged.get("language") or "ch").strip() or "ch",
        "enable_formula": bool(merged.get("enable_formula", True)),
        "enable_table": bool(merged.get("enable_table", True)),
        "is_ocr": bool(merged.get("is_ocr", False)),
        "timeout_s": max(60.0, min(3600.0, float(merged.get("timeout_s") or 600.0))),
        "updated_at": str(merged.get("updated_at") or ""),
    }


def _load_raw() -> dict[str, Any]:
    path = _settings_path()
    if not path.is_file():
        return _normalize({})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _normalize({})
        return _normalize(data)
    except Exception as exc:
        logger.warning("kb mineru settings load failed: %s", exc)
        return _normalize({})


def _save_raw(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(payload)
    normalized["updated_at"] = _now_iso()
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(normalized, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".kb-mineru-", suffix=".json")
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        Path(tmp_name).replace(path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return normalized


def get_runtime() -> KbMineruRuntime:
    raw = _load_raw()
    return KbMineruRuntime(
        api_token=raw["api_token"],
        api_base_url=raw["api_base_url"],
        model_version=raw["model_version"],
        language=raw["language"],
        enable_formula=raw["enable_formula"],
        enable_table=raw["enable_table"],
        is_ocr=raw["is_ocr"],
        timeout_s=raw["timeout_s"],
    )


def require_export_config() -> KbMineruRuntime:
    cfg = get_runtime()
    if not cfg.api_token:
        raise KbMineruConfigError("知识库 MinerU Token 未配置")
    if cfg.model_version not in ALLOWED_MODELS:
        raise KbMineruConfigError("知识库仅支持 vlm / pipeline，不支持 HTML 导出")
    return cfg


def get_settings_out() -> KbMineruOut:
    raw = _load_raw()
    return KbMineruOut(
        settings=KbMineruPublic(
            api_base_url=raw["api_base_url"],
            model_version=raw["model_version"],
            language=raw["language"],
            enable_formula=raw["enable_formula"],
            enable_table=raw["enable_table"],
            is_ocr=raw["is_ocr"],
            timeout_s=raw["timeout_s"],
            updated_at=raw.get("updated_at") or "",
        ),
        api_token_set=bool(raw["api_token"]),
        api_token=raw["api_token"],
    )


def update_settings(body: KbMineruUpdate) -> KbMineruOut:
    raw = _load_raw()
    if body.apply_textbook_preset:
        token = raw.get("api_token") or ""
        raw = _normalize({**TEXTBOOK_PRESET, "api_token": token})
    patch = body.model_dump(exclude_unset=True)
    patch.pop("apply_textbook_preset", None)
    token = patch.pop("api_token", None)
    if token is not None:
        token = token.strip()
        if token:
            raw["api_token"] = token
    if patch.get("model_version") == "MinerU-HTML":
        raise KbMineruConfigError("知识库拒绝 MinerU-HTML，请用 vlm")
    for key, val in patch.items():
        if val is not None:
            raw[key] = val
    _save_raw(raw)
    return get_settings_out()


def test_settings(req: KbMineruTestRequest | None = None) -> KbMineruTestOut:
    saved = get_runtime()
    body = req or KbMineruTestRequest()
    token = (body.api_token or saved.api_token).strip()
    if not token:
        return KbMineruTestOut(ok=False, message="未配置 MinerU API Token")
    model = body.model_version or saved.model_version
    if model == "MinerU-HTML":
        return KbMineruTestOut(ok=False, message="知识库拒绝 MinerU-HTML")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    payload: dict[str, Any] = {
        "files": [{"name": "kb-connectivity.pdf", "is_ocr": saved.is_ocr if body.is_ocr is None else body.is_ocr}],
        "model_version": model,
        "enable_formula": saved.enable_formula if body.enable_formula is None else body.enable_formula,
        "enable_table": saved.enable_table if body.enable_table is None else body.enable_table,
    }
    language = body.language if body.language is not None else saved.language
    if language:
        payload["language"] = language
    base = (body.api_base_url or saved.api_base_url).rstrip("/")
    try:
        with httpx.Client(base_url=base, headers=headers, timeout=60.0) as client:
            response = client.post("/api/v4/file-urls/batch", json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            return KbMineruTestOut(ok=False, message="Token 被拒绝（401/403）")
        return KbMineruTestOut(ok=False, message=f"MinerU API 返回 HTTP {status}")
    except httpx.HTTPError as exc:
        return KbMineruTestOut(ok=False, message=f"无法连接 MinerU API：{exc}")
    if not isinstance(data, dict):
        return KbMineruTestOut(ok=False, message="MinerU API 返回非 JSON")
    code = data.get("code")
    if code not in (0, None):
        return KbMineruTestOut(ok=False, message=str(data.get("msg") or f"code {code}"))
    return KbMineruTestOut(ok=True, message="Token 有效，精准解析 API v4 连通")
