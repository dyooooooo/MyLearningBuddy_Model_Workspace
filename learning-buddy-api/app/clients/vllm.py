from __future__ import annotations

import base64
import json
import logging
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from app.clients.redis_client import acquire_vllm_slot, release_vllm_slot
from app.config import settings

logger = logging.getLogger(__name__)

# Valid JSON single-char escapes: " \ / b f n r t ; plus \uXXXX.
# LaTeX often emits \underline / \neq / \frac / \{ which break json.loads.
# Escape repair is string-aware (see _fix_invalid_json_escapes).

# Shared, lazily-created HTTP client so multiple per-page refine calls reuse the
# same connection pool instead of opening a fresh TCP/TLS connection each time.
# httpx.Client is safe to share across threads for sending requests.
_CLIENT: httpx.Client | None = None
_CLIENT_LOCK = threading.Lock()


_MEDIA_MARKER: str | None = None


def _llama_origin() -> str:
    base = settings.vllm_base_url.rstrip("/")
    if base.endswith("/v1"):
        return base[: -len("/v1")]
    return base


def _media_marker(client: httpx.Client) -> str:
    """Image placeholder from the running llama-server. It changes when that process restarts."""
    global _MEDIA_MARKER
    if _MEDIA_MARKER:
        return _MEDIA_MARKER
    response = client.get(f"{_llama_origin()}/props", timeout=10.0)
    response.raise_for_status()
    marker = str(response.json().get("media_marker") or "")
    if not marker:
        raise RuntimeError("llama-server props has no media_marker")
    _MEDIA_MARKER = marker
    return marker


def _vision_completion(text: str, images: list[str], max_tokens: int) -> str:
    """One vision call on /completions. /v1/completions rejects a structured prompt."""
    client = _vllm_client()
    marker = _media_marker(client)
    marks = "\n".join(marker for _ in images)
    prompt = f"<|im_start|>user\n{marks}\n{text}<|im_end|>\n<|im_start|>assistant\n"
    response = client.post(
        f"{_llama_origin()}/completions",
        json={
            "prompt": {"prompt_string": prompt, "multimodal_data": images},
            "temperature": settings.vllm_temperature,
            "n_predict": max_tokens,
        },
        timeout=180.0,
    )
    response.raise_for_status()
    return str(response.json().get("content") or "").strip()


def _vllm_client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = httpx.Client(
                    base_url=settings.vllm_base_url,
                    timeout=180.0,
                )
    return _CLIENT


@dataclass
class ChatCompletionResult:
    content: str
    reasoning_content: str = ""
    usage: dict[str, Any] | None = None


@contextmanager
def vllm_slot():
    if not acquire_vllm_slot():
        raise TimeoutError("vLLM concurrency limit reached")
    try:
        yield
    finally:
        release_vllm_slot()


def chat_completion_detail(
    system: str,
    user: str,
    *,
    max_tokens: int = 1024,
    enable_thinking: bool | None = None,
) -> ChatCompletionResult:
    thinking = False if enable_thinking is None else enable_thinking
    with vllm_slot():
        client = _vllm_client()
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
                "chat_template_kwargs": {"enable_thinking": thinking},
            },
            timeout=120.0,
        )
        r.raise_for_status()
        data = r.json()
        msg = data["choices"][0]["message"]
        usage = data.get("usage")
        return ChatCompletionResult(
            content=msg.get("content") or "",
            reasoning_content=msg.get("reasoning_content") or "",
            usage=usage if isinstance(usage, dict) else None,
        )


def chat_completion(
    system: str,
    user: str,
    *,
    max_tokens: int = 1024,
    enable_thinking: bool | None = None,
) -> str:
    result = chat_completion_detail(
        system,
        user,
        max_tokens=max_tokens,
        enable_thinking=enable_thinking,
    )
    if result.reasoning_content and enable_thinking:
        return f"{result.reasoning_content}\n\n{result.content}".strip()
    return result.content


