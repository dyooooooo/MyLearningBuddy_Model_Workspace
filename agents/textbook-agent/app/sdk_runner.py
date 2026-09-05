from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable

from app import checkpoints, conversations, jobs, plans, prompts_store, sdk_client, transport_breaker
from app.bridge_cleanup import close_sdk_bridge, sweep_orphan_bridges
from app.config import default_model, default_model_params, settings, title_model, title_model_params
from app.config_store import read_mcp
from app.context import bind_live_sdk_job, current_job_id
from app.json_util import extract_json_object
from app.resume_context import compose_prompts
from app.sdk_errors import (
    AUTH_ZH,
    HookDenied,
    SEND_ZH,
    STALL_ZH,
    humanize_run_fail,
    humanize_sdk_error,
    hook_deny_text,
    is_agent_busy,
    is_auth_text,
    is_bridge_error,
    is_connect_stall,
    is_dead_thread,
    is_detached_run,
    is_stream_stall,
)
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
        "create_task",
        "update_task",
        "cancel_task",
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
        prompt, followup, meta = compose_prompts(
            message or "",
            system=_prompt_for(kind),
            conversation_id=conversation_id,
            job_id=job_id,
        )
        if attachments:
            lines = [f"- {row.get('path') or row.get('name')}" for row in attachments if row]
            if lines:
                extra = "\n\n用户附件（工作区路径）：\n" + "\n".join(lines)
                prompt += extra
                followup += extra
        resume = conversations.resume_id(conversation_id) if conversation_id else ""
        if meta.get("has_breakpoint") and job_id:
            jobs.append_trace(job_id, "tool", "breakpoint", "已钉住上一轮断点，不翻更早的对话")
        plan_before = plans.snapshot_index() if kind == "plan" else {}
        reply, agent_id = _prompt_sdk(
            prompt,
            conversation_id=conversation_id,
            resume_id=resume,
            job_id=job_id,
            mode=kind,
            model=model,
            model_params=model_params,
            images=images,
            followup=followup,
        )
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
        out: dict[str, Any] = {
            "conversation_id": conversation_id or agent_id or "local",
            "title": title,
            "reply": reply,
            "book_missing": False,
            "mode": kind,
            "model": (model or default_model()).strip(),
            "model_params": list(model_params or default_model_params()),
            "checkpoint": checkpoint_id,
        }
        if kind == "plan":
            live = jobs.get_job(job_id) if job_id else None
            plan_row = plans.finalize(
                reply,
                plan_before,
                job_id,
                conversation_id,
                trace=(live or {}).get("trace") if live else None,
            )
            if plan_row:
                out["plan"] = plan_row
        return out
    finally:
        if token is not None:
            current_job_id.reset(token)


def mcp_servers_for_sdk() -> dict[str, Any]:
    servers = read_mcp().get("mcpServers") or {}
    if not isinstance(servers, dict):
        return {}
    return _to_sdk_mcp(servers)


def tool_allowed(mode: str, tool_id: str) -> bool:
    kind = (mode or "agent").strip().lower()
    if kind in ("ask", "plan") and tool_id in WRITE_TOOL_IDS:
        return False
    return True


def custom_tools_for_sdk(mode: str = "agent") -> dict[str, Any]:
    from cursor_sdk import CustomTool

    out: dict[str, Any] = {}
    for spec in load_tool_specs():
        tool_id = str(spec["id"])
        if not tool_allowed(mode, tool_id):
            continue
        out[tool_id] = CustomTool(
            description=spec["description"],
            input_schema=spec["input_schema"],
            execute=spec["execute"],
        )
    return out


def _sdk_mode(mode: str) -> str:
    kind = (mode or "agent").strip().lower()
    if kind == "ask":
        return "ask"
    if kind == "plan":
        return "plan"
    return "agent"


