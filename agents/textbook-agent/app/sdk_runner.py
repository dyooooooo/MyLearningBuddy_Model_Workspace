from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable

from app import checkpoints, conversations, jobs, prompts_store
from app.bridge_cleanup import close_sdk_bridge, sweep_orphan_bridges
from app.config import settings
from app.config_store import read_mcp
from app.context import current_job_id
from app.json_util import extract_json_object
from app.sdk_errors import HookDenied, STALL_ZH, humanize_sdk_error, hook_deny_text, is_bridge_error
from app.sdk_patches import apply_cursor_sdk_patches
from app.tools_loader import load_tool_specs
from app.workspace import has_textbook_files

logger = logging.getLogger(__name__)

GRADE_PROMPT = prompts_store.SEEDS["grade"]


def book_missing_result(request_id: str, notes: str = "教材未覆盖") -> dict[str, Any]:
    return {
        "request_id": request_id,
        "correct": None,
        "verdict": "教材未覆盖",
        "correct_solution": "",
        "textbook": {},
        "book_missing": True,
        "notes": notes,
    }


def run_grade(payload: dict[str, Any], job_id: str = "") -> dict[str, Any]:
    request_id = str(payload.get("request_id") or "").strip()
    hint = str(payload.get("textbook_hint") or "")
    token = current_job_id.set(job_id) if job_id else None
    try:
        if not has_textbook_files(hint):
            if job_id:
                jobs.append_trace(job_id, "search", "查教材目录", "textbooks 目录没有 PDF/MD")
                jobs.append_trace(job_id, "answer", "教材未覆盖", "不调 SDK")
            return book_missing_result(request_id, "textbooks 目录没有 PDF/MD")
        prompt = prompts_store.get("grade").format(
            request_id=request_id,
            subject=payload.get("subject") or "math",
            grade=payload.get("grade") or "hs",
            textbook_hint=hint,
            problem_text=payload.get("problem_text") or "",
            student_answer=payload.get("student_answer") or "",
        )
        text, _agent_id = _prompt_sdk(prompt, job_id=job_id)
        data = extract_json_object(text)
        data.setdefault("request_id", request_id)
        data.setdefault("book_missing", False)
        data.setdefault("textbook", {})
        if job_id:
            jobs.append_trace(job_id, "answer", "判定", _short(data))
        return data
    finally:
        if token is not None:
            current_job_id.reset(token)


CHAT_PROMPT = prompts_store.SEEDS["chat"]
PLAN_PROMPT = prompts_store.SEEDS["plan"]
ASK_PROMPT = prompts_store.SEEDS["ask"]

WRITE_TOOL_IDS = frozenset(
    {
        "write_tool",
        "write_skill",
        "write_rule",
        "reload_config",
        "write_prompt",
        "write_hook",
        "write_hook_script",
        "checkpoint",
        "restore_checkpoint",
    }
)


def _prompt_for(mode: str) -> str:
    return prompts_store.for_mode(mode)


