"""ParseJobConfig — per-job parse settings, stored in job meta["parse_config"].

Hard-break: ``edition_id`` (alias textbook_profile) is required at create time.
Worker side always calls ``ParseJobConfig.from_meta(meta)`` to reconstruct.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator


class ParseJobConfig(BaseModel):
    engine: str = "mineru_cloud"
    # Hard-break: must be an explicit edition bundle id (no silent default at API).
    edition_id: Annotated[str, Field(min_length=1)] = Field(
        ...,
        description="Edition bundle id (e.g. hujiao_math_hs_v1)",
    )
    # Back-compat alias written alongside edition_id in meta.
    textbook_profile: str = ""
    page_limit: Annotated[int, Field(ge=1)] | None = None
    page_ranges: str | None = Field(
        default=None,
        description='MinerU Cloud page_ranges，如 "1-20"；优先于 page_limit',
    )

    @model_validator(mode="before")
    @classmethod
    def _alias_profile(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        eid = str(data.get("edition_id") or data.get("textbook_profile") or "").strip()
        if eid:
            data = {**data, "edition_id": eid, "textbook_profile": eid}
        pr = data.get("page_ranges")
        if pr is not None:
            data = {**data, "page_ranges": str(pr).strip() or None}
        return data

    @field_validator("edition_id")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("edition_id is required (hard-break: no implicit textbook default)")
        return s

    @model_validator(mode="after")
    def _check_engine(self) -> "ParseJobConfig":
        from app.services.parsing.registry import VALID_ENGINE_IDS

        if self.engine not in VALID_ENGINE_IDS:
            raise ValueError(f"unknown parse engine: {self.engine!r}")
        if not self.textbook_profile:
            self.textbook_profile = self.edition_id
        return self

    @classmethod
    def from_meta(cls, meta: dict) -> "ParseJobConfig":
        """Reconstruct from job meta JSON (supports nested + legacy flat)."""
        raw = meta.get("parse_config")
        if isinstance(raw, dict):
            payload = dict(raw)
        else:
            payload = {
                "engine": str(meta.get("parse_engine") or "mineru_cloud"),
            }
            if meta.get("page_limit"):
                try:
                    payload["page_limit"] = int(meta["page_limit"])
                except (TypeError, ValueError):
                    pass
            if meta.get("page_ranges"):
                payload["page_ranges"] = str(meta["page_ranges"]).strip() or None
        # Flat mirrors written by persist / migration
        if meta.get("page_ranges") and not payload.get("page_ranges"):
            payload["page_ranges"] = str(meta["page_ranges"]).strip() or None
        eid = (
            str(payload.get("edition_id") or "").strip()
            or str(payload.get("textbook_profile") or "").strip()
            or str(meta.get("edition_id") or "").strip()
            or str(meta.get("textbook_profile") or "").strip()
        )
        if not eid:
            raise ValueError(
                "edition_id missing on job (run scripts/migrate_backfill_edition.py)"
            )
        payload["edition_id"] = eid
        payload["textbook_profile"] = eid
        return cls.model_validate(payload)


__all__ = ["ParseJobConfig"]
