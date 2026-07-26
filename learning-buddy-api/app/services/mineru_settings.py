"""Runtime MinerU settings — JSON file under buddy_data_dir/settings/.

Token and API parameters are managed from the Studio UI instead of .env so
operators can rotate MinerU credentials without redeploying containers.
Environment variables remain as optional bootstrap fallbacks only.
"""
from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.schemas.mineru_settings import (
    MinerUSettingsOut,
    MinerUSettingsPublic,
    MinerUSettingsTestOut,
    MinerUSettingsUpdate,
)

logger = logging.getLogger(__name__)

_SETTINGS_DIR = "settings"
_SETTINGS_FILE = "mineru.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _settings_path() -> Path:
    return Path(settings.buddy_data_dir) / _SETTINGS_DIR / _SETTINGS_FILE


def _defaults_from_env() -> dict[str, Any]:
    return {
        "api_token": (settings.mineru_api_token or "").strip(),
        "api_base_url": (settings.mineru_api_base_url or "https://mineru.net").strip(),
        "model_version": settings.mineru_model_version or "vlm",
        "language": settings.mineru_language or "ch",
        "enable_formula": bool(settings.mineru_enable_formula),
        "enable_table": bool(settings.mineru_enable_table),
        "is_ocr": bool(settings.mineru_is_ocr),
        "timeout_s": float(settings.mineru_timeout_s or 600.0),
        "updated_at": "",
    }


def _normalize_payload(raw: dict[str, Any]) -> dict[str, Any]:
    base = _defaults_from_env()
    merged = {**base, **raw}
    mv = str(merged.get("model_version") or "vlm").strip()
    if mv not in ("pipeline", "vlm", "MinerU-HTML"):
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
        return _normalize_payload({})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _normalize_payload({})
        return _normalize_payload(data)
    except Exception as exc:
        logger.warning("mineru settings load failed path=%s: %s", path, exc)
        return _normalize_payload({})


def _save_raw(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_payload(payload)
    normalized["updated_at"] = _now_iso()
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(normalized, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".mineru-", suffix=".json")
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        Path(tmp_name).replace(path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    logger.info("mineru settings saved path=%s token_set=%s", path, bool(normalized["api_token"]))
    return normalized


def _to_public(raw: dict[str, Any]) -> MinerUSettingsPublic:
    return MinerUSettingsPublic(
        api_base_url=raw["api_base_url"],
        model_version=raw["model_version"],
        language=raw["language"],
        enable_formula=raw["enable_formula"],
        enable_table=raw["enable_table"],
        is_ocr=raw["is_ocr"],
        timeout_s=raw["timeout_s"],
        updated_at=raw.get("updated_at") or "",
    )


@dataclass(frozen=True)
class MinerURuntimeConfig:
    api_token: str
    api_base_url: str
    model_version: str
    language: str
    enable_formula: bool
    enable_table: bool
    is_ocr: bool
    timeout_s: float


def get_effective_mineru_config() -> MinerURuntimeConfig:
    """Resolved config for the MinerU cloud adapter (file overrides env)."""
    raw = _load_raw()
    return MinerURuntimeConfig(
        api_token=raw["api_token"],
        api_base_url=raw["api_base_url"],
        model_version=raw["model_version"],
        language=raw["language"],
        enable_formula=raw["enable_formula"],
        enable_table=raw["enable_table"],
        is_ocr=raw["is_ocr"],
        timeout_s=raw["timeout_s"],
    )


def get_mineru_settings_out() -> MinerUSettingsOut:
    raw = _load_raw()
    return MinerUSettingsOut(
        settings=_to_public(raw),
        api_token_set=bool(raw["api_token"]),
        api_token=raw["api_token"],
    )


def update_mineru_settings(body: MinerUSettingsUpdate) -> MinerUSettingsOut:
    raw = _load_raw()
    patch = body.model_dump(exclude_unset=True)
    token = patch.pop("api_token", None)
    if token is not None:
        token = token.strip()
        if token:
            raw["api_token"] = token
    for key, val in patch.items():
        if val is not None:
            raw[key] = val
    _save_raw(raw)
    return get_mineru_settings_out()


def _verify_token(cfg: MinerURuntimeConfig) -> MinerUSettingsTestOut:
    if not cfg.api_token:
        return MinerUSettingsTestOut(ok=False, message="未配置 MinerU API Token")
    headers = {
        "Authorization": f"Bearer {cfg.api_token}",
        "Accept": "application/json",
    }
    body: dict[str, Any] = {
        "files": [{"name": "connectivity-check.pdf", "is_ocr": cfg.is_ocr}],
        "model_version": cfg.model_version,
        "enable_formula": cfg.enable_formula,
        "enable_table": cfg.enable_table,
    }
    if cfg.language:
        body["language"] = cfg.language
    base_url = cfg.api_base_url.rstrip("/")
    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=60.0) as client:
            response = client.post("/api/v4/file-urls/batch", json=body)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            return MinerUSettingsTestOut(
                ok=False,
                message="Token 被拒绝（401/403），请在 MinerU API 管理页重新创建 Token",
            )
        return MinerUSettingsTestOut(ok=False, message=f"MinerU API 返回 HTTP {status}")
    except httpx.HTTPError as exc:
        return MinerUSettingsTestOut(ok=False, message=f"无法连接 MinerU API：{exc}")

    if not isinstance(payload, dict):
        return MinerUSettingsTestOut(ok=False, message="MinerU API 返回非 JSON 响应")
    code = payload.get("code")
    if code not in (0, None):
        msg = str(payload.get("msg") or "未知错误")
        return MinerUSettingsTestOut(ok=False, message=f"MinerU API 错误（code {code}）：{msg}")
    return MinerUSettingsTestOut(ok=True, message="Token 有效，精准解析 API v4 连通正常")


def test_mineru_settings(
    *,
    api_token: str | None = None,
    api_base_url: str | None = None,
    model_version: str | None = None,
    language: str | None = None,
    enable_formula: bool | None = None,
    enable_table: bool | None = None,
    is_ocr: bool | None = None,
) -> MinerUSettingsTestOut:
    saved = get_effective_mineru_config()
    cfg = MinerURuntimeConfig(
        api_token=(api_token or saved.api_token).strip(),
        api_base_url=(api_base_url or saved.api_base_url).rstrip("/"),
        model_version=model_version or saved.model_version,
        language=language if language is not None else saved.language,
        enable_formula=saved.enable_formula if enable_formula is None else enable_formula,
        enable_table=saved.enable_table if enable_table is None else enable_table,
        is_ocr=saved.is_ocr if is_ocr is None else is_ocr,
        timeout_s=saved.timeout_s,
    )
    return _verify_token(cfg)