def run_chat(
    message: str,
    conversation_id: str = "",
    job_id: str = "",
    mode: str = "agent",
    model: str = "",
    model_params: list[dict[str, str]] | None = None,
    images: list[dict[str, Any]] | None = None,
    attachments: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    token = current_job_id.set(job_id) if job_id else None
    try:
        kind = (mode or "agent").strip().lower()
        if kind not in ("agent", "plan", "ask"):
            kind = "agent"
        if job_id and jobs.is_cancelled(job_id):
            raise jobs.JobCancelled()
        existing = jobs.get_job(job_id) if job_id else None
        checkpoint_id = jobs.job_checkpoint(existing)
        if not checkpoint_id:
            checkpoint_id = checkpoints.snapshot_turn(
                job_id,
                conversation_id,
                (message or "")[:80] or "对话开始",
            )
        prompt = _prompt_for(kind) + (message or "")
        if attachments:
            lines = [f"- {row.get('path') or row.get('name')}" for row in attachments if row]
            if lines:
                prompt += "\n\n用户附件（工作区路径）：\n" + "\n".join(lines)
        resume = conversations.resume_id(conversation_id) if conversation_id else conversation_id
        reply, agent_id = _prompt_sdk(
            prompt,
            conversation_id=resume,
            job_id=job_id,
            mode=kind,
            model=model,
            model_params=model_params,
            images=images,
        )
        if conversation_id and agent_id:
            conversations.bind_sdk(conversation_id, agent_id)
        if job_id:
            jobs.append_trace(job_id, "answer", "回答", reply[:2000])
        title = (conversations.get(conversation_id) or {}).get("title") or ""
        if conversation_id:
            conversations.schedule_title(
                conversation_id,
                summarize_title,
                current_user=message,
                current_reply=reply,
            )
        return {
            "conversation_id": conversation_id or agent_id or "local",
            "title": title,
            "reply": reply,
            "book_missing": False,
            "mode": kind,
            "model": (model or settings.cursor_model).strip(),
            "model_params": list(model_params or []),
            "checkpoint": checkpoint_id,
        }
    finally:
        if token is not None:
            current_job_id.reset(token)


def mcp_servers_for_sdk() -> dict[str, Any]:
    servers = read_mcp().get("mcpServers") or {}
    if not isinstance(servers, dict):
        return {}
    return _to_sdk_mcp(servers)


def custom_tools_for_sdk(mode: str = "agent") -> dict[str, Any]:
    from cursor_sdk import CustomTool

    out: dict[str, Any] = {}
    for spec in load_tool_specs():
        tool_id = str(spec["id"])
        if mode == "ask" and tool_id in WRITE_TOOL_IDS:
            continue
        out[tool_id] = CustomTool(
            description=spec["description"],
            input_schema=spec["input_schema"],
            execute=spec["execute"],
        )
    return out


def _sdk_mode(mode: str) -> str:
    return "plan" if mode in ("plan", "ask") else "agent"


def model_selection(model: str, params: list[dict[str, str]] | None = None) -> Any:
    mid = (model or settings.cursor_model or "").strip() or settings.cursor_model
    if mid.lower() in {"auto", "default"}:
        mid = "default"
    try:
        from cursor_sdk import ModelParameterValue, ModelSelection
    except ImportError:
        return mid
    pairs = []
    for row in params or []:
        pid = str(row.get("id") or "").strip()
        val = str(row.get("value") if row.get("value") is not None else "").strip()
        if pid:
            pairs.append(ModelParameterValue(id=pid, value=val))
    try:
        return ModelSelection(id=mid, params=pairs)
    except TypeError:
        return mid


def _send_message(prompt: str, images: list[dict[str, Any]] | None) -> Any:
    usable = [row for row in (images or []) if (row.get("data") or "").strip()]
    if not usable:
        return prompt
    from cursor_sdk import UserMessage

    packed = [
        {"data": str(row.get("data") or ""), "mime_type": str(row.get("mime_type") or "image/png")}
        for row in usable
    ]
    return UserMessage(text=prompt, images=packed)


TITLE_PROMPT = prompts_store.SEEDS["title"]


def summarize_title(blob: str) -> str:
    """One-shot Agent call, no workspace tools — same idea as Cursor's chat title."""
    key = (settings.cursor_api_key or "").strip()
    if not key or not (blob or "").strip():
        return ""
    from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

    tmp = settings.home / ".title-scratch"
    tmp.mkdir(parents=True, exist_ok=True)
    opt_kw: dict[str, Any] = {
        "api_key": key,
        "model": (settings.cursor_model or "composer-2.5").strip(),
        "name": "conversation-title",
        "local": LocalAgentOptions(cwd=str(tmp), setting_sources=[]),
    }
    try:
        opts = AgentOptions(**opt_kw, mode="ask")
    except TypeError:
        opts = AgentOptions(**opt_kw)
    result = Agent.prompt(prompts_store.title_template().format(blob=(blob or "")[:1800]), opts)
    text = getattr(result, "result", None) or getattr(result, "text", None) or ""
    if callable(text):
        text = text()
    return conversations.clean_agent_title(str(text or ""))


def prepare_sdk_turn() -> None:
    apply_cursor_sdk_patches()
    try:
        sweep_orphan_bridges(grace_s=1.0)
    except Exception:
        logger.warning("sweep_orphan_bridges failed", exc_info=True)


def recover_bridge() -> None:
    try:
        close_sdk_bridge()
    except Exception:
        logger.warning("close_sdk_bridge failed", exc_info=True)
    try:
        sweep_orphan_bridges(grace_s=1.0)
    except Exception:
        logger.warning("sweep_orphan_bridges failed", exc_info=True)


def _stall_s() -> float:
    return max(1.0, float(getattr(settings, "cursor_stall_timeout_s", 45) or 45))


def _eval_timeout_s() -> float:
    return max(1.0, float(getattr(settings, "cursor_eval_timeout_s", 300) or 300))


def _raise_if_cancelled(job_id: str) -> None:
    if job_id and jobs.is_cancelled(job_id):
        raise jobs.JobCancelled()


def _cancel_run(run: Any) -> None:
    if run is None:
        return
    try:
        cancel = getattr(run, "cancel", None)
        if callable(cancel):
            cancel()
    except Exception:
        logger.warning("run.cancel failed", exc_info=True)


def _fail(exc: BaseException) -> RuntimeError:
    return RuntimeError(humanize_sdk_error(exc))


def _call_watched(
    fn: Callable[[], Any],
    *,
    job_id: str,
    run: Any = None,
    label: str = "",
) -> Any:
    """Run ``fn`` on a worker thread; fail if it does not return within the stall window."""
    box: dict[str, Any] = {}
    done = threading.Event()

    def _work() -> None:
        try:
            box["result"] = fn()
        except Exception as exc:
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=_work, name=f"sdk-{label or 'call'}", daemon=True).start()
    waited = 0
    stall = _stall_s()
    while not done.wait(1):
        waited += 1
        _raise_if_cancelled(job_id)
        if job_id and waited % 15 == 0:
            jobs.append_trace(job_id, "tool", "still running", f"{label or '等待桥接'}已 {waited}s")
        if waited >= stall:
            _cancel_run(run)
            recover_bridge()
            raise TimeoutError(STALL_ZH)
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _resume_or_create(agent_cls: Any, cid: str, opts: Any, job_id: str) -> Any:
    agent = None
    if cid:
        try:
            agent = _call_watched(lambda: agent_cls.resume(cid), job_id=job_id, label="resume")
            reload = getattr(agent, "reload", None)
            if callable(reload):
                try:
                    reload()
                except Exception:
                    logger.warning("Agent.reload failed id=%s", cid, exc_info=True)
        except jobs.JobCancelled:
            raise
        except Exception as exc:
            logger.warning("Agent.resume failed id=%s err=%s", cid, exc)
            agent = None
    if agent is None:
        agent = _call_watched(lambda: agent_cls.create(opts), job_id=job_id, label="create")
    return agent