def model_selection(model: str, params: list[dict[str, str]] | None = None) -> Any:
    mid = (model or default_model() or "").strip() or default_model()
    if mid.lower() in {"auto", "default"}:
        mid = "default"
    try:
        from cursor_sdk import ModelParameterValue, ModelSelection
    except ImportError:
        return mid
    rows = list(params or [])
    if not rows and mid not in {"default", "auto"}:
        rows = default_model_params()
    pairs = []
    for row in rows:
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
        "model": model_selection(title_model(), title_model_params()),
        "name": "conversation-title",
        "local": LocalAgentOptions(cwd=str(tmp), setting_sources=[]),
    }
    try:
        opts = AgentOptions(**opt_kw, mode="ask")
    except TypeError:
        opts = AgentOptions(**opt_kw)
    wait_send_slot(_TITLE_SLOT)
    try:
        client = sdk_client.ensure_client()
        if client is not None:
            result = Agent.prompt(
                prompts_store.title_template().format(blob=(blob or "")[:1800]),
                opts,
                client=client,
            )
        else:
            result = Agent.prompt(prompts_store.title_template().format(blob=(blob or "")[:1800]), opts)
        text = getattr(result, "result", None) or getattr(result, "text", None) or ""
        if callable(text):
            text = text()
        return conversations.clean_agent_title(str(text or ""))
    finally:
        release_send_slot(_TITLE_SLOT)


def prepare_sdk_turn(job_id: str = "") -> None:
    apply_cursor_sdk_patches()
    if count_held_slots(exclude=job_id) > 0:
        return
    try:
        sweep_orphan_bridges(grace_s=1.0)
    except Exception:
        logger.warning("sweep_orphan_bridges failed", exc_info=True)


def recover_bridge(job_id: str = "") -> None:
    others = count_held_slots(exclude=job_id)
    if others > 0:
        logger.info("recover_bridge skip; %s other send(s) on the shared bridge", others)
        return
    try:
        sdk_client.close_owned_client()
    except Exception:
        logger.warning("close_owned_client failed", exc_info=True)
        try:
            close_sdk_bridge()
        except Exception:
            logger.warning("close_sdk_bridge failed", exc_info=True)
    try:
        sweep_orphan_bridges(grace_s=1.0)
    except Exception:
        logger.warning("sweep_orphan_bridges failed", exc_info=True)
    try:
        sdk_client.ensure_client()
    except Exception:
        logger.warning("relaunch CursorClient after recover failed", exc_info=True)


_slot_lock = threading.Lock()
_slot_held: set[str] = set()
_TITLE_SLOT = "title"


def reset_send_slots_for_tests() -> None:
    with _slot_lock:
        _slot_held.clear()


def count_held_slots(exclude: str = "") -> int:
    """How many turns have claimed the shared bridge, not counting ``exclude``.

    A job can be ``running`` while it is only queued. Those waiters must not
    fill the cap (that deadlocks at cap=1) and must not block ``recover_bridge``.
    """
    skip = (exclude or "").strip()
    with _slot_lock:
        return sum(1 for item in _slot_held if item and item != skip)


def release_send_slot(job_id: str = "") -> None:
    key = (job_id or "").strip()
    if not key:
        return
    with _slot_lock:
        _slot_held.discard(key)


def wait_send_slot(job_id: str = "", max_n: int | None = None) -> None:
    cap = max(1, int(max_n if max_n is not None else getattr(settings, "max_concurrent_sends", 1) or 1))
    key = (job_id or "").strip()
    announced = False
    while True:
        if key and jobs.is_cancelled(key):
            raise jobs.JobCancelled()
        with _slot_lock:
            others = sum(1 for item in _slot_held if item and item != key)
            if others < cap:
                if key:
                    _slot_held.add(key)
                return
        if key and not announced:
            jobs.append_trace(key, "tool", "queued", "另一条对话占用共享桥，排队等待")
            announced = True
        time.sleep(0.4)


