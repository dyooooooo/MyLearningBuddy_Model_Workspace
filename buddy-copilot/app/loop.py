"""自主运行监督器（Cursor SDK 连续对话模型）。

对齐官方 Agent → Run 语义（见 cursor.com/docs/sdk/python）：
- 一个会话对应一个持久 Agent；每条用户消息是一次 send()/Run。
- 确认卡是旁路闸门，不劫持普通聊天：澄清问答后卡仍有效；
  仅「同意执行 / 打回改方案」才改变卡状态。
- 断连恢复：Agent.resume + send(local.force=True) 清僵尸 run。

产品流程仍保留：简报 →（可追问）→ 确认 → 连续执行 → FinalReport；
blocked 时等用户输入后继续 send，直到 done / failed / 取消。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import traceback
from typing import Any, Callable

from .agent_env import build_context_pack
from .agent_runner import AgentRunError, SessionAgent, extract_final_report
from .bridge_cleanup import close_sdk_bridge, sweep_orphan_bridges
from .brief_intent import classify_brief_message
from .config import Settings
from .continue_intent import is_continue_request
from .next_steps import (
    looks_like_finalize_request,
    looks_like_refuse_principle,
    next_steps_message,
)
from .permissions import Permissions, load_permissions
from .layout_triad.triad import merged_to_page_plan_hints, run_triad
from .preview_batch import (
    PREVIEW_PAGES_PER_RUN,
    batch_instruction,
    detect_page_limit_from_messages,
    extract_page_plan_partial,
    final_import_instruction,
    merge_page_plans,
    page_batches,
    user_wants_full_preview,
)
from .schemas import FinalReport, PagePlanEntry
from .segment_state import (
    IMPORT_PAGES_PER_SEGMENT,
    advance_past_gate_batch,
    apply_qa_report_to_payload,
    extract_qa_report,
    fix_instruction,
    has_blocking_open_issues,
    has_layout_or_effective_ledger,
    import_batch_ready,
    imported_through,
    mark_issues_status,
    merge_open_issues,
    note_imported_through,
    note_qa_through,
    open_issue_pages,
    page_range_str,
    mentioned_open_pages,
    qa_instruction,
    qa_through,
    segment_batches,
    set_gate_batch,
    user_accepts_suggestions,
    user_confirms_effective,
    user_wants_skip_and_continue,
)
from .store import Store
from .tools.buddy_api import BuddyApiClient
from .tools.fs_guard import WorktreeSnapshot

log = logging.getLogger("copilot.loop")

_BRIEF_INSTRUCTION = (
    "\n\n请阅读以上环境与任务上下文，先产出一份**行动简报**（markdown，不要 json）：\n"
    "1. 你对问题的判断；2. 打算做什么（动哪些东西、走哪条通道）；3. 怎么验收。\n"
    "简报阶段只允许使用只读工具查证，**不要**执行任何修改类动作。"
    "简报会以确认卡呈现给用户，确认后你才进入连续自主执行。"
)

_EXECUTE_INSTRUCTION = (
    "简报已获用户确认。现在开始**连续自主执行**：\n"
    "- 开始执行时先调用 session.update_todos 建立任务清单（按简报拆步），"
    "每完成/跳过一步就更新一次状态（in_progress/done/skipped），让用户实时看到进度；\n"
    "- 自主调用工具完成任务；工具报错就地诊断、修复、重试（同一失败最多 3 次）；\n"
    "- 看预览图时每看完 3 张就 session.update_todos 打心跳（防空转误判）；\n"
    "- 修改文件前必须先 fs.request_edit 申请并获批；闸门工具会弹批准卡，被拒绝时按用户理由调整；\n"
    "- 完成前必须有验证证据（golden / page_diff / pytest / 重新拉数据核实）；\n"
    "- 结束时输出最终报告 ```json 块（status=done/blocked/failed + summary + evidence）。"
)

_NUDGE_INSTRUCTION = (
    "你尚未按收尾契约输出最终报告。请立即输出一个 ```json 块："
    '{"status": "done|blocked|failed", "summary": "...", "evidence": [...], '
    '"next_steps": "...", "question": "..."}，不要再调用工具。'
)


class _RoundHang(Exception):
    """一轮运行无响应（墙钟超限或长时间零输出）——SDK 流挂死的判定信号。"""


# 桥接 / 网络类瞬时故障：应自动重连重试，而不是立刻把会话标 failed
_RECOVERABLE_MARKERS = (
    "bridge request failed",
    "remoteprotocolerror",
    "incomplete chunked",
    "connection refused",
    "connection reset",
    "broken pipe",
    "peer closed connection",
    "connecterror",
    "networkerror",
    "temporarily unavailable",
    "server disconnected",
    # Cursor 长会话偶发：Resume 成功但 Send 立刻失败；需新建 Agent，不能 resume 同一条坏死上下文
    "internal: internal error",
    "internal error",
)


def _is_recoverable_agent_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(m in text for m in _RECOVERABLE_MARKERS)


def _needs_fresh_agent(exc: BaseException) -> bool:
    """这类错误重连仍 resume 旧 Agent 会立刻再炸，必须清空 agent_id 新建。"""
    text = str(exc).lower()
    return "internal error" in text or "internal: internal" in text


def _pdf_identity(minio_key: Any, label: Any = "") -> str:
    """PDF 身份标识：取文件名（去目录、去 .pdf、小写）。

    MinIO 键在 staging → 正式目录之间会换哈希前缀，只有文件名稳定，
    所以用文件名判断「是不是同一本书」。
    """
    for raw in (minio_key, label):
        s = str(raw or "").strip().replace("\\", "/")
        s = s.rsplit("/", 1)[-1].strip()
        if s.lower().endswith(".pdf"):
            s = s[:-4]
        if s:
            return s.lower()
    return ""


class SessionRuntime:
    """会话线程的运行时信道。"""

    def __init__(self) -> None:
        self.user_messages: queue.Queue[str] = queue.Queue()
        self.approval_event = threading.Event()
        self.cancelled = threading.Event()


class LoopEngine:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        task_context_builder: Callable[[str, dict[str, Any]], str],
    ) -> None:
        self.settings = settings
        self.store = store
        self.task_context_builder = task_context_builder
        self._runtimes: dict[str, SessionRuntime] = {}
        self._agents: dict[str, SessionAgent] = {}
        #  已被用户「停止」的会话：状态不再被残余流程改回 awaiting_user / executing
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    # ---------- 对外接口（由 FastAPI 调用） ----------

    def start(self, sid: str) -> None:
        rt = SessionRuntime()
        with self._lock:
            self._runtimes[sid] = rt
            self._cancelled.discard(sid)
        t = threading.Thread(target=self._run, args=(sid, rt), daemon=True, name=f"copilot-{sid}")
        t.start()

    def resume_session(self, sid: str, text: str) -> None:
        """终态（done/failed/cancelled）会话继续对话：Agent.resume 复用同一 Cursor Agent，
        进入新一轮连续执行（Cursor 式追问体验）。"""
        rt = SessionRuntime()
        with self._lock:
            if sid in self._runtimes:
                raise KeyError(f"session {sid} 已在运行中")
            self._runtimes[sid] = rt
            self._cancelled.discard(sid)  # 用户主动续聊：解除取消封锁
        t = threading.Thread(
            target=self._run_followup, args=(sid, rt, text), daemon=True, name=f"copilot-fu-{sid}"
        )
        t.start()

    def post_user_message(self, sid: str, text: str) -> None:
        rt = self._runtimes.get(sid)
        if rt is None:
            raise KeyError(f"session {sid} 不在运行中")
        rt.user_messages.put(text)

    def notify_approval(self, sid: str) -> None:
        """批准决定已写入 store（main.py），这里只负责唤醒等待线程。"""
        rt = self._runtimes.get(sid)
        if rt:
            rt.approval_event.set()

    def cancel(self, sid: str) -> None:
        with self._lock:
            self._cancelled.add(sid)
        rt = self._runtimes.get(sid)
        if rt:
            rt.cancelled.set()
            rt.user_messages.put("")  # 解除阻塞
            rt.approval_event.set()
        # 状态先落库：MCP 进程内的闸门/工具轮询按此中止
        self.store.update_session(sid, status="cancelled")
        agent = self._agents.get(sid)
        if agent:
            agent.cancel_current()

    def is_running(self, sid: str) -> bool:
        return sid in self._runtimes

    def shutdown(self) -> None:
        """进程退出前：取消运行中会话并关闭全部 Agent（尽量让 SDK 自己收桥）。"""
        with self._lock:
            sids = list(self._runtimes.keys())
            agents = list(self._agents.items())
        for sid in sids:
            try:
                self.cancel(sid)
            except Exception:  # noqa: BLE001
                log.exception("shutdown cancel %s failed", sid)
        for sid, agent in agents:
            try:
                agent.close()
            except Exception:  # noqa: BLE001
                log.exception("shutdown close agent %s failed", sid)
            self._agents.pop(sid, None)
        with self._lock:
            self._runtimes.clear()
            self._agents.clear()

    # ---------- 会话主循环 ----------

    def _say(self, sid: str, role: str, content: dict[str, Any]) -> None:
        self.store.add_message(sid, role, content)

    def _say_continue_cta(self, sid: str, text: str) -> None:
        """断连/失败后的系统提示，附带「继续」按钮（前端渲染 actions）。"""
        self._say(
            sid,
            "system",
            {
                "text": text,
                "actions": [{"id": "continue", "label": "继续", "send": "继续"}],
            },
        )

    def _say_next_steps(self, sid: str, preface: str = "") -> None:
        """停靠时强制给出状态化下一步选择（禁止 awaiting_user 无 actions）。"""
        if sid in self._cancelled:
            return
        sess = self.store.get_session(sid) or {}
        pl = dict(sess.get("payload") or {})
        self._say(sid, "system", next_steps_message(pl, preface=preface))

    def _status(self, sid: str, status: str) -> None:
        # 已取消的会话不得被后续流程改回 awaiting_user/executing（否则「停止」形同无效）
        if status != "cancelled" and sid in self._cancelled:
            log.info("忽略取消后的状态回写 sid=%s status=%s", sid, status)
            return
        self.store.update_session(sid, status=status)
        self._say(sid, "status", {"status": status})

    def _unfinished_pipeline(self, session: dict[str, Any]) -> str | None:
        """若有未完成的 MinerU 分段导入/质检，返回原因标签；否则 None。"""
        if session.get("kind") != "import_advice":
            return None
        payload = session.get("payload") or {}
        if not payload.get("minio_key"):
            return None
        if has_blocking_open_issues(payload):
            return "segment_issues"
        nxt = int(payload.get("segment_next_page") or payload.get("preview_next_page") or 0)
        pc = int(payload.get("page_count") or 0)
        if 0 < nxt <= pc:
            return "segment"
        if (
            (nxt > pc or (pc > 0 and imported_through(payload) >= pc))
            and payload.get("import_job_id")
            and not payload.get("result_edition_id")
        ):
            # 全书分段完成，有账本则待沉淀新排版方案
            if has_layout_or_effective_ledger(payload):
                return "finalize_edition"
        return None

    def _open_agent(
        self,
        sid: str,
        session: dict[str, Any],
        *,
        recreate_on_fail: bool = False,
    ) -> SessionAgent:
        """打开/恢复 Agent；用户点「继续」时允许新建以绕过坏死的桥。"""
        try:
            sweep_orphan_bridges(grace_s=1.0)
        except Exception:  # noqa: BLE001
            log.warning("sweep_orphan_bridges before open_agent failed", exc_info=True)
        old = self._agents.pop(sid, None)
        if old:
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass

        def _new_agent() -> SessionAgent:
            return SessionAgent(
                self.settings,
                agent_id=str(session.get("agent_id") or "") or None,
                model=session.get("model") or None,
                session_id=sid,
                kind=session["kind"],
                recreate_on_fail=recreate_on_fail,
            )

        try:
            agent = _new_agent()
        except AgentRunError:
            # SDK 默认客户端缓存着已死的桥端点（桥进程早退后 _DEFAULT_CLIENT
            # 不会自愈），resume/create 都打在同一具尸体上。重置默认客户端，
            # 下一次创建会重新拉起桥进程，然后重试一次。
            log.warning("open_agent 失败，重置 SDK 默认桥后重试 sid=%s", sid)
            try:
                close_sdk_bridge()
            except Exception:  # noqa: BLE001
                log.warning("close_sdk_bridge during open_agent retry failed", exc_info=True)
            try:
                sweep_orphan_bridges(grace_s=1.0)
            except Exception:  # noqa: BLE001
                pass
            agent = _new_agent()
        self._agents[sid] = agent
        recreated = bool(getattr(agent, "recreated", False))
        if recreated or not session.get("agent_id"):
            self.store.update_session(sid, agent_id=agent.agent_id)
        if recreated:
            self._say(
                sid,
                "system",
                {"text": "原 Agent 桥接不可用，已新建 Agent 并承接断点上下文。"},
            )
        return agent

    def _run(self, sid: str, rt: SessionRuntime) -> None:
        self._guarded(sid, lambda: self._run_inner(sid, rt))

    def _run_followup(self, sid: str, rt: SessionRuntime, text: str) -> None:
        self._guarded(sid, lambda: self._run_followup_inner(sid, rt, text))

    def _guarded(self, sid: str, fn: Callable[[], None]) -> None:
        """会话线程统一异常护栏 + 资源清理。"""
        try:
            fn()
        except AgentRunError as e:
            log.error("session %s agent error: %s", sid, e)
            self._say_continue_cta(
                sid,
                f"Agent 出错：{e}\n点「继续」或发送「继续」可重连并从断点接着完成。",
            )
            self.store.update_session(sid, status="failed")
        except Exception:  # noqa: BLE001
            log.exception("session %s crashed", sid)
            self._say_continue_cta(
                sid,
                "内部错误：\n"
                + traceback.format_exc()[-1800:]
                + "\n点「继续」可尝试重连恢复。",
            )
            self.store.update_session(sid, status="failed")
        finally:
            agent = self._agents.pop(sid, None)
            if agent:
                agent.close()
            with self._lock:
                self._runtimes.pop(sid, None)

    def _run_followup_inner(self, sid: str, rt: SessionRuntime, text: str) -> None:
        session = self.store.get_session(sid)
        assert session is not None
        want_continue = is_continue_request(text)
        unfinished = self._unfinished_pipeline(session)
        # 续跑一律允许新建 Agent：刷新/重启后常缺 agent_id，或桥已死
        recreate = True
        # 导入会话 failed 后再「继续」：旧 Agent 上下文常已坏死（internal error），
        # Resume 仍成功但 Send 秒挂——直接清空 agent_id 新建。
        if (
            want_continue
            and session.get("status") == "failed"
            and session.get("kind") == "import_advice"
            and session.get("agent_id")
        ):
            self.store.update_session(sid, agent_id="")
            session = self.store.get_session(sid) or session
            self._say(
                sid,
                "system",
                {"text": "检测到上次 Agent 异常中断，已新建 Agent 承接断点（不沿用坏死会话）。"},
            )
        perms = load_permissions(self.settings.agent_env_dir)
        self._say(sid, "user", {"text": text})
        try:
            agent = self._open_agent(sid, session, recreate_on_fail=recreate)
        except AgentRunError as e:
            self._say_continue_cta(
                sid,
                f"重连 Agent 失败：{e}\n"
                "可再点「继续」或发送「继续」重试（会清扫桥接进程并新建 Agent）。",
            )
            self._status(sid, "failed")
            return
        self.store.audit(
            sid,
            "orchestrator",
            "agent_resumed",
            {
                "agent_id": agent.agent_id,
                "continue": want_continue,
                "unfinished": unfinished,
                "recreated": bool(getattr(agent, "recreated", False)),
            },
        )

        payload = dict(session.get("payload") or {})
        # 分段导入：处理「全文继续 / 接受建议 / 继续 / 任意输入」与门闸
        if session.get("kind") == "import_advice" and payload.get("minio_key"):
            if looks_like_refuse_principle(text):
                self._say_next_steps(
                    sid,
                    preface=(
                        "无法执行该请求：违背本场景原则（改架构 / 动 35B 线 / 越权）。"
                        "请选下一步，或换一种合理说法。"
                    ),
                )
                self._status(sid, "awaiting_user")
                return
            if (
                looks_like_finalize_request(text)
                or (want_continue and unfinished == "finalize_edition")
            ) and unfinished == "finalize_edition":
                self._execution_phase(
                    sid,
                    rt,
                    "请调用 edition.finalize_from_session（会写入 layout_change_log "
                    "+ effective_changes）fork 新排版方案草稿，并输出最终报告 JSON。",
                    perms,
                    soft_chat=True,
                )
                self._say_next_steps(sid)
                return
            # 不修 / 跳过 / 下一批 → 标跳过，立刻进下一批 MinerU
            if user_wants_skip_and_continue(text) and (
                has_blocking_open_issues(payload) or unfinished == "segment_issues"
            ):
                n = len(open_issue_pages(payload))
                if n:
                    payload["open_issues"] = mark_issues_status(
                        payload, status="skipped_by_user"
                    )
                gate_end = int(payload.get("segment_gate_end") or 0)
                if gate_end > 0:
                    note_qa_through(payload, gate_end)
                nxt = advance_past_gate_batch(payload)
                self.store.update_session(sid, payload=payload)
                self._say(
                    sid,
                    "system",
                    {
                        "text": (
                            (f"已跳过 {n} 个问题页（不修），" if n else "本批无待修问题，")
                            + f"从第 {nxt} 页继续下一批。"
                        )
                    },
                )
                self._mineru_segmented_execution(sid, rt, perms)
                return
            # 修指定页 / 按建议改 → 只改点名的页（「按建议改」= 全部待办）
            open_pages = open_issue_pages(payload)
            named_pages = mentioned_open_pages(text, open_pages)
            wants_fix = (
                unfinished == "segment_issues"
                and has_blocking_open_issues(payload)
                and not user_wants_skip_and_continue(text)
                and (user_accepts_suggestions(text) or bool(named_pages))
            )
            if wants_fix:
                pages = (
                    open_pages
                    if user_accepts_suggestions(text) and not named_pages
                    else named_pages
                )
                if not pages:
                    self._say_next_steps(
                        sid, preface="未识别要修的页。请点「按建议改」或说明修哪几页。"
                    )
                    self._status(sid, "awaiting_user")
                    return
                skipped = [p for p in open_pages if p not in set(pages)]
                if skipped:
                    payload["open_issues"] = mark_issues_status(
                        payload, status="skipped_by_user", pages=skipped
                    )
                    self.store.update_session(sid, payload=payload)
                    self._say(
                        sid,
                        "system",
                        {"text": f"未点名的问题页 {skipped} 已跳过；只修 {pages}。"},
                    )
                instr = fix_instruction(
                    job_id=str(payload.get("import_job_id") or ""),
                    pages=pages,
                    edition_id=str(payload.get("edition_id") or ""),
                )
                self._execution_phase(sid, rt, instr, perms, soft_chat=True)
                self._sync_issues_from_last_report(sid)
                session2 = self.store.get_session(sid)
                pl2 = dict((session2 or {}).get("payload") or {})
                if not has_blocking_open_issues(pl2):
                    gate_end = int(pl2.get("segment_gate_end") or 0)
                    if gate_end > 0:
                        note_qa_through(pl2, gate_end)
                    nxt = advance_past_gate_batch(pl2)
                    self.store.update_session(sid, payload=pl2)
                    self._say(
                        sid,
                        "system",
                        {"text": f"指定页已处理，从第 {nxt} 页继续下一批。"},
                    )
                    self._mineru_segmented_execution(sid, rt, perms)
                else:
                    self._say_next_steps(
                        sid,
                        preface=f"仍有待办页 {open_issue_pages(pl2)}。",
                    )
                    self._status(sid, "awaiting_user")
                return
            if want_continue and unfinished:
                self._say(
                    sid,
                    "system",
                    {
                        "text": (
                            "已重连，从 MinerU 分段导入断点继续。"
                            + (self._preview_progress_hint(sid) or "")
                        )
                    },
                )
                self._mineru_segmented_execution(sid, rt, perms)
                return
            # 自由文本：一等公民 — 白名单内执行后刷新下一步选择
            if not want_continue:
                instruction = (
                    text
                    + "\n\n（会话续聊：直接处理这条用户消息。"
                    "若用户在问问题/看页面：用 page_html、必要时 render_preview，不要擅自改页。"
                    "若用户明确要求修改某页：才可用 get_blocks/replace_page_blocks/"
                    "patch_block/rerender_page，并以最终 HTML 验收。"
                    "禁止 35B reprocess refine。"
                    "合理请求在工具白名单内执行；违背原则须拒绝并说明。"
                    "确认有效的改动用 edition.record_effective_change 记账。"
                    "做完后不要假装全书已结束；用户可继续下一批或再提要求。）"
                )
                if user_confirms_effective(text):
                    instruction += (
                        "\n用户确认近期修改有效：请对已执行的有效建议调用 "
                        "edition.record_effective_change。"
                    )
                self._execution_phase(sid, rt, instruction, perms, soft_chat=True)
                self._sync_issues_from_last_report(sid)
                self._say_next_steps(sid, preface="本轮已处理。")
                self._status(sid, "awaiting_user")
                return

        if want_continue and unfinished:
            self._say(
                sid,
                "system",
                {"text": "已重连，从断点继续。" + (self._preview_progress_hint(sid) or "")},
            )
            if unfinished in ("segment", "segment_issues", "finalize_edition"):
                self._mineru_segmented_execution(sid, rt, perms)
            else:
                self._full_book_preview_execution(sid, rt, perms)
            return

        if want_continue and not unfinished:
            instruction = (
                "用户要求「继续」。请检查此前未完成的步骤并接着做完；"
                "若任务其实已完成，简要说明现状即可。"
                "收尾时按契约输出最终报告 JSON（若适用）。"
            )
        else:
            instruction = (
                text
                + "\n\n（会话续聊：直接处理这条用户消息。"
                "合理请求在工具白名单内执行；违背原则（改架构/动 35B 线/越权）须拒绝并说明。"
                "确认有效的改动用 edition.record_effective_change 记账。）"
            )
            if unfinished:
                self._say_continue_cta(
                    sid,
                    "检测到未完成的分段导入断点。点「继续」或发送「继续」即可重连并从断点接着做。"
                    + (self._preview_progress_hint(sid) or ""),
                )
            if user_confirms_effective(text):
                instruction += (
                    "\n用户确认近期修改有效：请对已执行的有效建议调用 "
                    "edition.record_effective_change。"
                )
        self._execution_phase(sid, rt, instruction, perms, soft_chat=True)

    def _run_inner(self, sid: str, rt: SessionRuntime) -> None:
        session = self.store.get_session(sid)
        assert session is not None
        kind, payload = session["kind"], session["payload"]
        perms = load_permissions(self.settings.agent_env_dir)

        # ---- 阶段 1：简报 + 确认 ----
        self._status(sid, "planning")
        self.store.audit(sid, "orchestrator", "brief_start", {"kind": kind})
        task_context = self.task_context_builder(kind, payload)
        # builder 可能往 payload 回写缓存（before_html / sample_bboxes），持久化
        self.store.update_session(sid, payload=payload)
        pack = build_context_pack(self.settings.agent_env_dir, kind, task_context)

        agent = SessionAgent(
            self.settings, model=session.get("model") or None, session_id=sid, kind=kind
        )
        self._agents[sid] = agent
        self.store.update_session(sid, agent_id=agent.agent_id)
        self.store.audit(
            sid,
            "orchestrator",
            "agent_created",
            {"agent_id": agent.agent_id, "model": agent.model, "mcp": "buddy-copilot"},
        )

        if self._ask(sid, rt, pack + _BRIEF_INSTRUCTION, perms) is None:
            return  # cancelled / 挂死重试失败（状态已置）
        if not self._confirm_brief(sid, rt, perms):
            return  # cancelled

        # ---- 阶段 2~4：MinerU 分段导入（20 页/批）或普通执行 ----
        if kind == "import_advice" and self._should_mineru_segment(sid):
            self._mineru_segmented_execution(sid, rt, perms)
            return
        self._execution_phase(sid, rt, _EXECUTE_INSTRUCTION, perms)

    def _should_mineru_segment(self, sid: str) -> bool:
        """有 PDF + edition → 走 MinerU 分段质检（替代旧 35B 三角预览）。"""
        session = self.store.get_session(sid)
        if not session:
            return False
        payload = session.get("payload") or {}
        return bool(payload.get("minio_key") and str(payload.get("edition_id") or "").strip())

    def _should_batch_full_preview(self, sid: str) -> bool:
        """兼容旧测试：有 PDF 且页数超过单批。"""
        session = self.store.get_session(sid)
        if not session:
            return False
        payload = session.get("payload") or {}
        if not payload.get("minio_key"):
            return False
        page_count = int(payload.get("page_count") or 0)
        msgs = self.store.get_messages(sid)
        if user_wants_full_preview(msgs):
            return True
        return page_count > PREVIEW_PAGES_PER_RUN

    def _wait_import_job(
        self,
        client: BuddyApiClient,
        job_id: str,
        *,
        page_ranges: str | None = None,
        page_limit: int | None = None,
        timeout_s: float = 1800,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = client.get_import_job(job_id)
            status = str(last.get("status") or "")
            if status == "failed":
                return last
            if import_batch_ready(last, page_ranges=page_ranges, page_limit=page_limit):
                return last
            time.sleep(8)
        return last

    def _drop_foreign_job_binding(
        self, sid: str, payload: dict[str, Any], client: BuddyApiClient
    ) -> None:
        """绑定的导入任务若不是本会话这本 PDF，立即解绑并清进度。

        否则 extend_import 会把本书页码写进别人的任务（曾把「第二册」写进「第一册」）。
        """
        job_id = str(payload.get("import_job_id") or payload.get("job_id") or "").strip()
        mine = _pdf_identity(payload.get("minio_key"), payload.get("label"))
        if not job_id or not mine:
            return
        try:
            job = client.get_import_job(job_id)
        except Exception:  # noqa: BLE001
            log.warning("任务归属校验取任务失败 sid=%s job=%s（保守放行）", sid, job_id)
            return
        meta = job.get("meta") or {}
        theirs = _pdf_identity(
            job.get("minio_key") or meta.get("minio_key"),
            job.get("label") or meta.get("label"),
        )
        if not theirs or theirs == mine:
            return
        for k in (
            "import_job_id",
            "job_id",
            "segment_imported_through",
            "segment_qa_through",
            "segment_next_page",
            "segment_gate_end",
        ):
            payload.pop(k, None)
        self.store.update_session(sid, payload=payload, import_job_id="")
        self.store.audit(
            sid,
            "orchestrator",
            "foreign_job_binding_dropped",
            {"job_id": job_id, "job_pdf": theirs, "session_pdf": mine},
        )
        self._say(
            sid,
            "system",
            {
                "text": (
                    f"⚠️ 已解除错误绑定：任务 `{job_id}` 属于「{theirs}」，"
                    f"本次导入的是「{mine}」。将为本书新建 MinerU 导入任务，"
                    "不会再写入别本书的任务。"
                )
            },
        )

    def _mineru_segmented_execution(
        self, sid: str, rt: SessionRuntime, perms: Permissions
    ) -> None:
        """MinerU 每批 20 页实导入 → Agent 文本质检 → 有 open_issues 则停；清债后 extend。"""
        self._status(sid, "executing")
        session = self.store.get_session(sid)
        assert session is not None
        payload = dict(session.get("payload") or {})
        self._drop_foreign_job_binding(sid, payload, BuddyApiClient(self.settings))
        minio_key = str(payload.get("minio_key") or "")
        edition_id = str(payload.get("edition_id") or "").strip()
        page_count = int(payload.get("page_count") or 0)
        if not edition_id:
            self._say(sid, "system", {"text": "必须先选择教材方案（edition_id）才能 AI 分段导入。"})
            self._status(sid, "failed")
            return
        payload.setdefault("source_edition_id", edition_id)
        client = BuddyApiClient(self.settings)
        snapshot = self._new_snapshot(perms)
        try:
            if page_count <= 0:
                # 轻探：让 Agent 不必要；用 start 1-1 不划算。要求 payload 带 page_count。
                self._say(
                    sid,
                    "system",
                    {"text": "缺少 page_count。请在导入面板重新选择 PDF（会探测页数）后再开 AI 导入。"},
                )
                self._status(sid, "failed")
                return

            batches = segment_batches(page_count, IMPORT_PAGES_PER_SEGMENT)
            resume_from = int(payload.get("segment_next_page") or 1)
            start_idx = 0
            for i, (a, b) in enumerate(batches):
                if b < resume_from:
                    start_idx = i + 1
                    continue
                if a <= resume_from <= b:
                    start_idx = i
                    break
                if a >= resume_from:
                    start_idx = i
                    break

            self._say(
                sid,
                "system",
                {
                    "text": (
                        f"开始 MinerU 分段导入：全书 {page_count} 页，每批 "
                        f"{IMPORT_PAGES_PER_SEGMENT} 页；方案 `{edition_id}`。"
                        f"从第 {batches[start_idx][0] if batches else 1} 页继续。"
                    )
                },
            )

            for idx in range(start_idx, len(batches)):
                if rt.cancelled.is_set():
                    self._status(sid, "cancelled")
                    return
                if has_blocking_open_issues(payload):
                    self.store.update_session(sid, payload=payload)
                    self._say_next_steps(
                        sid,
                        preface=f"待办问题页未清：{open_issue_pages(payload)}。",
                    )
                    self._status(sid, "awaiting_user")
                    return

                start, end = batches[idx]
                pr = page_range_str(start, end)
                run_no, total = idx + 1, len(batches)
                job_id = str(payload.get("import_job_id") or "")
                already = imported_through(payload) >= end and bool(job_id)
                qa_done = qa_through(payload) >= end
                if already and qa_done:
                    if has_blocking_open_issues(payload):
                        set_gate_batch(payload, start, end)
                        self.store.update_session(sid, payload=payload)
                        self._say_next_steps(
                            sid,
                            preface=(
                                f"第 {start}–{end} 页已导入，仍有待办页 "
                                f"{open_issue_pages(payload)}。请选择修哪些或不修。"
                            ),
                        )
                        self._status(sid, "awaiting_user")
                        return
                    # 已导入且质检过 → 不重跑，直接下一批
                    payload["segment_next_page"] = end + 1
                    payload.pop("segment_gate_end", None)
                    self.store.update_session(sid, payload=payload)
                    self._say(
                        sid,
                        "system",
                        {
                            "text": (
                                f"📄 分段进度 {run_no}/{total}：第 {start}–{end} 页已导入且已质检，"
                                "跳过重复识别与质检。"
                            )
                        },
                    )
                    continue
                if already and not qa_done:
                    self._say(
                        sid,
                        "system",
                        {
                            "text": (
                                f"📄 分段进度 {run_no}/{total}：第 {start}–{end} 页已导入，"
                                "跳过重复 MinerU，开始质检…"
                            )
                        },
                    )
                else:
                    self._say(
                        sid,
                        "system",
                        {"text": f"📄 分段进度 {run_no}/{total}：MinerU 识别第 {start}–{end} 页…"},
                    )
                    try:
                        if not job_id:
                            out = client.start_pdf_import(
                                minio_key=minio_key,
                                edition_id=edition_id,
                                label=str(payload.get("label") or ""),
                                page_ranges=pr,
                            )
                            job_id = str(out.get("id") or "")
                            payload["import_job_id"] = job_id
                            payload["job_id"] = job_id
                            self.store.update_session(sid, payload=payload)
                            try:
                                self.store.bind_import_job(sid, job_id)
                            except Exception:  # noqa: BLE001
                                log.exception("bind_import_job failed sid=%s job=%s", sid, job_id)
                            self.store.add_message(
                                sid,
                                "import_event",
                                {
                                    "event": "import_started",
                                    "line": "mineru",
                                    "job_id": job_id,
                                    "page_ranges": pr,
                                    "edition_id": edition_id,
                                },
                            )
                        else:
                            if has_blocking_open_issues(payload):
                                raise RuntimeError("open_issues 未清，拒绝 extend")
                            out = client.extend_pdf_import(job_id, pr)
                            self.store.add_message(
                                sid,
                                "import_event",
                                {
                                    "event": "import_started",
                                    "line": "mineru",
                                    "job_id": job_id,
                                    "page_ranges": pr,
                                    "edition_id": edition_id,
                                },
                            )
                        job = self._wait_import_job(client, job_id, page_ranges=pr)
                        if str(job.get("status") or "") == "failed":
                            self._say_continue_cta(
                                sid,
                                f"MinerU 分段失败（{pr}）：{job.get('error')}\n点「继续」可重试本批。",
                            )
                            payload["segment_next_page"] = start
                            self.store.update_session(sid, payload=payload)
                            self._status(sid, "failed")
                            return
                        if not import_batch_ready(job, page_ranges=pr):
                            self._say_continue_cta(
                                sid,
                                f"MinerU 分段等待超时（{pr}），本批页尚未全部就绪。"
                                "点「继续」可重试本批。",
                            )
                            payload["segment_next_page"] = start
                            self.store.update_session(sid, payload=payload)
                            self._status(sid, "failed")
                            return
                        note_imported_through(payload, end)
                        self.store.update_session(sid, payload=payload)
                        self.store.add_message(
                            sid,
                            "import_event",
                            {
                                "event": "import_done",
                                "line": "mineru",
                                "job_id": job_id,
                                "page_ranges": pr,
                                "status": job.get("status"),
                                "ok": True,
                            },
                        )
                        self._say(
                            sid,
                            "system",
                            {
                                "text": (
                                    f"✅ MinerU 第 {start}–{end} 页已就绪（job `{job_id}`）。"
                                    "中栏可浏览；右栏开始质检…"
                                )
                            },
                        )
                    except Exception as e:  # noqa: BLE001
                        log.exception("segment import failed sid=%s pages=%s", sid, pr)
                        self._say_continue_cta(
                            sid, f"分段导入异常：{e}\n点「继续」可重试本批 {pr}。"
                        )
                        payload["segment_next_page"] = start
                        self.store.update_session(sid, payload=payload)
                        self._status(sid, "failed")
                        return

                open_pages = open_issue_pages(payload)
                instr = qa_instruction(
                    job_id=job_id,
                    start=start,
                    end=end,
                    page_count=page_count,
                    run_index=run_no,
                    run_total=total,
                    open_pages=open_pages,
                    edition_id=edition_id,
                )
                text = self._ask(sid, rt, instr, perms)
                if text is None or rt.cancelled.is_set():
                    set_gate_batch(payload, start, end)
                    self.store.update_session(sid, payload=payload)
                    if rt.cancelled.is_set():
                        self._status(sid, "cancelled")
                    return
                # 每批质检结束即查越权：本批就地拦下，不带着改动继续导入下一批
                if self._fs_violation_stop(sid, snapshot):
                    set_gate_batch(payload, start, end)
                    self.store.update_session(sid, payload=payload)
                    return
                report = extract_qa_report(text)
                if report is None:
                    set_gate_batch(payload, start, end)
                    self.store.update_session(sid, payload=payload)
                    self._say_continue_cta(
                        sid,
                        f"第 {start}–{end} 页质检未交出问题清单 JSON，本批不算完成。"
                        "点「继续」可重跑本批质检。",
                    )
                    self._status(sid, "failed")
                    return
                apply_qa_report_to_payload(payload, report, batch=pr)
                note_imported_through(payload, end)
                note_qa_through(payload, end)  # 质检轮已完成（无论有无问题）
                needs_user = bool(report.get("needs_user")) or has_blocking_open_issues(payload)
                if needs_user and has_blocking_open_issues(payload):
                    set_gate_batch(payload, start, end)
                    self.store.update_session(sid, payload=payload)
                    self._say_next_steps(
                        sid,
                        preface=(
                            "本批已根据最终页面列出问题，已暂停。"
                            "请选择：不修进下一批、按建议改全部，或说明修哪几页。"
                        ),
                    )
                    self._status(sid, "awaiting_user")
                    return

                payload["segment_next_page"] = end + 1
                payload.pop("segment_gate_end", None)
                note_imported_through(payload, end)
                self.store.update_session(sid, payload=payload)
                self._say(
                    sid,
                    "system",
                    {
                        "text": (
                            f"✅ 质检通过 {run_no}/{total}（页 {start}–{end}）。"
                            + (
                                f"下一批：{batches[idx + 1][0]}–{batches[idx + 1][1]}。"
                                if idx + 1 < total
                                else "全书分段完成。"
                            )
                        )
                    },
                )

            # 收尾：排版账本 / 有效变更 → fork 新排版方案
            payload = dict((self.store.get_session(sid) or {}).get("payload") or payload)
            if has_layout_or_effective_ledger(payload) and not payload.get(
                "result_edition_id"
            ):
                self._say_next_steps(
                    sid,
                    preface="全书 MinerU 分段已完成，存在排版改动账本，可生成新排版方案。",
                )
                self._say(sid, "system", {"text": "正在根据账本沉淀新排版方案草稿…"})
                fin = self._ask(
                    sid,
                    rt,
                    "全书 MinerU 分段已完成。请调用 edition.finalize_from_session"
                    "（会写入 layout_change_log），"
                    "然后输出最终报告 ```json（含 job_id、import_line=mineru、result_edition_id）。",
                    perms,
                )
                if fin is None:
                    self._status(sid, "awaiting_user")
                    return
                payload = dict((self.store.get_session(sid) or {}).get("payload") or payload)
            self.store.update_session(sid, payload=payload)
            self._say_next_steps(
                sid,
                preface=(
                    f"MinerU 分段导入完成。job_id=`{payload.get('import_job_id')}`。"
                    + (
                        f" 新排版方案草稿：`{payload.get('result_edition_id')}`。"
                        if payload.get("result_edition_id")
                        else " 无新沉淀，沿用原方案。"
                    )
                ),
            )
            self._say(
                sid,
                "result",
                {
                    "ok": True,
                    "report": {
                        "status": "done",
                        "summary": "MinerU 分段导入完成",
                        "job_id": payload.get("import_job_id"),
                        "import_line": "mineru",
                        "result_edition_id": payload.get("result_edition_id") or "",
                    },
                },
            )
            self._status(sid, "done")
        finally:
            snapshot.cleanup()
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    def _full_book_preview_execution(
        self, sid: str, rt: SessionRuntime, perms: Permissions
    ) -> None:
        """全书预览：多轮 Agent.send（每轮 PREVIEW_PAGES_PER_RUN 页）→ 汇总 page_plan → 导入。

        每一轮 Run 结束都会写系统进度；挂死后重连带上「已完成到第几页」。
        """
        self._status(sid, "executing")
        session = self.store.get_session(sid)
        assert session is not None
        payload = dict(session.get("payload") or {})
        minio_key = str(payload.get("minio_key") or "")
        page_count = int(payload.get("page_count") or 0)
        edition_id = str(payload.get("edition_id") or "")
        if page_count <= 0:
            # 不知页数时先让 Agent 渲一页探页数，再进入批循环
            probe = self._ask(
                sid,
                rt,
                (
                    f"PDF `{minio_key}` 需要全书预览但尚不知总页数。"
                    "请 corpus.render_preview(pages=\"1-1\")，从返回的 page_count 得知总页数后，"
                    "只输出 ```json {\"page_count\": N, \"page_plan_partial\": [], \"pages_done\": \"\"} ```，"
                    "不要导入。"
                ),
                perms,
            )
            if probe is None:
                return
            partial = extract_page_plan_partial(probe)
            # 也容忍裸 JSON 里的 page_count
            if partial is None:
                self._say(sid, "system", {"text": "无法解析总页数，全书预览中止。"})
                self._status(sid, "failed")
                return
            # page_count may be in notes or we need another path - check raw json
            import json as _json
            import re as _re

            m = _re.search(r"```json\s*(\{.*?\})\s*```", probe, _re.S)
            if m:
                try:
                    page_count = int(_json.loads(m.group(1)).get("page_count") or 0)
                except Exception:  # noqa: BLE001
                    page_count = 0
            if page_count <= 0:
                self._say(sid, "system", {"text": "总页数无效，全书预览中止。"})
                self._status(sid, "failed")
                return
            payload["page_count"] = page_count
            self.store.update_session(sid, payload=payload)

        batches = page_batches(page_count)
        if not batches:
            self._say(sid, "system", {"text": "页数为 0，无法预览。"})
            self._status(sid, "failed")
            return

        accumulated: list[dict] = list(payload.get("page_plan") or [])
        # 支持断点续预览：payload.preview_next_page
        resume_from = int(payload.get("preview_next_page") or 1)
        start_batch_idx = 0
        for i, (a, b) in enumerate(batches):
            if b < resume_from:
                start_batch_idx = i + 1
                continue
            if a < resume_from <= b:
                start_batch_idx = i
                break
            if a >= resume_from:
                start_batch_idx = i
                break

        total = len(batches)
        self._say(
            sid,
            "system",
            {
                "text": (
                    f"开始全书预览：共 {page_count} 页，拆成 {total} 轮"
                    f"（每轮 {PREVIEW_PAGES_PER_RUN} 页三角裁决："
                    "35B 主力 + MinerU/结构锚点 + 仅分歧页用 Cursor 视觉）。"
                    f"从第 {batches[start_batch_idx][0]} 页继续。"
                )
            },
        )

        snapshot = self._new_snapshot(perms)
        try:
            for idx in range(start_batch_idx, total):
                if rt.cancelled.is_set():
                    self._status(sid, "cancelled")
                    return
                start, end = batches[idx]
                run_no = idx + 1
                self._say(
                    sid,
                    "system",
                    {
                        "text": (
                            f"⚖️ 预览进度 {run_no}/{total}：三角裁决第 {start}–{end} 页"
                            f"（35B + MinerU/锚点；已累计 {len(accumulated)} 段）…"
                        )
                    },
                )
                self.store.audit(
                    sid,
                    "orchestrator",
                    "preview_batch_start",
                    {"run": run_no, "total": total, "pages": f"{start}-{end}"},
                )
                # 编排器侧跑三角裁决（MCP 同款逻辑），Agent 只处理分歧 + 写 page_plan
                try:
                    triad = run_triad(
                        self.settings,
                        minio_key=minio_key,
                        pages=f"{start}-{end}",
                        dpi=72,
                    )
                except Exception as te:  # noqa: BLE001
                    log.exception("session %s triad failed pages=%s-%s", sid, start, end)
                    payload["preview_next_page"] = start
                    payload["page_plan"] = accumulated
                    self.store.update_session(sid, payload=payload)
                    self._say_continue_cta(
                        sid,
                        f"三角裁决失败（{te}），本批中止。"
                        f"{self._preview_progress_hint(sid)}"
                        "\n点「继续」或发送「继续」可重连并从该批接着做。",
                    )
                    self._status(sid, "failed")
                    return
                if triad.get("error"):
                    payload["preview_next_page"] = start
                    payload["page_plan"] = accumulated
                    self.store.update_session(sid, payload=payload)
                    self._say_continue_cta(
                        sid,
                        f"三角裁决错误：{triad.get('error')}"
                        f"{self._preview_progress_hint(sid)}"
                        "\n点「继续」或发送「继续」可重连并从该批接着做。",
                    )
                    self._status(sid, "failed")
                    return
                triad["page_plan_draft"] = merged_to_page_plan_hints(
                    triad.get("merged") or [], edition_id=edition_id
                )
                esc = triad.get("escalate_pages") or []
                self._say(
                    sid,
                    "system",
                    {
                        "text": (
                            f"三角裁决完成（锚点={triad.get('anchor_source')}）："
                            f"页 {start}–{end}，分歧需 Cursor 视觉 {len(esc)} 页"
                            + (f" {esc}" if esc else "（无需读图）")
                            + "。交给 Agent 汇总 page_plan…"
                        )
                    },
                )
                self.store.audit(
                    sid,
                    "orchestrator",
                    "preview_triad_done",
                    {
                        "pages": f"{start}-{end}",
                        "anchor_source": triad.get("anchor_source"),
                        "escalate_pages": esc,
                    },
                )
                instr = batch_instruction(
                    minio_key=minio_key,
                    start=start,
                    end=end,
                    page_count=page_count,
                    run_index=run_no,
                    run_total=total,
                    already=accumulated,
                    triad=triad,
                    edition_id=edition_id,
                )
                text = self._ask(sid, rt, instr, perms)
                if text is None:
                    # 已 failed/cancelled；把断点写入便于续聊
                    payload["preview_next_page"] = start
                    payload["page_plan"] = accumulated
                    self.store.update_session(sid, payload=payload)
                    return
                partial = extract_page_plan_partial(text)
                if partial is None or not partial.page_plan_partial:
                    # 追问一次只要 partial
                    text2 = self._ask(
                        sid,
                        rt,
                        (
                            f"本轮必须交付页 {start}-{end} 的 page_plan_partial。"
                            "请立即只输出 ```json {\"pages_done\":\""
                            f"{start}-{end}"
                            "\",\"page_plan_partial\":[...]} ```，不要其它工具。"
                        ),
                        perms,
                    )
                    if text2 is None:
                        payload["preview_next_page"] = start
                        payload["page_plan"] = accumulated
                        self.store.update_session(sid, payload=payload)
                        return
                    partial = extract_page_plan_partial(text2)
                if partial is None or not partial.page_plan_partial:
                    payload["preview_next_page"] = start
                    payload["page_plan"] = accumulated
                    self.store.update_session(sid, payload=payload)
                    self._say_continue_cta(
                        sid,
                        f"第 {run_no}/{total} 轮（页 {start}–{end}）未交出 page_plan_partial，"
                        "全书预览中止。"
                        f"{self._preview_progress_hint(sid)}"
                        "\n点「继续」或发送「继续」可重连并从该批重试。",
                    )
                    self._status(sid, "failed")
                    return

                accumulated = merge_page_plans(accumulated, partial.page_plan_partial)
                payload["page_plan"] = accumulated
                payload["preview_next_page"] = end + 1
                self.store.update_session(sid, payload=payload)
                # 每批查一次越权：不让带着越权改动继续跑后面几十分钟
                if self._fs_violation_stop(sid, snapshot):
                    return
                self._say(
                    sid,
                    "system",
                    {
                        "text": (
                            f"✅ 预览进度 {run_no}/{total}：第 {start}–{end} 页已归类"
                            f"（累计 {len(accumulated)} 段 / 全书 {page_count} 页）。"
                            + (
                                f"下一批：{batches[idx + 1][0]}–{batches[idx + 1][1]}。"
                                if idx + 1 < total
                                else "全部页已看完，进入导入。"
                            )
                        )
                    },
                )
                self.store.audit(
                    sid,
                    "orchestrator",
                    "preview_batch_done",
                    {
                        "run": run_no,
                        "pages": f"{start}-{end}",
                        "segments": len(accumulated),
                    },
                )
                # 消化排队消息（不打断批循环语义，仅记录）
                queued = self._drain_user(rt)
                if queued:
                    self._say(
                        sid,
                        "system",
                        {"text": "已记下你的补充：" + " / ".join(queued)[:300] + "（导入阶段一并考虑）"},
                    )

            # ---- 全部预览完成 → 导入收尾 ----
            msgs = self.store.get_messages(sid)
            page_limit = detect_page_limit_from_messages(msgs, default=12)
            self._say(
                sid,
                "system",
                {
                    "text": (
                        f"全书 {page_count} 页预览归类完成（{len(accumulated)} 段）。"
                        f"开始预览版导入 page_limit={page_limit}…"
                    )
                },
            )
            import_instr = final_import_instruction(
                minio_key=minio_key,
                page_count=page_count,
                page_plan=accumulated,
                page_limit=page_limit,
                edition_id=edition_id,
            )
            report = self._run_round(sid, rt, import_instr, perms, soft_chat=False)
            if report is None:
                return
            if self._fs_violation_stop(sid, snapshot):
                return
            if not report.page_plan and accumulated:
                report.page_plan = [PagePlanEntry.model_validate(x) for x in accumulated]
            payload["page_plan"] = accumulated
            if report.status == "done":
                payload["preview_next_page"] = 0  # 清除断点
            self.store.update_session(sid, payload=payload, plan=report.model_dump())
            if report.status == "done":
                self._say(sid, "result", {"ok": True, "report": report.model_dump()})
                self._status(sid, "done")
                self.store.audit(sid, "orchestrator", "session_done", report.model_dump())
                return
            if report.status == "failed":
                self._say(sid, "system", {"text": self._rollback(snapshot, "任务失败")})
                self._say(sid, "result", {"ok": False, "report": report.model_dump()})
                self._status(sid, "failed")
                return
            # blocked
            self._say(sid, "result", {"ok": False, "blocked": True, "report": report.model_dump()})
            self._status(sid, "awaiting_user")
            text = self._wait_user(rt)
            if text is None:
                self._status(sid, "cancelled")
                return
            self._say(sid, "user", {"text": text})
            # 导入阶段卡住后走普通执行续聊
            self._execution_phase(
                sid,
                rt,
                text + "\n\n（请继续完成导入与验收，收尾输出最终报告 JSON。）",
                perms,
            )
        finally:
            snapshot.cleanup()

    def _execution_phase(
        self,
        sid: str,
        rt: SessionRuntime,
        first_instruction: str,
        perms: Permissions,
        *,
        soft_chat: bool = False,
    ) -> None:
        """连续自主执行阶段（首轮会话与终态续聊共用）。

        Cursor 式排队消息：执行期间用户发的消息在回合间隙送达 Agent——
        即使 Agent 已报 done，也会先消化排队消息再收尾。
        soft_chat：续聊允许无 FinalReport JSON（纯问答也算 done）。
        """
        self._status(sid, "executing")
        snapshot = self._new_snapshot(perms)
        try:
            instruction = first_instruction
            while True:
                report = self._run_round(sid, rt, instruction, perms, soft_chat=soft_chat)
                if report is None:
                    return  # 终止状态（cancelled/failed）由 _ask 内部置好，不在此覆盖

                if self._fs_violation_stop(sid, snapshot):
                    return

                self.store.update_session(sid, plan=report.model_dump())

                # 排队消息优先：Agent 报了 done/blocked，但用户执行期间又说了话 → 继续对话
                queued = self._drain_user(rt)
                if queued and report.status in ("done", "blocked"):
                    self._say(
                        sid,
                        "result",
                        {"ok": report.status == "done", "interim": True, "report": report.model_dump()},
                    )
                    self._status(sid, "executing")
                    instruction = (
                        "\n\n".join(queued)
                        + "\n\n（请继续自主执行，收尾时按契约输出最终报告 JSON。）"
                    )
                    continue

                if report.status == "done":
                    self._say(sid, "result", {"ok": True, "report": report.model_dump()})
                    self._status(sid, "done")
                    self.store.audit(sid, "orchestrator", "session_done", report.model_dump())
                    return
                if report.status == "failed":
                    self._say(sid, "system", {"text": self._rollback(snapshot, "任务失败")})
                    self._say(sid, "result", {"ok": False, "report": report.model_dump()})
                    self._status(sid, "failed")
                    return

                # blocked：等用户输入后继续
                self._say(sid, "result", {"ok": False, "blocked": True, "report": report.model_dump()})
                self._status(sid, "awaiting_user")
                text = self._wait_user(rt)
                if text is None:
                    self._status(sid, "cancelled")
                    return
                self._say(sid, "user", {"text": text})
                self._status(sid, "executing")
                instruction = text + "\n\n（请继续自主执行，收尾时按契约输出最终报告 JSON。）"
        finally:
            snapshot.cleanup()

    # ---------- 子流程 ----------

    def _ask(
        self,
        sid: str,
        rt: SessionRuntime,
        prompt: str,
        perms: Permissions,
    ) -> str | None:
        """带预算护栏的一轮 send（自愈版）：

        - 墙钟超限或空转（长时间零输出且无工具活动/待决批准）→ 判定 SDK 流挂死；
        - 挂死后自动重连同一 Cursor Agent 重试一次；连续两次才终止会话（failed）。
        返回 None 表示会话应终止（状态已由本函数置好）。
        """
        # 预算按「轮」重置：计数落在库里（多个 MCP 进程共享），
        # 但续跑/下一批不该继承上一轮已用满的额度。
        try:
            self.store.reset_counter(sid, "tool_calls")
        except Exception:  # noqa: BLE001 - 计数器问题不该阻断这一轮
            log.warning("reset tool_calls counter failed", exc_info=True)
        force = False
        for attempt in (1, 2):
            try:
                return self._ask_once(sid, rt, prompt, perms, force=force)
            except (_RoundHang, AgentRunError) as e:
                # 空转挂死 / 桥接断连（RemoteProtocol、Connection refused…）都走同路径自愈
                if isinstance(e, AgentRunError) and not _is_recoverable_agent_error(e):
                    raise
                reason = str(e)
                if attempt == 1:
                    self._say(
                        sid,
                        "system",
                        {"text": f"Agent 运行中断（{reason}），已中止本轮并自动重连重试…"},
                    )
                    fresh = _needs_fresh_agent(e)
                    self.store.audit(
                        sid,
                        "orchestrator",
                        "agent_reconnect",
                        {"reason": reason[:500], "force_new": fresh},
                    )
                    try:
                        self._reconnect(sid, recreate_on_fail=True, force_new=fresh)
                    except Exception as re:  # noqa: BLE001
                        log.exception("session %s reconnect failed", sid)
                        self._say_continue_cta(
                            sid,
                            f"自动重连失败：{re}\n"
                            "点「继续」或发送「继续」可再次重连并从断点接着做。",
                        )
                        self._status(sid, "failed")
                        return None
                    force = True  # 官方 local.force：清掉可能残留的僵尸 run
                    # 重连提示：保留「本轮任务」原文，附上断点进度（绝不改成少看图）
                    progress = self._preview_progress_hint(sid)
                    if fresh:
                        prompt = (
                            "（上一轮 Cursor Agent 内部错误，已新建 Agent。"
                            f"{progress}"
                            "请按下方任务说明从当前断点完整重做本轮，不要假设上一轮工具结果仍有效。）\n\n"
                            + prompt
                        )
                        reconnect_msg = (
                            "已新建 Agent，继续本轮任务。"
                            + (progress if progress else "")
                        )
                    else:
                        prompt = (
                            "（上一轮连接中断被中止——可能是墙钟超时/桥接挂死/空转。"
                            f"{progress}"
                            "请从当前进度继续：已成功的 render_preview 不要重复；"
                            "本轮该看的图仍要看完并打 session.update_todos 心跳；"
                            "不要擅自缩小预览范围。）\n\n" + prompt
                        )
                        reconnect_msg = (
                            "已重连，继续本轮任务。"
                            + (progress if progress else "请 Agent 汇报卡在哪一步。")
                        )
                    self._say(sid, "system", {"text": reconnect_msg})
                    continue
                progress = self._preview_progress_hint(sid)
                self._say_continue_cta(
                    sid,
                    f"Agent 连续两轮中断（{reason}），本轮终止。"
                    + (progress if progress else "")
                    + "\n点「继续」或发送「继续」会重连 Agent 并从断点接着完成。",
                )
                self._status(sid, "failed")
                return None
        return None  # 不可达

    def _reconnect(
        self,
        sid: str,
        *,
        recreate_on_fail: bool = True,
        force_new: bool = False,
    ) -> None:
        """挂死自愈：清扫桥 → 关闭旧句柄 → resume；失败则新建 Agent。

        force_new=True：清空 agent_id，强制 Agent.create（用于 internal error 坏死上下文）。
        """
        if force_new:
            self.store.update_session(sid, agent_id="")
        session = self.store.get_session(sid)
        assert session is not None
        try:
            close_sdk_bridge(timeout=3)
        except Exception:  # noqa: BLE001
            pass
        self._open_agent(sid, session, recreate_on_fail=recreate_on_fail)

    def _sync_issues_from_last_report(self, sid: str) -> None:
        """从最近 assistant/result 文本解析质检 JSON，写回 open_issues。"""
        session = self.store.get_session(sid)
        if not session:
            return
        payload = dict(session.get("payload") or {})
        msgs = self.store.get_messages(sid, 0)
        report = None
        for m in reversed(msgs[-40:]):
            role = m.get("role")
            content = m.get("content") or {}
            text = ""
            if role == "assistant":
                text = str(content.get("text") or "")
            elif role == "result":
                rep = content.get("report")
                if isinstance(rep, dict):
                    text = str(rep.get("summary") or "")
                    # 有时 report 本身就是质检结构
                    if "issues" in rep or "fixed_pages" in rep:
                        report = rep
                        break
            if text:
                report = extract_qa_report(text)
                if report:
                    break
        if not report:
            return
        batch = ""
        ge = int(payload.get("segment_gate_end") or 0)
        gs = int(payload.get("segment_next_page") or 0)
        if ge and gs:
            batch = page_range_str(gs, ge)
        apply_qa_report_to_payload(payload, report, batch=batch)
        self.store.update_session(sid, payload=payload)

    def _preview_progress_hint(self, sid: str) -> str:
        """全书预览断点文案（重连/失败时告诉用户卡在哪）。"""
        session = self.store.get_session(sid)
        if not session:
            return ""
        payload = session.get("payload") or {}
        nxt = int(payload.get("segment_next_page") or payload.get("preview_next_page") or 0)
        page_count = int(payload.get("page_count") or 0)
        done = imported_through(payload)
        if done <= 0 and nxt > 1:
            done = max(0, nxt - 1)
        open_n = len(open_issue_pages(payload))
        if done <= 0 and nxt <= 0 and page_count <= 0:
            return ""
        if done <= 0 and nxt <= 0:
            return f"当前全书共 {page_count} 页，分段导入尚未写入断点。"
        extra = f"；待办问题页 {open_n} 个" if open_n else ""
        gate = int(payload.get("segment_gate_end") or 0)
        if open_n and gate:
            return (
                f"MinerU 分段断点：已导入约 {done}/{page_count} 页"
                f"（门闸停在 {nxt}–{gate}）{extra}。"
            )
        return (
            f"MinerU 分段断点：已导入约 {done}/{page_count} 页"
            f"（下一页 {max(nxt, done + 1)}）{extra}。"
        )

    def _idle_seconds(self, sid: str, stream_ts: float) -> float:
        """距最近一次活动的秒数。活动 = 流式文本 / 新消息落库；
        待决批准卡或进行中的工具调用（step start 未 done）视为零空转。"""
        if self.store.has_pending_approval(sid):
            return 0.0
        last = self.store.last_message(sid)
        ts = stream_ts
        if last:
            content = last.get("content") or {}
            if last["role"] == "step" and content.get("state") == "start":
                return 0.0  # 工具执行中（docker build 等分钟级操作是合法的静默）
            ts = max(ts, float(last["created_at"]))
        return time.time() - ts

    def _ask_once(
        self,
        sid: str,
        rt: SessionRuntime,
        prompt: str,
        perms: Permissions,
        *,
        force: bool = False,
    ) -> str | None:
        agent = self._agents[sid]
        chunks: list[str] = []
        buf: list[str] = []
        buf_len = 0
        activity = {"ts": time.time()}

        def flush() -> None:
            nonlocal buf, buf_len
            if buf:
                self._say(sid, "assistant", {"text": "".join(buf)})
                buf = []
                buf_len = 0

        def on_text(t: str) -> None:
            # SDK 逐 token 回调：按段落/长度缓冲落库，避免一 token 一条消息
            nonlocal buf_len
            activity["ts"] = time.time()
            chunks.append(t)
            buf.append(t)
            buf_len += len(t)
            if buf_len >= 400 or t.endswith("\n\n"):
                flush()

        result: dict[str, Any] = {}

        def target() -> None:
            try:
                result["text"] = agent.ask(prompt, on_text=on_text, force=force)
            except Exception as e:  # noqa: BLE001
                result["err"] = e

        wall = perms.budgets.run_timeout_s or self.settings.agent_run_timeout
        idle_limit = perms.budgets.idle_timeout_s
        th = threading.Thread(target=target, daemon=True, name=f"copilot-ask-{sid}")
        started = time.time()
        last_heartbeat = started
        th.start()
        while True:
            th.join(5)
            if not th.is_alive():
                break
            if rt.cancelled.is_set():
                agent.cancel_current()  # 已限时
                th.join(10)
                break
            elapsed = time.time() - started
            # 第二轮及以后也要有可见心跳：每 45s 报一次仍在跑（挂了也能看到最后卡在哪）
            if time.time() - last_heartbeat >= 45:
                idle = self._idle_seconds(sid, activity["ts"])
                hint = self._preview_progress_hint(sid)
                self._say(
                    sid,
                    "system",
                    {
                        "text": (
                            f"⏳ Run 仍在进行（已 {int(elapsed)}s，距上次活动约 {int(idle)}s）。"
                            + (hint if hint else "等待 Agent 工具/文本输出…")
                        )
                    },
                )
                last_heartbeat = time.time()
            if elapsed > wall:
                agent.cancel_current()
                th.join(10)
                raise _RoundHang(f"超过单轮墙钟预算 {wall}s")
            if idle_limit and elapsed > idle_limit:
                idle = self._idle_seconds(sid, activity["ts"])
                if idle > idle_limit:
                    agent.cancel_current()
                    th.join(10)
                    raise _RoundHang(f"连续 {int(idle)}s 无输出（疑似连接挂死）")
        flush()  # 落库缓冲区尾部
        if rt.cancelled.is_set():
            self._status(sid, "cancelled")
            return None
        if "err" in result:
            raise result["err"]
        return str(result.get("text") or "".join(chunks))

    def _confirm_brief(self, sid: str, rt: SessionRuntime, perms: Permissions) -> bool:
        """简报确认卡（闸门与对话分离，对齐 Cursor Agent 连续对话）。

        - 点「确认」/ 短口令「确认执行」→ 进入执行
        - 点「拒绝」或明确打回修改 → 重拟简报
        - 其余消息（含「接下来要做什么」）→ 澄清问答，确认卡保持 pending
        """
        while True:
            aid = self.store.create_approval(
                sid,
                "brief_confirm",
                {"tool": "brief_confirm", "title": "行动简报确认", "risk": "low"},
            )
            self._say(
                sid,
                "approval",
                {
                    "approval_id": aid,
                    "action_key": "brief_confirm",
                    "title": "行动简报确认",
                    "description": "点「确认执行」后开始连续自主执行（闸门动作仍会逐个请求批准）。"
                    "你也可以先在输入框追问（例如「接下来做什么」）——Agent 会回答且确认卡仍有效；"
                    "只有明确说「拒绝 / 改方案」才会重拟简报。",
                    "risk": "low",
                },
            )
            self._status(sid, "awaiting_approval")

            # 同一张确认卡上可多轮澄清，直到 approve / revise / cancel
            while True:
                decision = self._wait_approval(sid, rt, aid)
                if decision is None:
                    self._status(sid, "cancelled")
                    return False
                kind, note = decision

                if kind == "message":
                    intent = classify_brief_message(note)
                    self.store.audit(
                        sid, "user", "brief_chat",
                        {"intent": intent, "text": note[:200]},
                    )
                    if intent == "approve":
                        self.store.decide_approval(aid, True, note=note or "（用户以消息确认）")
                        self._say(
                            sid, "approval_resolved",
                            {"approval_id": aid, "status": "approved"},
                        )
                        return True
                    if intent == "clarify":
                        # 对话通道：send 一轮回答，卡仍 pending，状态保持 awaiting_approval
                        self._say(
                            sid,
                            "system",
                            {"text": "确认卡仍有效。正在回答你的问题（不会重拟简报，也不会开始执行）…"},
                        )
                        clarify_prompt = (
                            "用户在确认简报期间追问（确认卡仍然有效）：\n"
                            f"{note}\n\n"
                            "请基于你刚才给出的行动简报直接回答。"
                            "不要重拟整份简报，不要开始执行，不要输出最终报告 JSON，"
                            "不要调用修改类工具；回答清楚接下来会做什么即可。\n"
                            "若用户要求「全部页预览/每一页都看」：明确告知会做**全书三角裁决**——"
                            "每轮 35B 主力 + MinerU/锚点对比，仅分歧页用 Cursor 视觉，"
                            "全部页归类后再写 page_plan 并导入；不是抽样凑合。"
                        )
                        if self._ask(sid, rt, clarify_prompt, perms) is None:
                            return False
                        self._status(sid, "awaiting_approval")
                        continue
                    # revise：作废当前卡，带意见重拟
                    self.store.decide_approval(
                        aid, False, note="（用户以消息形式要求修改简报）"
                    )
                    self._say(
                        sid, "approval_resolved",
                        {"approval_id": aid, "status": "superseded"},
                    )
                    feedback = note
                    break

                # 卡片按钮
                approved = kind == "approved"
                self._say(
                    sid,
                    "approval_resolved",
                    {"approval_id": aid, "status": "approved" if approved else "rejected"},
                )
                self.store.audit(
                    sid, "user", "brief_decision",
                    {"approved": approved, "note": note, "via": kind},
                )
                if approved:
                    return True
                feedback = note
                if kind == "rejected" and feedback:
                    self._say(sid, "user", {"text": feedback})
                break

            if not feedback:
                self._status(sid, "awaiting_user")
                got = self._wait_user(rt)
                if got is None:
                    self._status(sid, "cancelled")
                    return False
                feedback = got
                self._say(sid, "user", {"text": feedback})
            self._status(sid, "planning")
            if (
                self._ask(
                    sid,
                    rt,
                    f"用户打回了简报，意见：{feedback}\n请修订行动简报（仍不要执行修改类动作）。",
                    perms,
                )
                is None
            ):
                return False

    def _wait_approval(
        self, sid: str, rt: SessionRuntime, aid: str
    ) -> tuple[str, str] | None:
        """等待批准决定或用户消息（先到者优先）。

        返回 ("approved"|"rejected", note) / ("message", text)；取消返回 None。
        决定由 main.py 直接写 store，事件仅用于唤醒；用户消息从运行时队列取。
        """
        while True:
            rt.approval_event.wait(timeout=0.5)
            rt.approval_event.clear()
            if rt.cancelled.is_set():
                return None
            a = self.store.get_approval(aid)
            if a and a["status"] != "pending":
                return ("approved" if a["status"] == "approved" else "rejected"), str(
                    a.get("note") or ""
                )
            try:
                text = rt.user_messages.get_nowait()
            except queue.Empty:
                continue
            if text:
                return "message", text

    def _run_round(
        self,
        sid: str,
        rt: SessionRuntime,
        instruction: str,
        perms: Permissions,
        *,
        soft_chat: bool = False,
    ) -> FinalReport | None:
        """一轮连续自主执行：跑到底 → 提取最终报告；未交则追问（预算内）。

        soft_chat：续聊允许无 JSON 收尾，把助手文本当作 done（纯问答）。
        """
        text = self._ask(sid, rt, instruction, perms)
        if text is None:
            return None
        report = extract_final_report(text)
        if report is None and soft_chat:
            return FinalReport(
                status="done",
                summary=(text.strip()[:800] or "已回复"),
                evidence=["soft_chat"],
            )
        nudges = 0
        while report is None and nudges < perms.budgets.max_nudges:
            nudges += 1
            text = self._ask(sid, rt, _NUDGE_INSTRUCTION, perms)
            if text is None:
                return None
            report = extract_final_report(text)
        if report is None:
            report = FinalReport(
                status="failed",
                summary=f"Agent 追问 {perms.budgets.max_nudges} 次后仍未按契约交付最终报告",
            )
        return report

    def _new_snapshot(self, perms: Permissions) -> WorktreeSnapshot:
        """建立可回滚基线（白名单 + guarded_roots 源码），供每轮 fs_check 使用。"""
        snapshot = WorktreeSnapshot(
            self.settings.workspace_root,
            perms.write_whitelist,
            perms.forbidden,
            perms.guarded_roots,
            perms.backup_suffixes,
        )
        snapshot.backup_guarded()
        return snapshot

    def _fs_violation_stop(self, sid: str, snapshot: WorktreeSnapshot) -> bool:
        """每轮/每批查一次越权改动：命中即回滚并终止会话。返回 True 表示已终止。"""
        violation = self._fs_check(sid, snapshot)
        if not violation:
            return False
        self._say(sid, "system", {"text": violation})
        self._say(
            sid,
            "result",
            {"ok": False, "report": "文件改动越权，已回滚，会话终止。\n" + violation},
        )
        self._status(sid, "failed")
        return True

    def _fs_check(self, sid: str, snapshot: WorktreeSnapshot) -> str | None:
        """终检：白名单外改动，或有改动但从未获批 fs.request_edit → 全部回滚。

        返回违规说明（None = 通过）。
        """
        ok_files, bad_files = snapshot.changed_since()
        approved_edits = [
            a
            for a in self.store.list_approvals(sid, "fs.request_edit")
            if a["status"] == "approved"
        ]
        violation_files = list(bad_files)
        labels = list(bad_files)
        if ok_files and not approved_edits:
            violation_files += ok_files
            labels += [f"{f}（未经 fs.request_edit 批准）" for f in ok_files]
        if violation_files:
            msg = "fs_guard 查到越权文件改动：\n- " + "\n- ".join(labels[:20])
            return msg + "\n" + self._rollback_violations(snapshot, violation_files)
        if ok_files:
            # diff 卡：文件清单 + 补丁摘要，前端渲染为可折叠 diff 视图
            try:
                patch = snapshot.diff_patch(ok_files[:20])
            except Exception:  # noqa: BLE001 - diff 生成失败不影响流程
                log.warning("diff_patch failed", exc_info=True)
                patch = ""
            self._say(
                sid,
                "diff",
                {"files": ok_files[:20], "stat": snapshot.diff_stat()[:2000], "patch": patch},
            )
        return None

    def _rollback_violations(self, snapshot: WorktreeSnapshot, files: list[str]) -> str:
        """按文件清单还原越权改动（含白名单外、未跟踪文件），如实回报计数。"""
        try:
            restored, failed = snapshot.rollback_all_violations(files)
        except Exception as e:  # noqa: BLE001 - 回滚失败降级为消息，不炸会话
            log.exception("rollback violations failed")
            return f"回滚越权改动时出错：{e}（请人工检查上列文件）"
        msg = f"已回滚 {len(restored)}/{len(files)} 个越权改动"
        if failed:
            msg += f"；{len(failed)} 个未能还原，请人工检查：" + "; ".join(failed[:5])
        return msg

    def _rollback(self, snapshot: WorktreeSnapshot, reason: str) -> str:
        """按文件回滚白名单改动；回滚本身出错也不让会话崩溃。"""
        try:
            restored, failed = snapshot.rollback_whitelist()
        except Exception as e:  # noqa: BLE001 - 回滚失败降级为消息，不炸会话
            log.exception("rollback failed")
            return f"{reason}，且回滚白名单改动时出错：{e}"
        msg = f"{reason}，白名单文件改动已回滚（{len(restored)} 个文件）"
        if failed:
            msg += f"；{len(failed)} 个文件回滚失败，请人工检查：" + "; ".join(failed[:5])
        return msg

    def _wait_user(self, rt: SessionRuntime) -> str | None:
        """阻塞等待下一条非空用户消息；取消返回 None。"""
        while True:
            text = rt.user_messages.get()
            if rt.cancelled.is_set():
                return None
            if text:
                return text

    def _drain_user(self, rt: SessionRuntime) -> list[str]:
        """非阻塞取空排队中的用户消息（执行期间用户发的话，回合间隙送达 Agent）。"""
        out: list[str] = []
        while True:
            try:
                text = rt.user_messages.get_nowait()
            except queue.Empty:
                return out
            if text:
                out.append(text)
