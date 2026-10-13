"""Printed-page layout service (PP-DocLayoutV3, CPU only).

Callers send page images. This process never reads the textbook tree.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import threading
import time

import torch
import transformers
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
torch.set_num_threads(int(os.environ.get("LAYOUT_THREADS", "2")))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("layout-svc")

MODEL_PATH = os.environ.get(
    "LAYOUT_MODEL_PATH",
    "/models/hf-cache/models--PaddlePaddle--PP-DocLayoutV3_safetensors/"
    "snapshots/e4f489dc536556fc1be02b973ba06756e0d4a2ac",
)
REVISION = os.environ.get("LAYOUT_REVISION", "e4f489dc536556fc1be02b973ba06756e0d4a2ac")
DEFAULT_THRESHOLD = float(os.environ.get("LAYOUT_THRESHOLD", "0.3"))

app = FastAPI(title="Layout Service", version="1.0")

_lock = threading.Lock()
_model = None
_proc = None
_labels: dict[int, str] = {}
_ready = False
_error = ""


def _versions() -> dict[str, str]:
    return {
        "torch": torch.__version__,
        "transformers": transformers.__version__,
    }


def _load() -> None:
    global _model, _proc, _labels, _ready, _error
    if _ready:
        return
    with _lock:
        if _ready:
            return
        from transformers import AutoImageProcessor, AutoModelForObjectDetection

        started = time.perf_counter()
        logger.info("Loading PP-DocLayoutV3 from %s", MODEL_PATH)
        try:
            _proc = AutoImageProcessor.from_pretrained(MODEL_PATH, local_files_only=True)
            _model = AutoModelForObjectDetection.from_pretrained(
                MODEL_PATH, local_files_only=True
            )
            _model.to("cpu").eval()
            _labels = {int(k): v for k, v in _model.config.id2label.items()}
            _ready = True
            _error = ""
        except Exception as exc:  # noqa: BLE001
            _error = str(exc)
            logger.exception("layout model failed to load")
            raise
        logger.info("layout model ready in %.1fs", time.perf_counter() - started)


@app.on_event("startup")
def startup() -> None:
    _load()


class PageIn(BaseModel):
    page: int
    image_b64: str


class LayoutIn(BaseModel):
    pages: list[PageIn] = Field(default_factory=list)
    threshold: float | None = None


def _regions(image, threshold: float) -> list[dict]:
    from PIL import Image

    if not isinstance(image, Image.Image):
        image = Image.open(image).convert("RGB")
    else:
        image = image.convert("RGB")
    inputs = _proc(images=image, return_tensors="pt")
    with torch.no_grad():
        outputs = _model(**inputs)
    result = _proc.post_process_object_detection(
        outputs, threshold=threshold, target_sizes=[image.size[::-1]]
    )[0]
    scores = result["scores"]
    labels = result["labels"]
    boxes = result["boxes"]
    polygons = result.get("polygon_points")
    orders = result.get("order_seq")
    out: list[dict] = []
    for i, (score, label_id, box) in enumerate(zip(scores, labels, boxes)):
        item = {
            "label": _labels.get(int(label_id), str(int(label_id))),
            "score": round(float(score), 4),
            "box": [round(float(v), 1) for v in box.tolist()],
            "order": int(orders[i]) if orders is not None else i,
        }
        if polygons is not None:
            poly = polygons[i]
            item["polygon"] = [
                [round(float(p[0]), 1), round(float(p[1]), 1)] for p in poly
            ]
        else:
            x0, y0, x1, y1 = item["box"]
            item["polygon"] = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        out.append(item)
    return out


@app.get("/health")
def health() -> dict:
    if not _ready:
        raise HTTPException(status_code=503, detail=_error or "loading")
    return {
        "status": "ok",
        "ready": True,
        "model": "PP-DocLayoutV3",
        "revision": REVISION,
        "device": "cpu",
        **_versions(),
    }


@app.post("/v1/layout/pages")
def detect(body: LayoutIn) -> dict:
    if not _ready:
        raise HTTPException(status_code=503, detail=_error or "loading")
    if not body.pages:
        raise HTTPException(status_code=400, detail="pages required")
    threshold = DEFAULT_THRESHOLD if body.threshold is None else float(body.threshold)
    from PIL import Image

    pages = []
    for item in body.pages:
        try:
            raw = base64.b64decode(item.image_b64)
            image = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"bad image page {item.page}") from exc
        width, height = image.size
        started = time.perf_counter()
        regions = _regions(image, threshold)
        pages.append({
            "page": item.page,
            "width": width,
            "height": height,
            "seconds": round(time.perf_counter() - started, 3),
            "regions": regions,
        })
    return {
        "model": "PP-DocLayoutV3",
        "revision": REVISION,
        "threshold": threshold,
        "device": "cpu",
        **_versions(),
        "pages": pages,
    }