def _stream_watched(run: Any, job_id: str) -> None:
    stream = getattr(run, "messages", None)
    if not (job_id and callable(stream)):
        return
    inbox: queue.Queue[tuple[str, Any]] = queue.Queue()

    def _work() -> None:
        try:
            for message in stream():
                if jobs.is_cancelled(job_id):
                    inbox.put(("cancel", None))
                    return
                inbox.put(("msg", message))
        except jobs.JobCancelled:
            inbox.put(("cancel", None))
        except Exception as exc:
            inbox.put(("err", exc))
        finally:
            inbox.put(("done", None))

    threading.Thread(target=_work, name="sdk-stream", daemon=True).start()
    stall = _stall_s()
    started = time.monotonic()
    last_msg = started
    last_beat = started
    while True:
        _raise_if_cancelled(job_id)
        try:
            kind, payload = inbox.get(timeout=1)
        except queue.Empty:
            now = time.monotonic()
            waited = int(now - started)
            if now - last_beat >= 15:
                jobs.append_trace(job_id, "tool", "still running", f"已等待 {waited}s，还没有最终回答")
                last_beat = now
            if now - last_msg >= stall:
                _cancel_run(run)
                recover_bridge()
                raise TimeoutError(STALL_ZH)
            continue
        if kind == "msg":
            last_msg = time.monotonic()
            deny = hook_deny_text(payload)
            _ingest_sdk_message(job_id, payload)
            if deny:
                jobs.append_trace(job_id, "tool", "permission deny", deny)
                _cancel_run(run)
                raise HookDenied(deny)
        elif kind == "cancel":
            raise jobs.JobCancelled()
        elif kind == "err":
            if isinstance(payload, jobs.JobCancelled):
                raise payload
            logger.warning("sdk stream failed: %s", payload)
            jobs.append_trace(job_id, "tool", "stream interrupted", humanize_sdk_error(payload)[:800])
            if is_bridge_error(payload) or isinstance(payload, TimeoutError):
                raise _fail(payload)
        elif kind == "done":
            return


