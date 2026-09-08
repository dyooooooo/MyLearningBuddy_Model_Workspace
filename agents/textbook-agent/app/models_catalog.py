from __future__ import annotations

import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_EFFORT_VALUES = (
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
    ("xhigh", "Extra High"),
)
_FAST_VALUES = (("false", ""), ("true", "Fast"))

FALLBACK_MODELS: list[dict[str, Any]] = [
    {
        "id": "default",
        "label": "Auto",
        "group": "auto",
        "parameters": [],
        "variants": [{"display_name": "Auto", "is_default": True, "params": []}],
    },
    {
        "id": "grok-4.6",
        "label": "Cursor Grok 4.6",
        "group": "cursor",
        "parameters": [
            {"id": "effort", "display_name": "Effort", "values": [{"value": a, "display_name": b} for a, b in _EFFORT_VALUES]},
            {"id": "fast", "display_name": "Fast", "values": [{"value": a, "display_name": b} for a, b in _FAST_VALUES]},
        ],
        "variants": [
            {
                "display_name": "Cursor Grok 4.6",
                "is_default": True,
                "params": [{"id": "effort", "value": "high"}, {"id": "fast", "value": "true"}],
            }
        ],
    },
    {
        "id": "composer-2.5",
        "label": "Composer 2.5",
        "group": "cursor",
        "parameters": [
            {"id": "fast", "display_name": "Fast", "values": [{"value": a, "display_name": b} for a, b in _FAST_VALUES]},
        ],
        "variants": [
            {
                "display_name": "Composer 2.5",
                "is_default": True,
                "params": [{"id": "fast", "value": "true"}],
            }
        ],
    },
    {
        "id": "grok-4.5",
        "label": "Cursor Grok 4.5",
        "group": "cursor",
        "parameters": [
            {"id": "effort", "display_name": "Effort", "values": [{"value": a, "display_name": b} for a, b in _EFFORT_VALUES]},
            {"id": "fast", "display_name": "Fast", "values": [{"value": a, "display_name": b} for a, b in _FAST_VALUES]},
        ],
        "variants": [
            {
                "display_name": "Cursor Grok 4.5",
                "is_default": True,
                "params": [{"id": "effort", "value": "high"}, {"id": "fast", "value": "true"}],
            }
        ],
    },
    {
        "id": "composer-2",
        "label": "Composer 2",
        "group": "cursor",
        "parameters": [
            {"id": "fast", "display_name": "Fast", "values": [{"value": a, "display_name": b} for a, b in _FAST_VALUES]},
        ],
        "variants": [
            {"display_name": "Composer 2", "is_default": True, "params": [{"id": "fast", "value": "true"}]}
        ],
    },
]


def list_cursor_models() -> list[dict[str, Any]]:
    rows = _sdk_models()
    return rows or [dict(row) for row in FALLBACK_MODELS]


def _sdk_models() -> list[dict[str, Any]]:
    key = (settings.cursor_api_key or "").strip()
    if not key:
        return []
    try:
        from cursor_sdk import Cursor

        items = Cursor.models.list()
    except Exception as exc:
        logger.warning("Cursor.models.list failed: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items or []:
        row = public_model(item)
        mid = str(row.get("id") or "")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(row)
    return out


def public_model(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        mid = str(item.get("id") or "").strip()
        label = str(item.get("label") or item.get("display_name") or item.get("displayName") or mid)
        return {
            "id": mid,
            "label": label,
            "group": str(item.get("group") or group_of(mid, label)),
            "parameters": [_public_param(p) for p in (item.get("parameters") or [])],
            "variants": [_public_variant(v) for v in (item.get("variants") or [])],
        }
    mid = _field(item, "id")
    label = _field(item, "display_name") or _field(item, "displayName") or _field(item, "name") or mid
    return {
        "id": mid,
        "label": label,
        "group": group_of(mid, label),
        "parameters": [_public_param(p) for p in (getattr(item, "parameters", None) or ())],
        "variants": [_public_variant(v) for v in (getattr(item, "variants", None) or ())],
    }


def group_of(model_id: str, label: str = "") -> str:
    mid = (model_id or "").strip().lower()
    name = (label or "").strip().lower()
    if mid in {"default", "auto"} or name == "auto":
        return "auto"
    if mid.startswith("grok") or mid.startswith("composer") or name.startswith("cursor ") or name.startswith("composer"):
        return "cursor"
    return "other"


def _public_param(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        values = raw.get("values") or []
        return {
            "id": str(raw.get("id") or ""),
            "display_name": str(raw.get("display_name") or raw.get("displayName") or raw.get("id") or ""),
            "values": [_public_value(v) for v in values],
        }
    return {
        "id": _field(raw, "id"),
        "display_name": _field(raw, "display_name") or _field(raw, "displayName") or _field(raw, "id"),
        "values": [_public_value(v) for v in (getattr(raw, "values", None) or ())],
    }


def _public_variant(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        params = raw.get("params") or []
        return {
            "display_name": str(raw.get("display_name") or raw.get("displayName") or ""),
            "is_default": bool(raw.get("is_default") if raw.get("is_default") is not None else raw.get("isDefault")),
            "params": [_public_pair(p) for p in params],
        }
    return {
        "display_name": _field(raw, "display_name") or _field(raw, "displayName"),
        "is_default": bool(getattr(raw, "is_default", False) or getattr(raw, "isDefault", False)),
        "params": [_public_pair(p) for p in (getattr(raw, "params", None) or ())],
    }


def _public_value(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {
            "value": str(raw.get("value") or ""),
            "display_name": str(raw.get("display_name") or raw.get("displayName") or "").replace("\u200b", ""),
        }
    return {
        "value": _field(raw, "value"),
        "display_name": (_field(raw, "display_name") or _field(raw, "displayName")).replace("\u200b", ""),
    }


def _public_pair(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {"id": str(raw.get("id") or ""), "value": str(raw.get("value") or "")}
    return {"id": _field(raw, "id"), "value": _field(raw, "value")}


def _field(item: Any, name: str) -> str:
    if isinstance(item, dict):
        return str(item.get(name) or "").strip()
    return str(getattr(item, name, "") or "").strip()
