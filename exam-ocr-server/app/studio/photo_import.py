"""Photo OCR import for Corpus Studio (ops textbook capture)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.buddy_client import corpus_admin_request
from app.image_preprocess import read_normalized_upload_with_meta
from app.storage.minio_client import put_import_bytes
from app.vllm_client import vllm_client

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/import/photo")
async def import_photo(
    file: UploadFile = File(...),
    subject: str = Form("math"),
    grade_band: str = Form("hs"),
    label: str = Form(""),
) -> dict[str, Any]:
    data, mime, meta = await read_normalized_upload_with_meta(file)
    if not data:
        raise HTTPException(status_code=400, detail="Empty image")
    key = f"corpus/imports/photo-{uuid.uuid4().hex}/{file.filename or 'photo.jpg'}"
    put_import_bytes(key, data, content_type=mime or "image/jpeg")

    try:
        img_size = (int(meta["width"]), int(meta["height"]))
        ocr = await vllm_client.analyze_page(data, mime, img_size=img_size)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OCR failed: {e}") from e

    texts = [p.print_text for p in ocr.parts if p.print_text]
    full_text = "\n".join(texts).strip()
    node_id = f"chunk:photo:{subject}:{uuid.uuid4().hex[:8]}"
    body = {
        "id": node_id,
        "node_type": "chunk",
        "subject": subject,
        "grade_band": grade_band,
        "label": label or "拍照导入",
        "summary": full_text[:240],
        "full_text": full_text,
        "tags": [f"grade_band:{grade_band}", "source:photo"],
        "meta": {"minio_key": key, "source_channel": "photo"},
        "status": "draft",
    }
    r = await corpus_admin_request("POST", "/api/v1/admin/corpus/nodes", json=body)
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Buddy node create failed: {r.text}")
    return {"node": r.json(), "minio_key": key, "ocr_parts": len(ocr.parts)}