def chat_completion_json(
    system: str,
    user: str,
    schema: dict[str, Any],
    *,
    schema_name: str = "result",
    max_tokens: int = 4096,
    timeout_s: float = 180.0,
) -> dict[str, Any]:
    """Chat completion constrained to ``schema`` (json_schema), with plain retry.

    Falls back to unconstrained generation + ``parse_json_block_robust`` when the
    server rejects ``response_format`` (HTTP 400).
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    base_payload: dict[str, Any] = {
        "model": settings.vllm_model,
        "messages": messages,
        "temperature": settings.vllm_temperature,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    schema_payload = {
        **base_payload,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        },
    }

    def _post(payload: dict[str, Any]) -> str:
        with vllm_slot():
            client = _vllm_client()
            r = client.post("/chat/completions", json=payload, timeout=timeout_s)
            r.raise_for_status()
            data = r.json()
            msg = data["choices"][0]["message"]
            return (msg.get("content") or "").strip()

    raw = ""
    try:
        raw = _post(schema_payload)
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 400:
            logger.warning(
                "chat_completion_json: json_schema rejected (400), retrying without schema"
            )
            raw = _post(base_payload)
        else:
            raise
    return parse_json_block_robust(raw)


def vision_page_to_markdown(
    image_bytes: bytes,
    *,
    system: str,
    max_tokens: int = 4096,
) -> str:
    """OCR one page image to Markdown via the llama.cpp completions endpoint."""
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    text = f"{system}\n\n提取本页全部印刷体教材正文，输出 Markdown。"
    with vllm_slot():
        return _vision_completion(text, [b64], max_tokens)


def vision_page_refine_json(
    image_bytes: bytes,
    blocks_payload: list[dict],
    *,
    system: str,
    page_no: int,
    max_tokens: int = 3072,
    user_note: str = "",
) -> dict[str, Any]:
    """Refine one page layout via the llama.cpp completions endpoint."""
    import json as _json

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    note = (user_note or "").strip()
    note_block = (
        f"\n\n## 用户改进建议（优先按此修改）\n{note}\n"
        if note
        else ""
    )
    user_text = (
        f"{system}\n\npage_no={page_no}\n"
        "Previous recognition blocks JSON (draft to correct against the page image):\n"
        + _json.dumps(blocks_payload, ensure_ascii=False)
        + note_block
    )
    with vllm_slot():
        raw = _vision_completion(user_text, [b64], max_tokens)
        return parse_json_block_robust(raw)


def vision_json_completion(
    image_bytes: bytes,
    user_text: str,
    *,
    system: str,
    max_tokens: int = 6144,
    response_schema: dict[str, Any] | None = None,
    schema_name: str = "structured_output",
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generic multimodal JSON call for structured vision tasks.

    When ``response_schema`` is provided the request uses vLLM guided decoding
    (``response_format: json_schema``).  If the server rejects the request with
    HTTP 400 (e.g. an unsupported schema keyword) a warning is logged and the
    call is retried *without* the schema so the pipeline keeps running.

    When ``debug`` (a mutable dict) is provided it is populated with observability
    fields — ``used_schema``, ``finish_reason`` (``"length"`` means the model was
    truncated by ``max_tokens``), token ``usage`` and the raw response ``text`` —
    so the import pipeline can dump them for per-stage diagnosis.
    """
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    text = f"{system}\n\n{user_text}"
    if response_schema is not None:
        logger.info(
            "vision_json_completion: /completions has no json_schema; %s is not sent",
            schema_name,
        )
    with vllm_slot():
        raw = _vision_completion(text, [b64], max_tokens)
        if debug is not None:
            debug["used_schema"] = False
            debug["finish_reason"] = None
            debug["usage"] = None
            debug["max_tokens"] = max_tokens
            debug["raw_text"] = raw
        return parse_json_block_robust(raw)


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


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _fix_invalid_json_escapes(fragment: str) -> str:
    """Fix LaTeX-ish invalid escapes inside JSON string literals only.

    Walks the fragment with a small state machine so already-valid escapes
    (e.g. ``\\\\pi`` in the JSON source) are left alone, while raw LaTeX
    (``\\{``, ``\\pi``, ``\\frac``) gets an extra backslash.
    """
    out: list[str] = []
    i = 0
    n = len(fragment)
    in_string = False

    while i < n:
        ch = fragment[i]
        if not in_string:
            out.append(ch)
            if ch == '"':
                # Heuristic: start of a JSON string (keys/values). Escaped
                # quotes outside strings do not occur in model JSON.
                in_string = True
            i += 1
            continue

        if ch == '"':
            out.append(ch)
            in_string = False
            i += 1
            continue

        if ch == "\\" and i + 1 < n:
            nxt = fragment[i + 1]
            if nxt in '"\\/':
                out.append(ch)
                out.append(nxt)
                i += 2
                continue
            if nxt in "bfnrt":
                # JSON whitespace escape — unless LaTeX (\frac, \neq, \text…).
                follow = fragment[i + 2] if i + 2 < n else ""
                if follow.isalpha():
                    out.append("\\\\")
                    out.append(nxt)
                    i += 2
                    continue
                out.append(ch)
                out.append(nxt)
                i += 2
                continue
            if nxt == "u":
                hexpart = fragment[i + 2 : i + 6]
                if len(hexpart) == 4 and all(
                    c in "0123456789abcdefABCDEF" for c in hexpart
                ):
                    out.append(fragment[i : i + 6])
                    i += 6
                    continue
                # Incomplete \uXXXX (often \underline) → treat as LaTeX.
                out.append("\\\\")
                out.append(nxt)
                i += 2
                continue
            # Invalid JSON escape (\{, \pi, \left, trailing \, …).
            out.append("\\\\")
            out.append(nxt)
            i += 2
            continue

        if ch == "\\" and i + 1 >= n:
            # Trailing backslash at end of fragment.
            out.append("\\\\")
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)



