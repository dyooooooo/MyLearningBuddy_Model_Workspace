"""Buddy Copilot 宿主机编排服务入口。

只绑 127.0.0.1:8200，经 c2-gateway `/api/v1/studio/copilot/*` 代理暴露给 Studio。
消息实时性：SSE（Server-Sent Events）事件流为主，`/messages` 轮询接口保留作回退。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from .agent_env import verify_resource_paths
from .bridge_cleanup import close_sdk_bridge, sweep_orphan_bridges
from .config import get_settings
from .continue_intent import is_continue_request
from .loop import LoopEngine
from .permissions import load_permissions
from .schemas import ApprovalDecisionRequest, CreateSessionRequest, UserMessageRequest
from .scenarios import build_task_context
from .session_sweep import sweep_zombie_sessions
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("copilot.main")

settings = get_settings()
store = Store(settings.db_path)
engine = LoopEngine(
    settings=settings,
    store=store,
    task_context_builder=build_task_context(settings),
)

def _sweep_zombie_sessions() -> None:
    """启动时清扫僵尸会话：上次进程退出时线程已不在，但状态仍停在 planning/executing。
    awaiting_user（质检门闸等人）是合法停靠态，不扫 —— 用户刷新后发消息即可 resume。
    执行中残留停到 awaiting_user（不是 failed），用户点「继续」可续。"""
    swept = sweep_zombie_sessions(store, is_running=engine.is_running)
    if swept:
        log.info("启动清扫：%d 个僵尸会话已停靠为 awaiting_user（可续跑）", swept)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 启动：清扫上次异常退出残留的 SDK 桥，再清僵尸会话
    try:
        n = sweep_orphan_bridges()
        log.info("bridge 清扫（startup）：%d 个孤儿进程", n)
    except Exception:  # noqa: BLE001
        log.exception("bridge 清扫失败（startup）")
    _sweep_zombie_sessions()
    yield
    # 退出：关 Agent → 关 SDK 默认客户端 → 再扫一轮孤儿兜底
    try:
        engine.shutdown()
    except Exception:  # noqa: BLE001
        log.exception("engine.shutdown 失败")
    try:
        close_sdk_bridge()
    except Exception:  # noqa: BLE001
        log.exception("close_sdk_bridge 失败")
    try:
        n = sweep_orphan_bridges()
        log.info("bridge 清扫（shutdown）：%d 个孤儿进程", n)
    except Exception:  # noqa: BLE001
        log.exception("bridge 清扫失败（shutdown）")


app = FastAPI(title="Buddy Copilot", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request, call_next):
    """除 health 外全部要求 Bearer ADMIN_TOKEN（与 buddy-api 同模式）。

    服务需绑 0.0.0.0 才能被 c2-gateway 经 host.docker.internal 访问，
    因此必须带令牌，防止 LAN 直连。
    """
    from fastapi.responses import JSONResponse

    path = request.url.path
    if path == "/api/v1/copilot/health" or path in ("/docs", "/openapi.json", "/redoc"):
        return await call_next(request)
    token = settings.resolve_admin_token()
    got = request.headers.get("authorization", "")
    if not token or got != f"Bearer {token}":
        return JSONResponse(status_code=401, content={"detail": "unauthorized"})
    return await call_next(request)


@app.get("/api/v1/copilot/health")
def health() -> dict:
    problems: list[str] = []
    if not settings.cursor_api_key:
        problems.append("CURSOR_API_KEY 未配置（无法启动 Cursor Agent）")
    if not settings.resolve_admin_token():
        problems.append("ADMIN_TOKEN 未配置且 learning-buddy-c2/.env 不可读")
    stale = verify_resource_paths(settings.agent_env_dir, settings.workspace_root)
    if stale:
        problems.append(f"资源地图路径失效: {stale[:10]}")
    env_dir = settings.agent_env_dir
    try:
        load_permissions(env_dir)
    except Exception as e:  # noqa: BLE001
        problems.append(f"permissions.yaml 加载失败: {e}")
    for rel in (
        "resources.yaml",
        "pinning.yaml",
        "permissions.yaml",
        "rules/00-copilot-core.md",
        "rules/40-layout-rules-first.md",
        "skills/edition-tuning.md",
        "skills/page-diagnosis.md",
        "skills/pattern-abstract.md",
        "context/editions.md",
        "context/import-pipeline.md",
    ):
        if not (env_dir / rel).is_file():
            problems.append(f"agent-env 缺失: {rel}")
    try:
        from .mcp_server import mcp as _mcp  # noqa: F401
    except Exception as e:  # noqa: BLE001
        problems.append(f"MCP 服务模块不可导入: {e}")
    return {
        "status": "degraded" if problems else "ok",
        "problems": problems,
        "model": settings.cursor_model,
        "mcp": "buddy-copilot",
        "workspace": str(settings.workspace_root),
    }


_models_cache: dict = {"ts": 0.0, "models": []}


@app.get("/api/v1/copilot/models")
def list_models() -> dict:
    """Cursor SDK 可用模型列表（缓存 10 分钟）。前端下拉用；「自动」由前端补第一项。"""
    import time as _time

    if not settings.cursor_api_key:
        raise HTTPException(503, "CURSOR_API_KEY 未配置")
    now = _time.time()
    if now - _models_cache["ts"] > 600 or not _models_cache["models"]:
        from cursor_sdk import Cursor

        try:
            items = Cursor.models.list(api_key=settings.cursor_api_key)
        except Exception as e:  # noqa: BLE001
            log.warning("models.list failed: %s", e)
            raise HTTPException(502, f"获取模型列表失败: {e}") from e
        _models_cache["models"] = [
            {"id": m.id, "label": m.display_name or m.id}
            for m in items
            if m.id and m.id != "default"
        ]
        _models_cache["ts"] = now
    return {"models": _models_cache["models"], "default": settings.cursor_model}


_IP_KINDS = frozenset({"ip_book", "ip_triage", "ip_split"})


def _normalize_session_kind(kind: str) -> str:
    """旧 ip_triage / ip_split 归一为 ip_book（一书指纹一对话）。"""
    if kind in ("ip_triage", "ip_split", "ip_book"):
        return "ip_book"
    return kind


def _ensure_ip_intent(kind_raw: str, payload: dict) -> dict:
    """写入 payload.intent；兼容旧前端只传 kind / focus / process_all / ip_id。"""
    pl = dict(payload or {})
    intent = str(pl.get("intent") or "").strip().lower()
    if intent in ("triage", "split_all", "split_one", "aggregate"):
        pl["intent"] = intent
        return pl
    if str(pl.get("focus") or "").strip().lower() == "aggregate":
        pl["intent"] = "aggregate"
    elif kind_raw == "ip_split":
        pl["intent"] = (
            "split_all" if pl.get("process_all") else "split_one"
        )
    elif pl.get("ip_id") and not pl.get("process_all"):
        pl["intent"] = "split_one"
    elif pl.get("process_all") and kind_raw == "ip_book":
        # 新 kind 未带 intent：默认整理（全部拆分须显式 intent=split_all）
        pl["intent"] = "triage"
    else:
        pl["intent"] = "triage"
    return pl


def _normalize_aggregate_payload(payload: dict) -> dict:
    """全书聚合 vs 单簇互斥：有 member_ids 且未声明 process_all → 单簇。"""
    pl = dict(payload or {})
    if str(pl.get("intent") or "").strip().lower() != "aggregate":
        return pl
    members = [
        str(x).strip() for x in (pl.get("member_ids") or []) if str(x).strip()
    ]
    # 显式 process_all=true → 全书；否则有 member_ids → 单簇
    if pl.get("process_all") is True or (
        bool(pl.get("process_all")) and not members
    ):
        pl["process_all"] = True
        for k in (
            "member_ids",
            "aggregate_group_id",
            "aggregate_cluster_index",
            "aggregate_cluster_label",
        ):
            pl.pop(k, None)
        return pl
    if members:
        pl["process_all"] = False
        pl["member_ids"] = members
        idx = pl.get("aggregate_cluster_index")
        label = str(pl.get("aggregate_cluster_label") or "").strip()
        if not label and idx not in (None, ""):
            try:
                pl["aggregate_cluster_label"] = f"簇#{int(idx)}"
            except (TypeError, ValueError):
                pl["aggregate_cluster_label"] = f"簇#{idx}"
        return pl
    pl["process_all"] = True
    return pl


def _ip_intent_user_text(payload: dict) -> str:
    intent = str(payload.get("intent") or "triage").strip().lower()
    ip_id = str(payload.get("ip_id") or "").strip()
    if intent == "aggregate":
        pl = _normalize_aggregate_payload(payload)
        members = [
            str(x).strip() for x in (pl.get("member_ids") or []) if str(x).strip()
        ]
        label = str(pl.get("aggregate_cluster_label") or "").strip()
        if not label and pl.get("aggregate_cluster_index") not in (None, ""):
            label = f"簇#{pl.get('aggregate_cluster_index')}"
        gid = str(pl.get("aggregate_group_id") or "").strip()
        if members and not pl.get("process_all"):
            lab = label or (f"组 {gid[:8]}" if gid else "指定簇")
            return (
                f"【意图：处理聚合 · 仅{lab}】"
                f"aggregate_cluster_label={lab}；"
                f"aggregate_group_id={gid or '（无）'}；"
                f"member_ids={members}。"
                f"只裁决{lab}这一簇的对立，向我说明 keep/delete，确认后再 aggregate_resolve。"
                "**禁止**处理其它簇；**不要**问我要处理全部还是一个——"
                f"本次范围已定为{lab}。"
            )
        return (
            "【意图：处理全部聚合】请用 conflict_hints / 列表找出本书全部对立簇，"
            "按簇#1、簇#2…逐簇向我说明 keep/delete，确认后再 aggregate_resolve。"
        )
    if intent == "split_all":
        return (
            "【意图：全部拆分】请用 ip.list_for_split 清空该书全部 needs_split；"
            "逐条 propose→apply，直到 remaining_needs_split=0。"
        )
    if intent == "split_one":
        return (
            f"【意图：拆分本条】ip_id={ip_id or '（缺失）'}。"
            "请 propose 提案，向我展示后确认再 split_apply。"
        )
    return (
        "【意图：整理】请用 ip.list_for_triage 清空该书全部 needs_triage；"
        "每批 triage_apply 后继续 list，直到 remaining_needs_triage=0。"
    )


def _kick_ip_session(sid: str, payload: dict) -> None:
    """复用会话时按 intent 开跑：运行中则投递用户消息，否则 resume。"""
    text = _ip_intent_user_text(payload)
    if engine.is_running(sid):
        try:
            engine.post_user_message(sid, text)
            return
        except KeyError:
            pass
    engine.resume_session(sid, text)


@app.post("/api/v1/copilot/sessions")
def create_session(req: CreateSessionRequest) -> dict:
    if not settings.cursor_api_key:
        raise HTTPException(503, "CURSOR_API_KEY 未配置，无法创建会话")
    kind_raw = req.kind
    kind = _normalize_session_kind(kind_raw)
    payload = dict(req.payload or {})
    if kind == "ip_book":
        payload = _ensure_ip_intent(kind_raw, payload)
        payload = _normalize_aggregate_payload(payload)
    jid = Store._job_id_from_payload(payload)
    # 导入任务 ↔ AI 对话 1:1：已有绑定则复用，不新建平行会话
    if jid and kind == "import_advice":
        existing = store.find_session_id_by_import_job(jid)
        if existing:
            store.audit(
                existing,
                "user",
                "session_reused_for_job",
                {"import_job_id": jid, "kind": kind},
            )
            return {
                "session_id": existing,
                "resumed": True,
                "import_job_id": jid,
            }
    # 一书指纹 ↔ AI 对话 1:1
    if kind == "ip_book":
        fp = str(payload.get("fingerprint_hash") or "").strip()
        if fp:
            existing = store.find_session_id_by_fingerprint(fp, kind="ip_book")
            if existing:
                sess = store.get_session(existing) or {}
                # 单簇/全书字段先清再并入，避免上一轮 process_all/member_ids 残留
                merged = dict(sess.get("payload") or {})
                for k in (
                    "member_ids",
                    "aggregate_group_id",
                    "aggregate_cluster_index",
                    "aggregate_cluster_label",
                    "process_all",
                    "user_goal",
                    "focus",
                ):
                    merged.pop(k, None)
                merged.update(payload)
                merged = _ensure_ip_intent(kind_raw, merged)
                merged = _normalize_aggregate_payload(merged)
                store.update_session(existing, payload=merged, kind="ip_book")
                store.audit(
                    existing,
                    "user",
                    "session_reused_for_fingerprint",
                    {
                        "fingerprint_hash": fp,
                        "intent": merged.get("intent"),
                        "kind_raw": kind_raw,
                        "aggregate_cluster_label": merged.get(
                            "aggregate_cluster_label"
                        ),
                        "process_all": merged.get("process_all"),
                    },
                )
                if req.auto_start:
                    try:
                        _kick_ip_session(existing, merged)
                    except Exception:  # noqa: BLE001
                        log.exception("ip_book reuse kick failed sid=%s", existing)
                return {
                    "session_id": existing,
                    "resumed": True,
                    "import_job_id": jid or None,
                    "auto_start": bool(req.auto_start),
                }
    sid = store.create_session(kind, payload, model=req.model.strip())
    if jid:
        removed = store.bind_import_job(sid, jid)
        for oid in removed:
            try:
                engine.cancel(oid)
            except Exception:  # noqa: BLE001
                pass
    store.audit(
        sid,
        "user",
        "session_created",
        {
            "kind": kind,
            "kind_raw": kind_raw,
            "payload": payload,
            "model": req.model.strip() or "auto",
            "import_job_id": jid or "",
            "auto_start": bool(req.auto_start),
        },
    )
    if req.auto_start:
        engine.start(sid)
    else:
        # 已有导入任务的空绑定：等人聚焦改页，勿自动跑全书编排
        store.update_session(sid, status="awaiting_user")
        try:
            store.add_message(
                sid,
                "system",
                {
                    "text": (
                        "已与导入任务绑定（空对话）。"
                        "可在中栏聚焦页/块后在此发送修改要求；不会自动重新全书导入。"
                    )
                },
            )
        except Exception:  # noqa: BLE001
            log.warning("idle bind welcome message failed sid=%s", sid, exc_info=True)
    return {
        "session_id": sid,
        "import_job_id": jid or None,
        "resumed": False,
        "auto_start": bool(req.auto_start),
    }


@app.get("/api/v1/copilot/sessions")
def list_sessions(import_job_id: str = "") -> dict:
    return {"sessions": store.list_sessions(import_job_id=import_job_id)}


@app.get("/api/v1/copilot/sessions/by-fingerprint/{fingerprint_hash}")
def find_session_by_fingerprint(
    fingerprint_hash: str, kind: str = "ip_book"
) -> dict:
    """按书指纹找回改进点 AI 会话（刷新后无 localStorage 时用）。

    默认 kind=ip_book；命中旧 ip_triage/ip_split 时升级为 ip_book。
    """
    fp = (fingerprint_hash or "").strip()
    if not fp:
        raise HTTPException(400, "fingerprint_hash 必填")
    kind_q = (kind or "").strip() or "ip_book"
    if kind_q in _IP_KINDS:
        kind_q = "ip_book"
    sid = store.find_session_id_by_fingerprint(fp, kind=kind_q)
    if not sid:
        return {"session": None}
    s = store.get_session(sid)
    if not s:
        return {"session": None}
    if str(s.get("kind") or "") in ("ip_triage", "ip_split"):
        try:
            store.update_session(sid, kind="ip_book")
            s = store.get_session(sid) or s
        except Exception:  # noqa: BLE001
            log.warning("upgrade session kind to ip_book failed sid=%s", sid, exc_info=True)
    return {
        "session": {
            "id": s["id"],
            "kind": s.get("kind"),
            "status": s.get("status"),
            "import_job_id": s.get("import_job_id"),
            "label": s.get("label"),
            "created_at": s.get("created_at"),
            "updated_at": s.get("updated_at"),
        }
    }


@app.get("/api/v1/copilot/sessions/{sid}")
def get_session(sid: str) -> dict:
    s = store.get_session(sid)
    if not s:
        raise HTTPException(404, "会话不存在")
    s["running"] = engine.is_running(sid)
    s["pending_fs"] = engine.has_pending_checkpoint(sid)
    return s


def _purge_session(sid: str) -> bool:
    """取消运行中线程并物理删除会话（含消息）。"""
    try:
        engine.discard_files(sid, reason="删除会话")
    except Exception:  # noqa: BLE001
        log.exception("discard before delete failed sid=%s", sid)
    try:
        if engine.is_running(sid):
            engine.cancel(sid, discard_files=True)
    except Exception:  # noqa: BLE001
        log.exception("cancel before delete failed sid=%s", sid)
    # 关闭 Agent 引用
    try:
        agent = engine._agents.pop(sid, None)  # noqa: SLF001
        if agent:
            agent.close()
    except Exception:  # noqa: BLE001
        pass
    ok = store.delete_session(sid)
    if ok:
        # 附件目录
        try:
            import shutil

            att = settings.db_path.parent / "attachments" / sid
            if att.is_dir():
                shutil.rmtree(att, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
        store.audit(None, "system", "session_deleted", {"session_id": sid})
    return ok


@app.delete("/api/v1/copilot/sessions/{sid}")
def delete_session(sid: str) -> dict:
    if not store.get_session(sid):
        raise HTTPException(404, "会话不存在")
    _purge_session(sid)
    return {"ok": True, "deleted": [sid]}


@app.delete("/api/v1/copilot/sessions/by-import-job/{job_id}")
def delete_sessions_by_import_job(job_id: str) -> dict:
    """导入任务删除时级联：删掉绑定该 job 的全部 AI 对话。"""
    jid = (job_id or "").strip()
    if not jid:
        raise HTTPException(400, "job_id 必填")
    ids = [s["id"] for s in store.list_sessions(limit=100, import_job_id=jid)]
    deleted: list[str] = []
    for sid in ids:
        if _purge_session(sid):
            deleted.append(sid)
    return {"ok": True, "import_job_id": jid, "deleted": deleted}


@app.get("/api/v1/copilot/sessions/{sid}/messages")
def get_messages(sid: str, after_seq: int = 0) -> dict:
    s = store.get_session(sid)
    if not s:
        raise HTTPException(404, "会话不存在")
    return {
        "status": s["status"],
        "messages": store.get_messages(sid, after_seq),
    }


_TERMINAL_STATUS = {"done", "failed", "cancelled"}


@app.get("/api/v1/copilot/sessions/{sid}/events")
async def stream_events(sid: str, after_seq: int = 0) -> StreamingResponse:
    """SSE（Server-Sent Events）事件流：新消息与状态实时推送，替代前端 2 秒轮询。

    事件格式：`event: batch` + data={"status", "messages"}；15 秒心跳注释保活。
    会话进入终态且消息追平后发 `event: end` 并关闭。
    """
    if not store.get_session(sid):
        raise HTTPException(404, "会话不存在")

    async def gen():
        seq = after_seq
        last_status = ""
        last_beat = time.monotonic()
        while True:
            s = store.get_session(sid)
            if not s:
                break
            msgs = store.get_messages(sid, seq)
            status = s["status"]
            if msgs or status != last_status:
                if msgs:
                    seq = msgs[-1]["seq"]
                last_status = status
                payload = json.dumps(
                    {"status": status, "messages": msgs}, ensure_ascii=False, default=str
                )
                yield f"event: batch\ndata: {payload}\n\n"
                last_beat = time.monotonic()
            if status in _TERMINAL_STATUS and not msgs:
                yield "event: end\ndata: {}\n\n"
                break
            if time.monotonic() - last_beat > 15:
                yield ": keepalive\n\n"
                last_beat = time.monotonic()
            await asyncio.sleep(0.5)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/v1/copilot/sessions/{sid}/attachments")
async def upload_attachment(sid: str, file: UploadFile = File(...)) -> dict:
    """图片/文件附件：存到工作区内会话目录，返回绝对路径。

    Agent 走本地 runtime（cwd=工作区），可直接读取该路径查看图片。
    """
    if not store.get_session(sid):
        raise HTTPException(404, "会话不存在")
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "附件超过 20MB 上限")
    safe = re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", file.filename or "attachment.png")[-80:]
    att_dir = settings.db_path.parent / "attachments" / sid
    att_dir.mkdir(parents=True, exist_ok=True)
    dest = att_dir / f"{int(time.time() * 1000)}-{safe}"
    dest.write_bytes(data)
    store.audit(sid, "user", "attachment_uploaded", {"path": str(dest), "size": len(data)})
    return {"path": str(dest), "filename": safe, "size": len(data)}


@app.post("/api/v1/copilot/sessions/{sid}/messages")
def post_message(sid: str, req: UserMessageRequest) -> dict:
    s = store.get_session(sid)
    if not s:
        raise HTTPException(404, "会话不存在")
    if not engine.is_running(sid):
        # 运行时已退出但仍可续：
        # - awaiting_user：质检门闸等用户输入后线程已收尾（刷新页面最常见）
        # - 终态：有 agent_id 可 resume；说「继续」时即使缺 agent_id 也可新建接断点
        # - planning/executing 僵尸：仅「继续」可抢救
        status = str(s.get("status") or "")
        terminal = status in ("done", "failed", "cancelled")
        parked = status == "awaiting_user"
        want_continue = is_continue_request(req.text)
        if parked:
            can_resume = True
        elif terminal:
            can_resume = bool(s.get("agent_id")) or want_continue
        else:
            can_resume = want_continue
        if can_resume:
            try:
                engine.resume_session(sid, req.text)
            except KeyError:
                # 竞态：刚好已被另一条消息重启 → 走排队通道
                store.add_message(sid, "user", {"text": req.text})
                engine.post_user_message(sid, req.text)
            store.audit(sid, "user", "session_resumed", {"text": req.text[:200]})
            return {"ok": True, "resumed": True}
        raise HTTPException(
            409,
            "会话未在运行且无法自动恢复。"
            "请发送「继续」重连 Agent 并从断点接着做"
            + ("（当前缺 agent_id，会新建 Agent）" if not s.get("agent_id") else ""),
        )
    # awaiting_user 阶段由 loop 负责回显；其余状态先落库回显、执行中排队下一回合送达
    if s.get("status") != "awaiting_user":
        store.add_message(sid, "user", {"text": req.text})
    engine.post_user_message(sid, req.text)
    return {"ok": True}


@app.post("/api/v1/copilot/sessions/{sid}/approvals")
def decide_approval(sid: str, req: ApprovalDecisionRequest) -> dict:
    """批准决定直接写库（MCP 闸门与 loop 都轮询 store），事件仅用于唤醒 loop 线程。"""
    s = store.get_session(sid)
    if not s:
        raise HTTPException(404, "会话不存在")
    a = store.get_approval(req.approval_id)
    if not a or a["session_id"] != sid:
        raise HTTPException(404, "确认卡不存在")
    if a["status"] != "pending":
        raise HTTPException(409, f"确认卡已处理（{a['status']}）")
    store.decide_approval(req.approval_id, req.approved, req.note, remember=req.remember)
    store.audit(
        sid, "user", "approval_decision",
        {
            "approval_id": req.approval_id,
            "approved": req.approved,
            "note": req.note,
            "remember": req.remember,
        },
    )
    if req.note and a.get("action_key") != "brief_confirm":
        store.add_message(sid, "user", {"text": req.note})
    engine.notify_approval(sid)
    return {"ok": True}


@app.post("/api/v1/copilot/sessions/{sid}/cancel")
def cancel_session(sid: str, discard_files: bool = True) -> dict:
    # 线程可能已退出（awaiting_user 停靠态）：仍要落 cancelled，
    # 否则「停止」在界面上像没生效，且残余流程会把状态改回去。
    # discard_files 默认 True：停止 ≈ Cursor 放弃，回退本轮文件改动。
    engine.cancel(sid, discard_files=discard_files)
    store.audit(sid, "user", "session_cancelled", {"discard_files": discard_files})
    return {"ok": True, "discard_files": discard_files}


@app.post("/api/v1/copilot/sessions/{sid}/fs/keep")
def fs_keep(sid: str) -> dict:
    """保留本轮文件改动（释放检查点）。"""
    if not store.get_session(sid):
        raise HTTPException(404, "会话不存在")
    return engine.keep_files(sid)


@app.post("/api/v1/copilot/sessions/{sid}/fs/discard")
def fs_discard(sid: str) -> dict:
    """放弃本轮文件改动，回退到本轮执行前（类似 git / Cursor Undo）。"""
    if not store.get_session(sid):
        raise HTTPException(404, "会话不存在")
    return engine.discard_files(sid, reason="用户放弃改动")


@app.get("/api/v1/copilot/sessions/{sid}/audit")
def get_audit(sid: str) -> dict:
    return {"audit": store.get_audit(sid)}
