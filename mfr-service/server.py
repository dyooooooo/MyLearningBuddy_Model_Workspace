"""Math-Formula-Recognition (MFR) micro-service.

A tiny, self-contained HTTP wrapper around ``breezedeus/pix2text-mfr`` (a
VisionEncoderDecoder model shipped as ONNX weights) that turns an equation
image crop into LaTeX.  It is the side model consumed by
``learning-buddy-api``'s ``app.import_35b.mfr.MFRClient`` when ``MFR_ENABLED``
is set.

Contract (must match ``MFRClient.recognize``):
    POST /mfr   multipart  field ``image`` (png/jpg)  [+ optional form ``backend``]
        -> 200 {"latex": "...", "backend": "pix2text-mfr"}
    GET  /health -> {"status": "ok", "model": "...", "ready": true}

The model runs on CPU via onnxruntime; equation volume per page is small so
this is fast enough and keeps the GPU free for the 35B VLM.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import numpy as np
import torch
from fastapi import FastAPI, File, Form, UploadFile
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mfr")

MODEL_NAME = os.environ.get("MFR_MODEL", "breezedeus/pix2text-mfr")
IMG_SIZE = int(os.environ.get("MFR_IMG_SIZE", "384"))
MAX_NEW_TOKENS = int(os.environ.get("MFR_MAX_NEW_TOKENS", "512"))
NUM_BEAMS = int(os.environ.get("MFR_NUM_BEAMS", "1"))
# DeiT/TrOCR normalisation baked into pix2text-mfr's preprocessor_config.json.
IMG_MEAN = 0.5
IMG_STD = 0.5

app = FastAPI(title="MFR Service", version="1.0")

_lock = threading.Lock()
_model = None
_tokenizer = None
_ready = False


def _load() -> None:
    """Load tokenizer + ONNX model once (idempotent)."""
    global _model, _tokenizer, _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        from optimum.onnxruntime import ORTModelForVision2Seq
        from transformers import AutoTokenizer

        t0 = time.time()
        logger.info("Loading MFR model %s ...", MODEL_NAME)
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        _model = ORTModelForVision2Seq.from_pretrained(
            MODEL_NAME, use_cache=False, use_io_binding=False
        )
        _ready = True
        logger.info("MFR model ready in %.1fs", time.time() - t0)


def _preprocess(img: Image.Image) -> torch.Tensor:
    img = img.convert("RGB").resize((IMG_SIZE, IMG_SIZE), Image.BICUBIC)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr = (arr - IMG_MEAN) / IMG_STD
    arr = np.transpose(arr, (2, 0, 1))[None]  # NCHW
    return torch.from_numpy(arr)


def _recognize(image_bytes: bytes) -> str:
    import io

    _load()
    img = Image.open(io.BytesIO(image_bytes))
    pixel_values = _preprocess(img)
    # onnxruntime session is not re-entrant with io-binding disabled; serialise.
    with _lock:
        generated = _model.generate(
            pixel_values=pixel_values,
            max_new_tokens=MAX_NEW_TOKENS,
            num_beams=NUM_BEAMS,
        )
    text = _tokenizer.batch_decode(generated, skip_special_tokens=True)[0]
    return text.strip()


@app.on_event("startup")
def _startup() -> None:
    try:
        _load()
    except Exception:  # noqa: BLE001 — surfaced via /health, container stays up
        logger.exception("MFR model failed to load at startup; will retry lazily")


@app.get("/health")
def health() -> dict:
    return {"status": "ok" if _ready else "loading", "model": MODEL_NAME, "ready": _ready}


@app.post("/mfr")
async def mfr(image: UploadFile = File(...), backend: str = Form(default="pix2text")) -> dict:
    raw = await image.read()
    try:
        latex = _recognize(raw)
    except Exception as exc:  # noqa: BLE001 — client falls back to 35B latex
        logger.warning("MFR recognition failed: %s", exc)
        return {"latex": "", "error": str(exc), "backend": "pix2text-mfr"}
    return {"latex": latex, "backend": "pix2text-mfr"}