def _prompt_sdk(
    prompt: str,
    conversation_id: str = "",
    job_id: str = "",
    mode: str = "agent",
    model: str = "",
    model_params: list[dict[str, str]] | None = None,
    images: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    key = (settings.cursor_api_key or "").strip()
    if not key:
        raise RuntimeError("CURSOR_API_KEY is empty")
    prepare_sdk_turn()
    from cursor_sdk import Agent, AgentOptions, LocalAgentOptions, SendOptions

    mcp = mcp_servers_for_sdk()
    tools = custom_tools_for_sdk(mode)
    local_kw: dict[str, Any] = {
        "cwd": str(settings.home),
        "setting_sources": ["project", "plugins"],
    }
    if tools:
        local_kw["custom_tools"] = tools
    chosen = (model or settings.cursor_model).strip() or settings.cursor_model
    selection = model_selection(chosen, model_params)
    sdk_mode = _sdk_mode(mode)
    opt_kw: dict[str, Any] = {
        "api_key": key,
        "model": selection,
        "mcp_servers": mcp or None,
        "local": LocalAgentOptions(**local_kw),
    }
    try:
        opts = AgentOptions(**opt_kw, mode=sdk_mode)
    except TypeError:
        opts = AgentOptions(**opt_kw)
    try:
        send_opts = SendOptions(model=selection, mode=sdk_mode)
    except TypeError:
        send_opts = SendOptions(model=selection)

    cid = (conversation_id or "").strip()
    agent = None
    run = None
    last_exc: BaseException | None = None
    for attempt in range(2):
        _raise_if_cancelled(job_id)
        try:
            if job_id:
                title = "正在连接 Cursor" if attempt == 0 else "清桥后重试"
                jobs.append_trace(job_id, "tool", "connect", title)
            agent = _resume_or_create(Agent, cid, opts, job_id)
            run = _call_watched(
                lambda: agent.send(_send_message(prompt, images), send_opts),
                job_id=job_id,
                label="send",
            )
            last_exc = None
            break
        except jobs.JobCancelled:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt == 0 and is_bridge_error(exc):
                if job_id:
                    jobs.append_trace(job_id, "tool", "bridge retry", humanize_sdk_error(exc))
                recover_bridge()
                agent = None
                run = None
                continue
            raise _fail(exc) from exc
    if last_exc is not None:
        raise _fail(last_exc) from last_exc
    if run is None or agent is None:
        raise RuntimeError(STALL_ZH)

    if job_id:
        jobs.attach_run(job_id, run)
        if jobs.is_cancelled(job_id):
            _cancel_run(run)
            raise jobs.JobCancelled()
    try:
        _stream_watched(run, job_id)
    except (jobs.JobCancelled, HookDenied):
        raise
    except Exception as exc:
        if is_bridge_error(exc) or isinstance(exc, TimeoutError):
            raise _fail(exc) from exc
        logger.warning("sdk stream failed: %s", exc)
        if job_id:
            jobs.append_trace(job_id, "tool", "stream interrupted", humanize_sdk_error(exc)[:800])
    if job_id and jobs.is_cancelled(job_id):
        raise jobs.JobCancelled()
    result = _wait_run(run, job_id)
    status = getattr(result, "status", None)
    if str(status or "").lower() in ("cancelled", "canceled"):
        raise jobs.JobCancelled()
    if status == "error":
        raise RuntimeError(f"cursor agent run failed: {getattr(result, 'id', '')}")
    text = getattr(result, "result", None) or getattr(result, "text", None)
    if callable(text):
        text = text()
    agent_id = str(
        getattr(agent, "agent_id", None)
        or getattr(agent, "id", None)
        or getattr(result, "agent_id", None)
        or cid
        or ""
    )
    return str(text or ""), agent_id


def _wait_run(run: Any, job_id: str) -> Any:
    """Block on ``run.wait`` but keep the dock alive if the stream already ended."""
    timeout_s = _eval_timeout_s()
    box: dict[str, Any] = {}
    done = threading.Event()

    def _wait() -> None:
        try:
            box["result"] = run.wait()
        except Exception as exc:
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=_wait, name="sdk-wait", daemon=True).start()
    waited = 0
    while not done.wait(1):
        waited += 1
        if job_id and jobs.is_cancelled(job_id):
            _cancel_run(run)
            raise jobs.JobCancelled()
        if job_id and waited % 15 == 0:
            jobs.append_trace(job_id, "tool", "still running", f"已等待 {waited}s，还没有最终回答")
        if waited >= timeout_s:
            _cancel_run(run)
            raise TimeoutError(f"Cursor 任务超时，超过 {int(timeout_s)} 秒仍没有最终回答")
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _ingest_sdk_message(job_id: str, message: Any) -> None:
    typ = str(getattr(message, "type", "") or "")
    if typ == "thinking":
        text = str(getattr(message, "text", "") or "").strip()
        if text:
            jobs.append_trace(job_id, "think", "思考", text[:800])
        return
    if typ == "tool_call":
        name = str(getattr(message, "name", "") or "tool")
        status = str(getattr(message, "status", "") or "")
        args = getattr(message, "args", None)
        kind = "search" if "search" in name else "open_pdf" if "pdf" in name else "tool"
        jobs.append_trace(job_id, kind, f"{name} {status}".strip(), _short(args))
        return
    if typ in ("status", "task"):
        label = str(getattr(message, "status", "") or typ)
        detail = str(getattr(message, "message", None) or getattr(message, "text", "") or "")
        jobs.append_trace(job_id, "tool", f"{typ} {label}".strip(), detail[:800])
        return
    if typ == "request":
        jobs.append_trace(
            job_id,
            "tool",
            "request",
            str(getattr(message, "request_id", "") or getattr(message, "type", "request"))[:800],
        )
        return
    if typ == "assistant":
        text = _assistant_text(message)
        if text:
            jobs.append_trace(job_id, "answer", "作答中", text[:2000])