def _stall_s() -> float:
    return max(1.0, float(getattr(settings, "cursor_stall_timeout_s", 45) or 45))


def _eval_timeout_s() -> float:
    return max(1.0, float(getattr(settings, "cursor_eval_timeout_s", 1800) or 1800))


def _send_s() -> float:
    return max(_stall_s(), float(getattr(settings, "cursor_send_timeout_s", 180) or 180))


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


_ACTIVE_RUN = frozenset({"running", "queued", "in_progress", "active", "pending"})
_DONE_RUN = frozenset(
    {"finished", "completed", "error", "cancelled", "canceled", "failed", "expired"}
)


def _run_id_of(row: Any) -> str:
    return str(getattr(row, "id", None) or getattr(row, "run_id", None) or "").strip()


def _run_status(row: Any) -> str:
    return str(getattr(row, "status", "") or "").lower()


def _iter_runs(listed: Any) -> list[Any]:
    if listed is None:
        return []
    items = getattr(listed, "items", None)
    if items is None:
        items = listed if isinstance(listed, list) else []
    return [row for row in (items or []) if row is not None]


def _is_active_status(status: str) -> bool:
    s = (status or "").lower()
    if s in _DONE_RUN:
        return False
    return (not s) or s in _ACTIVE_RUN


def _looks_like_run(row: Any) -> bool:
    return callable(getattr(row, "wait", None)) or callable(getattr(row, "messages", None))


def _agent_cls() -> Any:
    try:
        from cursor_sdk import Agent as AgentCls
    except ImportError:
        return None
    return AgentCls


def _is_live_run(run: Any) -> bool:
    """True only if this handle still has a live event stream (can sit in the dock)."""
    if run is None:
        return False
    if getattr(run, "_event_stream", None) is not None:
        return True
    if getattr(run, "_buffer", None):
        return True
    return False


def _find_active_run(agent_id: str) -> Any:
    aid = (agent_id or "").strip()
    if not aid:
        return None
    AgentCls = _agent_cls()
    list_runs = getattr(AgentCls, "list_runs", None) if AgentCls else None
    get_run = getattr(AgentCls, "get_run", None) if AgentCls else None
    if not callable(list_runs):
        return None
    try:
        try:
            listed = list_runs(aid, cwd=str(settings.home))
        except TypeError:
            listed = list_runs(aid)
    except Exception:
        logger.warning("list_runs failed id=%s", aid, exc_info=True)
        return None
    for row in _iter_runs(listed):
        if not _is_active_status(_run_status(row)):
            continue
        rid = _run_id_of(row)
        run = None
        if rid and callable(get_run):
            try:
                try:
                    run = get_run(rid)
                except TypeError:
                    run = get_run(rid, agent_id=aid)
            except Exception:
                logger.warning("get_run failed id=%s", rid, exc_info=True)
                run = None
        if run is None and _looks_like_run(row):
            run = row
        if run is not None:
            return run
    return None


def _dispose_dead_run(agent_id: str, run: Any = None) -> None:
    _cancel_stale_runs(agent_id=agent_id, extra_run=run)


def _wait_until_idle(agent_id: str, *, job_id: str = "", timeout_s: float = 30) -> None:
    deadline = time.monotonic() + max(0.2, timeout_s)
    while True:
        _raise_if_cancelled(job_id)
        if _find_active_run(agent_id) is None:
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(0.25)


def _force_send_opts(send_opts: Any) -> Any:
    try:
        import cursor_sdk
    except ImportError:
        return send_opts
    send_cls = getattr(cursor_sdk, "SendOptions", None)
    if send_cls is None:
        return send_opts
    local_cls = getattr(cursor_sdk, "LocalSendOptions", None)
    local: Any = {"force": True}
    if local_cls is not None:
        try:
            local = local_cls(force=True)
        except TypeError:
            local = {"force": True}
    kw: dict[str, Any] = {}
    stored = getattr(send_opts, "kw", None)
    src = stored if isinstance(stored, dict) else None
    for name in ("model", "mode", "mcp_servers"):
        val = getattr(send_opts, name, None)
        if val is None and src:
            val = src.get(name)
        if val is not None:
            kw[name] = val
    try:
        return send_cls(**kw, local=local)
    except TypeError:
        return send_opts


