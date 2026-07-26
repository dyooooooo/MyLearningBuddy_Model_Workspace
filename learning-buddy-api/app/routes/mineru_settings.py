"""Admin routes for MinerU runtime settings (Studio UI)."""
from __future__ import annotations

from fastapi import APIRouter

from app.schemas.mineru_settings import (
    MinerUSettingsOut,
    MinerUSettingsTestOut,
    MinerUSettingsTestRequest,
    MinerUSettingsUpdate,
)
from app.services import mineru_settings as svc

router = APIRouter(prefix="/api/v1/admin/settings", tags=["admin-settings"])


@router.get("/mineru", response_model=MinerUSettingsOut)
def get_mineru_settings() -> MinerUSettingsOut:
    return svc.get_mineru_settings_out()


@router.put("/mineru", response_model=MinerUSettingsOut)
def put_mineru_settings(body: MinerUSettingsUpdate) -> MinerUSettingsOut:
    return svc.update_mineru_settings(body)


@router.post("/mineru/test", response_model=MinerUSettingsTestOut)
def test_mineru_settings(body: MinerUSettingsTestRequest | None = None) -> MinerUSettingsTestOut:
    req = body or MinerUSettingsTestRequest()
    return svc.test_mineru_settings(
        api_token=req.api_token,
        api_base_url=req.api_base_url,
        model_version=req.model_version,
        language=req.language,
        enable_formula=req.enable_formula,
        enable_table=req.enable_table,
        is_ocr=req.is_ocr,
    )