def _assistant_text(message: Any) -> str:
    inner = getattr(message, "message", None)
    content = getattr(inner, "content", None) if inner is not None else None
    if isinstance(content, str):
        return content
    chunks: list[str] = []
    for block in content or []:
        if getattr(block, "type", "") == "text":
            chunks.append(str(getattr(block, "text", "") or ""))
        elif isinstance(block, dict) and block.get("type") == "text":
            chunks.append(str(block.get("text") or ""))
    return "".join(chunks)


def _short(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:2000]
    try:
        import json

        return json.dumps(value, ensure_ascii=False)[:2000]
    except TypeError:
        return str(value)[:2000]


def _to_sdk_mcp(raw: dict[str, Any]) -> dict[str, Any]:
    """Map Cursor mcp.json entries to SDK Http/Stdio configs."""
    try:
        from cursor_sdk import HttpMcpServerConfig, StdioMcpServerConfig
    except ImportError:
        from cursor_sdk.types import HttpMcpServerConfig, StdioMcpServerConfig

    out: dict[str, Any] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        url = str(spec.get("url") or "").strip()
        command = str(spec.get("command") or "").strip()
        if url:
            headers = spec.get("headers") if isinstance(spec.get("headers"), dict) else None
            out[name] = HttpMcpServerConfig(
                url=url,
                type=str(spec.get("type") or "http"),
                headers=headers,
            )
        elif command:
            args = spec.get("args")
            env = spec.get("env")
            out[name] = StdioMcpServerConfig(
                command=command,
                args=list(args) if isinstance(args, list) else None,
                env=env if isinstance(env, dict) else None,
            )
    return out