def _cancel_stale_runs(agent: Any = None, *, agent_id: str = "", extra_run: Any = None) -> int:
    """Drop leftover Cursor runs so the next send is not blocked by activeRunId."""
    n = 0
    if extra_run is not None:
        _cancel_run(extra_run)
        n += 1
    aid = (agent_id or "").strip() or _agent_id_of(agent)
    AgentCls = _agent_cls()
    cancel_run = getattr(AgentCls, "cancel_run", None) if AgentCls else None
    list_runs = getattr(AgentCls, "list_runs", None) if AgentCls else None
    if aid and callable(list_runs):
        try:
            try:
                listed = list_runs(aid, cwd=str(settings.home))
            except TypeError:
                listed = list_runs(aid)
        except Exception:
            logger.warning("list_runs failed id=%s", aid, exc_info=True)
            listed = None
        for row in _iter_runs(listed):
            status = _run_status(row)
            if status in _DONE_RUN:
                continue
            if status and status not in _ACTIVE_RUN:
                continue
            rid = _run_id_of(row)
            try:
                if callable(cancel_run) and rid:
                    try:
                        cancel_run(rid, agent_id=aid)
                    except TypeError:
                        cancel_run(rid)
                else:
                    fn = getattr(row, "cancel", None)
                    if callable(fn):
                        fn()
                    else:
                        continue
                n += 1
            except Exception:
                logger.warning("cancel_run failed id=%s", rid, exc_info=True)
    elif agent is not None:
        cancel = getattr(agent, "cancel", None)
        if callable(cancel):
            try:
                cancel()
                n += 1
            except Exception:
                logger.warning("agent.cancel failed", exc_info=True)
    return n


