import base64
import json
import logging
import re
from typing import Any

import httpx

from app.config import settings
from app.image_utils import jpeg_size
from app.prompts import PAGE_ANALYZE_PROMPT
from app.schemas import PageAnalyzeResult, PagePart

logger = logging.getLogger(__name__)


def _image_size(image_bytes: bytes) -> tuple[int, int]:
    return jpeg_size(image_bytes)


def _norm_bbox(bbox: list[Any], img_w: int, img_h: int) -> list[float]:
    if not bbox or len(bbox) < 4 or img_w <= 0 or img_h <= 0:
        return []
    x1, y1, x2, y2 = (float(bbox[i]) for i in range(4))
    if max(abs(x1), abs(y1), abs(x2), abs(y2)) > 1.0:
        return [x1 / img_w, y1 / img_h, x2 / img_w, y2 / img_h]
    return [x1, y1, x2, y2]


def _pick_bbox(obj: dict[str, Any]) -> list[Any]:
    for key in ("bbox", "bbox_2d", "box", "bounding_box", "bbox_print"):
        if key in obj and obj[key]:
            return list(obj[key])
    return []


def _pick_text(obj: dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = obj.get(key)
        if val is not None and str(val).strip():
            return str(val)
    return ""


def _normalize_kind(raw: str) -> str:
    s = raw.lower().strip()
    if s in ("header", "title", "page_header"):
        return "header"
    if s in ("section", "section_title"):
        return "section"
    return "question"


def _part_from_dict(item: dict[str, Any], default_kind: str = "question", default_id: Any = None) -> dict[str, Any] | None:
    kind = _normalize_kind(str(item.get("kind", default_kind)))
    print_text = _pick_text(item, "print", "print_text", "stem", "question_content", "text")
    handwriting = _pick_text(
        item, "handwriting", "handwriting_text", "answer", "answer_text", "handwritten"
    )
    bbox_print = item.get("bbox_print") or item.get("print_bbox") or _pick_bbox(item)
    bbox_hw = item.get("bbox_handwriting") or item.get("handwriting_bbox") or item.get("answer_area")
    if not print_text and not handwriting:
        return None
    part_id = item.get("id", default_id)
    if kind != "question":
        part_id = None
    return {
        "kind": kind,
        "id": part_id,
        "print": print_text,
        "handwriting": handwriting if kind == "question" else "",
        "bbox_print": list(bbox_print) if bbox_print else [],
        "bbox_handwriting": list(bbox_hw) if bbox_hw and kind == "question" else [],
    }


def _normalize_parts(raw: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    if isinstance(raw.get("parts"), list):
        for i, item in enumerate(raw["parts"]):
            if not isinstance(item, dict):
                continue
            part = _part_from_dict(item, default_id=i + 1)
            if part:
                out.append(part)
        if out:
            return out

    header = raw.get("header")
    if isinstance(header, dict):
        part = _part_from_dict({**header, "kind": "header"})
        if part:
            out.append(part)
    elif isinstance(header, str) and header.strip():
        out.append({"kind": "header", "id": None, "print": header.strip(), "handwriting": "", "bbox_print": [], "bbox_handwriting": []})

    for item in raw.get("sections", []) if isinstance(raw.get("sections"), list) else []:
        if isinstance(item, dict):
            part = _part_from_dict({**item, "kind": "section"})
            if part:
                out.append(part)
        elif isinstance(item, str) and item.strip():
            out.append({"kind": "section", "id": None, "print": item.strip(), "handwriting": "", "bbox_print": [], "bbox_handwriting": []})

    for i, item in enumerate(raw.get("questions", []) if isinstance(raw.get("questions"), list) else []):
        if not isinstance(item, dict):
            continue
        part = _part_from_dict({**item, "kind": "question"}, default_id=i + 1)
        if part:
            out.append(part)

    return out


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object in model output")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("Model JSON root is not an object")
    return obj


class VllmVisionClient:
    def __init__(self) -> None:
        self.base_url = settings.vllm_base_url.rstrip("/")
        self.model = settings.vllm_model
        self.timeout = settings.request_timeout_s
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def ping(self) -> bool:
        try:
            client = self._get_client()
            resp = await client.get(f"{self.base_url}/models", timeout=10.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def _vision_completion(
        self,
        image_bytes: bytes,
        prompt: str,
        mime: str = "image/jpeg",
        max_tokens: int = 4096,
    ) -> str:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                }
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        client = self._get_client()
        resp = await client.post(f"{self.base_url}/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def analyze_page(
        self,
        image_bytes: bytes,
        mime: str = "image/jpeg",
        *,
        img_size: tuple[int, int] | None = None,
    ) -> PageAnalyzeResult:
        if img_size and img_size[0] > 0 and img_size[1] > 0:
            img_w, img_h = img_size
        else:
            img_w, img_h = _image_size(image_bytes)
        content = await self._vision_completion(image_bytes, PAGE_ANALYZE_PROMPT, mime)
        raw = _extract_json(content)
        raw["_raw_completion"] = content

        parts: list[PagePart] = []
        for p in _normalize_parts(raw):
            parts.append(
                PagePart(
                    kind=p["kind"],
                    id=str(p["id"]) if p.get("id") is not None else None,
                    print_text=str(p.get("print", "")),
                    handwriting=str(p.get("handwriting", "")),
                    bbox_print=_norm_bbox(p.get("bbox_print", []), img_w, img_h),
                    bbox_handwriting=_norm_bbox(p.get("bbox_handwriting", []), img_w, img_h),
                )
            )

        n_q = sum(1 for p in parts if p.kind == "question")
        logger.info(
            "analyze_page: parts=%d (header=%d section=%d question=%d)",
            len(parts),
            sum(1 for p in parts if p.kind == "header"),
            sum(1 for p in parts if p.kind == "section"),
            n_q,
        )
        return PageAnalyzeResult(parts=parts, raw=raw)


vllm_client = VllmVisionClient()
