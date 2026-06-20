from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from typing import Any

import httpx

from app.clients.redis_client import acquire_vllm_slot, release_vllm_slot
from app.config import settings

logger = logging.getLogger(__name__)


@contextmanager
def vllm_slot():
    if not acquire_vllm_slot():
        raise TimeoutError("vLLM concurrency limit reached")
    try:
        yield
    finally:
        release_vllm_slot()


def chat_completion(system: str, user: str, *, max_tokens: int = 1024) -> str:
    with vllm_slot():
        with httpx.Client(base_url=settings.vllm_base_url, timeout=120.0) as client:
            r = client.post(
                "/chat/completions",
                json={
                    "model": settings.vllm_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": settings.vllm_temperature,
                    "max_tokens": max_tokens,
                    # Qwen3.x defaults to chain-of-thought; disable for structured JSON agents.
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            )
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]


def parse_json_block(text: str) -> dict[str, Any]:
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