def _call_watched(
    fn: Callable[[], Any],
    *,
    job_id: str,
    run: Any = None,
    label: str = "",
    agent_id: str = "",
    stall_s: float | None = None,
    recover: bool = True,
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
    stall = _stall_s() if stall_s is None else max(1.0, float(stall_s))
    while not done.wait(1):
        waited += 1
        _raise_if_cancelled(job_id)
        if job_id and waited % 15 == 0:
            jobs.append_trace(job_id, "tool", "still running", f"{label or '等待桥接'}已 {waited}s")
        if waited >= stall:
            if recover:
                _cancel_stale_runs(agent_id=agent_id, extra_run=run)
                recover_bridge(job_id)
                raise TimeoutError(STALL_ZH)
            raise TimeoutError(SEND_ZH)
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _watched_send(agent: Any, payload: Any, send_opts: Any, job_id: str) -> Any:
    """Wait longer for ``send``; do not tear down the bridge if it is just slow."""
    aid = _agent_id_of(agent)

    def _send() -> Any:
        return agent.send(payload, send_opts)

    try:
        return _call_watched(
            _send,
            job_id=job_id,
            label="send",
            agent_id=aid,
            stall_s=_send_s(),
            recover=False,
        )
    except TimeoutError:
        leftover = _find_active_run(aid)
        if leftover is not None and _is_live_run(leftover):
            if job_id:
                jobs.append_trace(job_id, "tool", "attach", "发送偏慢，已接到已启动的任务")
            return leftover
        raise TimeoutError(SEND_ZH)


def _client_kw() -> dict[str, Any]:
    client = sdk_client.ensure_client()
    return {"client": client} if client is not None else {}


def _resume_or_create(
    agent_cls: Any,
    cid: str,
    opts: Any,
    job_id: str,
    conversation_id: str = "",
) -> tuple[Any, bool]:
    cached = sdk_client.peek_agent(conversation_id=conversation_id, sdk_id=cid)
    if cached is not None:
        reload = getattr(cached, "reload", None)
        if callable(reload):
            try:
                reload()
            except Exception:
                logger.warning("cached Agent.reload failed id=%s", cid, exc_info=True)
        return cached, True

    extra = _client_kw()
    agent = None
    if cid:
        try:
            def _resume() -> Any:
                try:
                    return agent_cls.resume(cid, opts, **extra)
                except TypeError:
                    return agent_cls.resume(cid, **extra) if extra else agent_cls.resume(cid)

            agent = _call_watched(_resume, job_id=job_id, label="resume", agent_id=cid)
            reload = getattr(agent, "reload", None)
            if callable(reload):
                try:
                    reload()
                except Exception:
                    logger.warning("Agent.reload failed id=%s", cid, exc_info=True)
            sdk_client.remember_agent(conversation_id=conversation_id, sdk_id=cid, agent=agent)
            return agent, True
        except jobs.JobCancelled:
            raise
        except Exception as exc:
            logger.warning("Agent.resume failed id=%s err=%s", cid, exc)
            agent = None
    agent = _call_watched(
        lambda: agent_cls.create(opts, **extra) if extra else agent_cls.create(opts),
        job_id=job_id,
        label="create",
    )
    sdk_client.remember_agent(
        conversation_id=conversation_id,
        sdk_id=_agent_id_of(agent),
        agent=agent,
    )
    return agent, False


def _stream_watched(run: Any, job_id: str, deadline: float | None = None) -> None:
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
    started = time.monotonic()
    last_beat = started
    cap = deadline if deadline is not None else started + _eval_timeout_s()
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
            if now >= cap:
                _cancel_run(run)
                raise TimeoutError(
                    f"Cursor 任务超时，超过 {int(_eval_timeout_s())} 秒仍没有最终回答"
                )
            continue
        if kind == "msg":
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
            if is_detached_run(payload):
                raise payload
            if is_bridge_error(payload) or isinstance(payload, TimeoutError):
                raise _fail(payload)
        elif kind == "done":
            return


def _agent_id_of(agent: Any, result: Any = None, fallback: str = "") -> str:
    for src in (agent, result):
        if src is None:
            continue
        for name in ("agent_id", "id"):
            val = getattr(src, name, None)
            if val and str(val) not in ("none", "local"):
                return str(val)
    return (fallback or "").strip()


def _bind_live_agent(conversation_id: str, job_id: str, agent: Any, *, resumed: bool, run: Any = None) -> str:
    agent_id = _agent_id_of(agent)
    if conversation_id and agent_id:
        conversations.bind_sdk(conversation_id, agent_id)
    if agent is not None:
        sdk_client.remember_agent(conversation_id=conversation_id, sdk_id=agent_id, agent=agent)
    if job_id and agent_id:
        run_id = ""
        if run is not None:
            run_id = str(getattr(run, "id", None) or getattr(run, "run_id", None) or "")
        jobs.patch_result(job_id, agent_id=agent_id, run_id=run_id, resumed=resumed)
    return agent_id


def _send_or_attach(agent: Any, payload: Any, send_opts: Any, job_id: str) -> Any:
    aid = _agent_id_of(agent)
    leftover = _find_active_run(aid)
    if leftover is not None and _is_live_run(leftover):
        if job_id:
            jobs.append_trace(job_id, "tool", "attach", "上一轮还在跑，已接到前台")
        return leftover
    if leftover is not None:
        if job_id:
            jobs.append_trace(job_id, "tool", "sweep", "上一轮是残留任务，无法接到前台，已清理")
        _dispose_dead_run(aid, leftover)
        _wait_until_idle(aid, job_id=job_id)

    try:
        return _watched_send(agent, payload, send_opts, job_id)
    except Exception as exc:
        if not (is_agent_busy(exc) or is_detached_run(exc)):
            raise
        leftover = _find_active_run(aid)
        if leftover is not None and _is_live_run(leftover):
            if job_id:
                jobs.append_trace(job_id, "tool", "attach", "上一轮还在跑，已接到前台")
            return leftover
        if job_id:
            jobs.append_trace(job_id, "tool", "sweep", "上一轮残留任务挡路，已清理后重开")
        _dispose_dead_run(aid, leftover)
        _wait_until_idle(aid, job_id=job_id)
        return _watched_send(agent, payload, _force_send_opts(send_opts), job_id)


def _prompt_sdk(
    prompt: str,
    conversation_id: str = "",
    job_id: str = "",
    mode: str = "agent",
    model: str = "",
    model_params: list[dict[str, str]] | None = None,
    images: list[dict[str, Any]] | None = None,
    resume_id: str = "",
    followup: str = "",
) -> tuple[str, str]:
    from app.cursor_auth import assert_cursor_ready, key_is_live

    wait_send_slot(job_id)
    try:
        assert_cursor_ready()
        bind_live_sdk_job(job_id)
        resume = _breaker_gate(resume_id, job_id)

        def _once() -> tuple[str, str]:
            return _prompt_sdk_once(
                prompt,
                conversation_id=conversation_id,
                job_id=job_id,
                mode=mode,
                model=model,
                model_params=model_params,
                images=images,
                resume_id=resume,
                followup=followup,
            )

        try:
            out = _once()
        except Exception as exc:
            if not is_auth_text(exc):
                if is_dead_thread(exc):
                    transport_breaker.trip(resume)
                    conversations.retire_sdk_id(resume)
                    sdk_client.drop_agent(conversation_id=conversation_id, sdk_id=resume)
                elif is_connect_stall(exc) or is_stream_stall(exc):
                    transport_breaker.note_failure(resume)
                raise
            # Vendor prose said auth. Ask the cloud before believing it: Cursor
            # confirmed this same text covers an expired exchanged token, and
            # that only a fresh bridge process clears it — rewriting auth.json
            # cannot, because this host authenticates by API key.
            if not key_is_live():
                raise RuntimeError(AUTH_ZH) from exc
            transport_breaker.note_failure(resume)
            if job_id:
                jobs.append_trace(
                    job_id,
                    "tool",
                    "token retry",
                    "钥匙仍有效，是云端令牌过期；重开本地桥后接回同一条对话",
                )
            recover_bridge(job_id)
            out = _once()
        transport_breaker.note_success(out[1] or resume)
        return out
    finally:
        bind_live_sdk_job("")
        release_send_slot(job_id)


def _local_options(local_cls: Any, local_kw: dict[str, Any]) -> Any:
    """Build ``AgentOptions.local``, asking for HTTP/1.1 on the agent stream.

    Agent runs ride a long-lived HTTP/2 bidirectional stream, and inside a
    container that stream gets silently dropped by NAT / proxy idle timeouts —
    which is what surfaces as "Connection stalled repeatedly". Cursor's
    TypeScript surface exposes ``local.useHttp1ForAgent`` for exactly this; the
    Python dataclass has no such field, but the SDK passes unknown mapping keys
    straight through to the bridge, whose schema does accept it. So take the
    mapping form only when the extra key is actually wanted, and keep the
    dataclass path untouched otherwise.
    """
    if not getattr(settings, "cursor_use_http1", False):
        return local_cls(**local_kw)
    return {**local_kw, "useHttp1ForAgent": True}


def _breaker_gate(resume_id: str, job_id: str) -> str:
    """Drop a resume target that has failed to connect ``LIMIT`` times in a row.

    Resuming a thread whose transport stays dead just reproduces the failure, so
    at the cap we restart the bridge and open a fresh thread instead. The
    checkpoint still carries the work forward, so nothing is lost.
    """
    resume = (resume_id or "").strip()
    if not resume or not transport_breaker.is_tripped(resume):
        return resume
    if job_id:
        jobs.append_trace(
            job_id,
            "tool",
            "breaker",
            f"这条 Cursor 对话连续 {transport_breaker.LIMIT} 次接不上，改开新线程继续",
        )
    transport_breaker.reset(resume)
    sdk_client.drop_agent(sdk_id=resume)
    recover_bridge(job_id)
    return ""


def _prompt_sdk_once(
    prompt: str,
    conversation_id: str = "",
    job_id: str = "",
    mode: str = "agent",
    model: str = "",
    model_params: list[dict[str, str]] | None = None,
    images: list[dict[str, Any]] | None = None,
    resume_id: str = "",
    followup: str = "",
) -> tuple[str, str]:
    from app.cursor_auth import live_api_key

    key = live_api_key()
    prepare_sdk_turn(job_id)
    from cursor_sdk import Agent, AgentOptions, LocalAgentOptions, SendOptions

    mcp = mcp_servers_for_sdk()
    tools = custom_tools_for_sdk(mode)
    local_kw: dict[str, Any] = {
        "cwd": str(settings.home),
        "setting_sources": ["project", "plugins"],
    }
    if tools:
        local_kw["custom_tools"] = tools
    local_opts: Any = _local_options(LocalAgentOptions, local_kw)
    chosen = (model or default_model()).strip() or default_model()
    selection = model_selection(chosen, model_params or default_model_params())
    sdk_mode = _sdk_mode(mode)
    opt_kw: dict[str, Any] = {
        "api_key": key,
        "model": selection,
        "mcp_servers": mcp or None,
        "local": local_opts,
    }
    try:
        opts = AgentOptions(**opt_kw, mode=sdk_mode)
    except TypeError:
        opts = AgentOptions(**opt_kw)
    try:
        send_opts = SendOptions(model=selection, mode=sdk_mode)
    except TypeError:
        send_opts = SendOptions(model=selection)

    cid = (resume_id or "").strip()
    our_cid = (conversation_id or "").strip()
    agent = None
    run = None
    resumed = False
    last_exc: BaseException | None = None
    for attempt in range(2):
        _raise_if_cancelled(job_id)
        try:
            if job_id:
                title = "正在连接 Cursor" if attempt == 0 else "清桥后重试"
                jobs.append_trace(job_id, "tool", "connect", title)
            agent, resumed = _resume_or_create(
                Agent, cid, opts, job_id, conversation_id=our_cid
            )
            _bind_live_agent(our_cid, job_id, agent, resumed=resumed)
            if job_id:
                # A first turn has nothing to resume, so saying the session
                # "could not be reached" there is a false alarm of its own.
                if resumed:
                    detail = "接上同一条 Cursor 对话"
                elif not cid:
                    detail = "首轮，新开一条 Cursor 对话"
                else:
                    detail = "上一条 Cursor 对话接不上，带着断点重开"
                jobs.append_trace(
                    job_id, "tool", "resume" if resumed else "new thread", detail
                )
            text = followup if resumed and (followup or "").strip() else prompt
            run = _send_or_attach(agent, _send_message(text, images), send_opts, job_id)
            _bind_live_agent(our_cid, job_id, agent, resumed=resumed, run=run)
            last_exc = None
            break
        except jobs.JobCancelled:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt == 0 and is_bridge_error(exc):
                if job_id:
                    jobs.append_trace(job_id, "tool", "bridge retry", humanize_sdk_error(exc))
                recover_bridge(job_id)
                agent = None
                run = None
                continue
            raise _fail(exc) from exc
    if last_exc is not None:
        raise _fail(last_exc) from last_exc
    if run is None or agent is None:
        raise RuntimeError(STALL_ZH)

    try:
        result = _watch_run(run, job_id)
    except (jobs.JobCancelled, HookDenied):
        raise
    except Exception as exc:
        if not is_detached_run(exc):
            raise
        if job_id:
            jobs.append_trace(job_id, "tool", "sweep", "接到的是残留句柄，无法挂前台，已清理后重开")
        _dispose_dead_run(_agent_id_of(agent), run)
        _wait_until_idle(_agent_id_of(agent), job_id=job_id)
        run = _watched_send(
            agent,
            _send_message(text, images),
            _force_send_opts(send_opts),
            job_id,
        )
        _bind_live_agent(our_cid, job_id, agent, resumed=resumed, run=run)
        result = _watch_run(run, job_id)
    status = getattr(result, "status", None)
    if str(status or "").lower() in ("cancelled", "canceled"):
        raise jobs.JobCancelled()
    if status == "error":
        traces = []
        if job_id:
            row = jobs.get_job(job_id)
            traces = list((row or {}).get("trace") or [])
        raise RuntimeError(humanize_run_fail(result, traces))
    text = getattr(result, "result", None) or getattr(result, "text", None)
    if callable(text):
        text = text()
    agent_id = _bind_live_agent(our_cid, job_id, agent, resumed=resumed, run=run) or _agent_id_of(
        agent, result, cid
    )
    return str(text or ""), agent_id


def _watch_run(run: Any, job_id: str) -> Any:
    if job_id:
        jobs.attach_run(job_id, run)
        if jobs.is_cancelled(job_id):
            _cancel_run(run)
            raise jobs.JobCancelled()
    deadline = time.monotonic() + _eval_timeout_s()
    try:
        _stream_watched(run, job_id, deadline=deadline)
    except (jobs.JobCancelled, HookDenied):
        raise
    except Exception as exc:
        if is_detached_run(exc) or is_bridge_error(exc) or isinstance(exc, TimeoutError):
            raise
        logger.warning("sdk stream failed: %s", exc)
        if job_id:
            jobs.append_trace(job_id, "tool", "stream interrupted", humanize_sdk_error(exc)[:800])
    if job_id and jobs.is_cancelled(job_id):
        raise jobs.JobCancelled()
    return _wait_run(run, job_id, deadline=deadline)


def _wait_run(run: Any, job_id: str, deadline: float | None = None) -> Any:
    """Block on ``run.wait`` but keep the dock alive if the stream already ended."""
    timeout_s = _eval_timeout_s()
    cap = deadline if deadline is not None else time.monotonic() + timeout_s
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
    started = time.monotonic()
    last_beat = started
    while not done.wait(1):
        now = time.monotonic()
        if job_id and jobs.is_cancelled(job_id):
            _cancel_run(run)
            raise jobs.JobCancelled()
        if job_id and now - last_beat >= 15:
            jobs.append_trace(
                job_id,
                "tool",
                "still running",
                f"已等待 {int(now - started)}s，还没有最终回答",
            )
            last_beat = now
        if now >= cap:
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
            jobs.append_trace(job_id, "think", "思考", text[:4000])
        return
    if typ == "tool_call":
        name = str(getattr(message, "name", "") or "tool")
        status = str(getattr(message, "status", "") or "")
        args = getattr(message, "args", None)
        kind = "search" if "search" in name else "open_pdf" if "pdf" in name else "tool"
        limit = 65536 if "plan" in name.lower() else 2000
        jobs.append_trace(job_id, kind, f"{name} {status}".strip(), _short(args, limit))
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


def _run_fail_text(result: Any, job_id: str = "") -> str:
    traces = []
    if job_id:
        row = jobs.get_job(job_id)
        traces = list((row or {}).get("trace") or [])
    return humanize_run_fail(result, traces)


def _short(value: Any, limit: int = 2000) -> str:
    if value is None:
        return ""
    cap = max(1, int(limit))
    if isinstance(value, str):
        return value[:cap]
    try:
        import json

        return json.dumps(value, ensure_ascii=False)[:cap]
    except TypeError:
        return str(value)[:cap]


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
