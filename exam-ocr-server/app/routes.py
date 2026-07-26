import logging
from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import Response

from app.clients.redis_client import redis_ok
from app.config import settings
from app.image_preprocess import read_normalized_upload_with_meta
from app.schemas import HealthResponse, PageAnalyzeResult
from app.storage.minio_client import minio_ok
from app.vllm_client import vllm_client

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/v1/system/health", response_model=HealthResponse)
async def system_health() -> HealthResponse:
    reachable = await vllm_client.ping()
    return HealthResponse(
        vllm_base_url=settings.vllm_base_url,
        vllm_model=settings.vllm_model,
        vllm_reachable=reachable,
        redis_ok=redis_ok(),
        minio_ok=minio_ok(),
        openai_lan_proxy=bool(
            settings.vllm_lan_proxy_enabled and (settings.vllm_lan_token or "").strip()
        ),
        openai_lan_proxy_base="/v1",
    )


@router.post("/api/v1/ocr/page", response_model=PageAnalyzeResult)
async def ocr_page(
    request: Request,
    file: Annotated[UploadFile, File(...)],
) -> PageAnalyzeResult:
    """Analyze one exam page: per-question print (top) + handwriting (bottom)."""
    already = request.headers.get("x-already-normalized", "").lower() in ("1", "true", "yes")
    data, mime, meta = await read_normalized_upload_with_meta(
        file, already_normalized=already
    )
    img_size = (int(meta["width"]), int(meta["height"]))
    return await vllm_client.analyze_page(data, mime, img_size=img_size)


@router.post("/api/v1/image/normalize")
async def normalize_image_upload(file: Annotated[UploadFile, File(...)]) -> Response:
    """Convert any supported upload to normalized JPEG (same pipeline as OCR). Full resolution up to max edge."""
    data, mime, meta = await read_normalized_upload_with_meta(file)
    return Response(
        content=data,
        media_type=mime,
        headers={
            "X-Source-Format": str(meta.get("source_format", "UNKNOWN")),
            "X-Image-Width": str(meta.get("width", 0)),
            "X-Image-Height": str(meta.get("height", 0)),
            "X-Bytes-In": str(meta.get("bytes_in", 0)),
            "X-Bytes-Out": str(meta.get("bytes_out", 0)),
        },
    )
