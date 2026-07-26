"""35B 视觉主力：轻量版面归类 JSON（非完整 PageBlueprint）。"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import httpx

from ..config import Settings
from .schema import PageLayoutSignals

log = logging.getLogger("copilot.layout_triad.judge")

_SYSTEM = """你是中小学教材版面分析器。根据页面图片判断版式，只输出一个 JSON 对象，不要 markdown。
字段：
{
  "page": 页码整数,
  "category": "目录|正文单栏|正文双栏|正文多栏|侧栏|习题|图版|公式密集|封面/扉页|空白/其它",
  "columns": 1或2或3,
  "aside": 是否有明显侧栏/旁注区,
  "toc_like": 是否像目录页,
  "exercise_like": 是否像习题页,
  "figure_heavy": 插图是否占主导,
  "math_dense": 公式是否很密,
  "confidence": 0到1,
  "notes": "一句中文说明"
}
有侧栏时 category 优先用「侧栏」；目录页用「目录」。"""


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def judge_page(
    settings: Settings,
    *,
    page: int,
    jpeg: bytes,
    timeout_s: float = 120.0,
) -> PageLayoutSignals:
    b64 = base64.standard_b64encode(jpeg).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"
    base = settings.vllm_base_url.rstrip("/")
    headers: dict[str, str] = {}
    if settings.vllm_api_token:
        headers["Authorization"] = f"Bearer {settings.vllm_api_token}"
    payload = {
        "model": settings.vllm_model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"请分析第 {page} 页版面，输出 JSON。"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "temperature": 0.1,
        "max_tokens": 512,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    with httpx.Client(timeout=timeout_s) as client:
        r = client.post(f"{base}/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        raw = (r.json()["choices"][0]["message"].get("content") or "").strip()
    data = _extract_json(raw)
    data["page"] = page
    data["source"] = "judge_35b"
    return PageLayoutSignals.model_validate(data)


def judge_pages(
    settings: Settings,
    jpeg_by_page: dict[int, bytes],
) -> list[PageLayoutSignals]:
    out: list[PageLayoutSignals] = []
    for page in sorted(jpeg_by_page):
        try:
            out.append(judge_page(settings, page=page, jpeg=jpeg_by_page[page]))
        except Exception as e:  # noqa: BLE001
            log.warning("judge_35b page %s failed: %s", page, e)
            out.append(
                PageLayoutSignals(
                    page=page,
                    category="空白/其它",
                    confidence=0.0,
                    notes=f"35B 调用失败: {e}",
                    source="judge_35b_error",
                )
            )
    return out
