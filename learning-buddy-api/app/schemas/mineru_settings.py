"""Schemas for MinerU runtime settings (Studio UI)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MinerUModelVersion = Literal["pipeline", "vlm", "MinerU-HTML"]
MinerULanguage = Literal["ch", "ch_server", "en", "korean", "japan"]


class MinerUSettingsPublic(BaseModel):
    """Settings returned to the UI — never includes the raw token."""

    api_base_url: str = "https://mineru.net"
    model_version: MinerUModelVersion = "vlm"
    language: MinerULanguage = "ch"
    enable_formula: bool = True
    enable_table: bool = True
    is_ocr: bool = False
    timeout_s: float = Field(default=600.0, ge=60.0, le=3600.0)
    updated_at: str = ""


class MinerUSettingsOut(BaseModel):
    settings: MinerUSettingsPublic
    api_token_set: bool = False
    # Admin-only Studio UI: echo saved token so the field repopulates on reopen.
    api_token: str = ""


class MinerUSettingsUpdate(BaseModel):
    """Write-only token: omit or empty string keeps the saved token."""

    api_token: str | None = None
    api_base_url: str | None = None
    model_version: MinerUModelVersion | None = None
    language: MinerULanguage | None = None
    enable_formula: bool | None = None
    enable_table: bool | None = None
    is_ocr: bool | None = None
    timeout_s: float | None = Field(default=None, ge=60.0, le=3600.0)


class MinerUSettingsTestRequest(BaseModel):
    """Optional overrides for connectivity test before save."""

    api_token: str | None = None
    api_base_url: str | None = None
    model_version: MinerUModelVersion | None = None
    language: MinerULanguage | None = None
    enable_formula: bool | None = None
    enable_table: bool | None = None
    is_ocr: bool | None = None


class MinerUSettingsTestOut(BaseModel):
    ok: bool
    message: str = ""
