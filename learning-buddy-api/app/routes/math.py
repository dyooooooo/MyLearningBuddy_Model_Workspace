from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.math.normalize import normalize_plain
from app.schemas.common import RichText

router = APIRouter(prefix="/api/v1/math", tags=["math"])


class NormalizeTextRequest(BaseModel):
    text: str = ""


class NormalizePartIn(BaseModel):
    print: str = ""
    handwriting: str = ""


class NormalizePartsRequest(BaseModel):
    parts: list[NormalizePartIn] = Field(default_factory=list)


class NormalizePartOut(BaseModel):
    print_rich: RichText
    handwriting_rich: RichText


class NormalizePartsResponse(BaseModel):
    parts: list[NormalizePartOut] = Field(default_factory=list)


@router.post("/normalize", response_model=RichText)
def normalize_text(body: NormalizeTextRequest) -> RichText:
    return normalize_plain(body.text or "")


@router.post("/normalize-parts", response_model=NormalizePartsResponse)
def normalize_parts(body: NormalizePartsRequest) -> NormalizePartsResponse:
    out: list[NormalizePartOut] = []
    for part in body.parts:
        out.append(
            NormalizePartOut(
                print_rich=normalize_plain(part.print or ""),
                handwriting_rich=normalize_plain(part.handwriting or ""),
            )
        )
    return NormalizePartsResponse(parts=out)