def _close_truncated_json(fragment: str) -> str:
    out = fragment.rstrip()
    if out.count('"') % 2 == 1:
        out += '"'
    while out.count("[") > out.count("]"):
        out += "]"
    while out.count("{") > out.count("}"):
        out += "}"
    return out


def salvage_json_fields(text: str, fields: tuple[str, ...]) -> dict[str, Any]:
    cleaned = _strip_code_fence(text)
    out: dict[str, Any] = {}

    for field in fields:
        if field in ("is_wrong",):
            m = re.search(rf'"{field}"\s*:\s*(true|false|null)', cleaned, re.I)
            if m:
                val = m.group(1).lower()
                out[field] = None if val == "null" else val == "true"
            continue
        if field in ("solver_confidence", "eval_confidence", "kp_confidence"):
            m = re.search(rf'"{field}"\s*:\s*([0-9.]+)', cleaned)
            if m:
                out[field] = float(m.group(1))
            continue
        m = re.search(
            rf'"{re.escape(field)}"\s*:\s*"((?:[^"\\]|\\.)*)"',
            cleaned,
            re.DOTALL,
        )
        if m:
            out[field] = m.group(1).replace('\\"', '"')

    arr_match = re.search(r'"solution_steps"\s*:\s*\[(.*?)\]', cleaned, re.DOTALL)
    if arr_match and "solution_steps" in fields:
        steps = re.findall(r'"((?:[^"\\]|\\.)*)"', arr_match.group(1))
        if steps:
            out["solution_steps"] = [s.replace('\\"', '"') for s in steps[:6]]

    rs_match = re.search(r'"reasoning_steps"\s*:\s*\[(.*?)\]', cleaned, re.DOTALL)
    if rs_match and "reasoning_steps" in fields:
        steps = re.findall(r'"((?:[^"\\]|\\.)*)"', rs_match.group(1))
        if steps:
            out["reasoning_steps"] = [s.replace('\\"', '"') for s in steps[:6]]

    return out


def parse_json_block_robust(text: str, *, salvage_fields: tuple[str, ...] | None = None) -> dict[str, Any]:
    cleaned = _strip_code_fence(text)
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("No JSON object in model output")
    fragment = cleaned[start:]

    # Prefer escape-fixed candidates first so LaTeX (\frac/\neq/…) is not
    # silently accepted as JSON control chars on the raw fragment.
    fixed = _fix_invalid_json_escapes(fragment)
    attempts = [
        fixed,
        _close_truncated_json(fixed),
        fragment,
        _close_truncated_json(fragment),
    ]
    last_err: Exception | None = None
    for candidate in attempts:
        try:
            return parse_json_block(candidate)
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e

    if salvage_fields:
        salvaged = salvage_json_fields(text, salvage_fields)
        if salvaged.get("reference_answer") or salvaged.get("is_wrong") is not None:
            logger.info("salvaged JSON fields: %s", list(salvaged.keys()))
            return salvaged

    if last_err is not None:
        raise last_err
    raise ValueError("No JSON object in model output")
