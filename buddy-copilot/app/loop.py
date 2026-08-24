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

import ast
import logging
import queue
import threading
import time
import traceback
from typing import Any, Callable

from .agent_env import build_context_pack
from .agent_runner import AgentRunError, SessionAgent, extract_final_report
from .bridge_cleanup import close_sdk_bridge, sweep_orphan_bridges
from .sdk_patches import is_bridge_auth_token_argv_error
from .brief_intent import classify_brief_message
from .config import Settings
from .continue_intent import is_continue_request
from .next_steps import (
    looks_like_finalize_request,
    looks_like_refuse_principle,
    next_steps_message,
)
from .permissions import Permissions, load_permissions
from .pattern_batch import (
    PATTERN_ABSTRACT_BATCH,
    PATTERN_ABSTRACT_MAX_ROUNDS,
    batch_instruction as pattern_batch_instruction,
    progress_text as pattern_progress_text,
    unassigned_count,
)
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
    AUTOFIX_MAX_ROUNDS,
    apply_qa_report_to_payload,
    autofix_may_finish,
    batch_actionable_findings,
    extract_qa_report,
    fix_instruction,
    fullbook_analyze_and_fix_instruction,
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
    remaining_actionable_findings,
    remaining_as_open_issues,
    user_accepts_suggestions,
    user_confirms_effective,
    user_wants_skip_and_continue,
)
from .pipeline_tick import pages_ready_n, symptom_rows_for_tick
from .store import Store
from .tools.buddy_api import BuddyApiClient
from .tools.fs_guard import WorktreeSnapshot

log = logging.getLogger("copilot.loop")


def wall_elapsed(
    started: float,
    *,
    now: float,
    paused: float = 0.0,
    pause_start: float | None = None,
) -> float:
    """单轮墙钟：等人点批准卡的时间不计入 ``run_timeout_s``。

    上次全书导入卡在 ``fs.request_edit`` 待批时，900 秒墙钟先于 1800 秒闸门超时
    把整轮掐死。待决批准期间停表，只统计 Agent 真正在跑的时间。
    """
    extra = (now - pause_start) if pause_start is not None else 0.0
    return max(0.0, now - started - paused - extra)

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
            "- 修改白名单内 .css/.html（前台显示）可直接编辑；改 prompt/测试/其它文件须先 "
            "fs.request_edit 获批；闸门被拒绝时按用户理由调整；\n"
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
        # 本轮执行结束后待用户「保留 / 放弃」的文件快照（类似 Cursor Agent Undo）
        self._checkpoints: dict[str, WorktreeSnapshot] = {}
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

    def cancel(self, sid: str, *, discard_files: bool = True) -> None:
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
        # 已挂起的检查点：停止 ≈ Cursor 放弃，默认回退文件
        if discard_files:
            self.discard_files(sid, reason="用户停止会话")

    def keep_files(self, sid: str, *, reason: str = "") -> dict[str, Any]:
        """保留本轮文件改动（释放检查点，不回滚）。"""
        return self._release_checkpoint(sid, discard=False, reason=reason)

    def discard_files(self, sid: str, *, reason: str = "") -> dict[str, Any]:
        """放弃本轮文件改动，回退到本轮执行前（类似 git checkout / Cursor Undo）。"""
        return self._release_checkpoint(sid, discard=True, reason=reason)

    def has_pending_checkpoint(self, sid: str) -> bool:
        with self._lock:
            return sid in self._checkpoints

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
        """若有未完成的 MinerU 全书导入/自动改/finalize，返回原因标签；否则 None。"""
        if session.get("kind") != "import_advice":
            return None
        payload = session.get("payload") or {}
        if not payload.get("minio_key"):
            return None
        if not payload.get("fullbook_autofix_done"):
            return "segment"
        if has_blocking_open_issues(payload):
            return "segment_issues"
        if (
            payload.get("import_job_id")
            and not payload.get("result_edition_id")
            and has_layout_or_effective_ledger(payload)
        ):
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

        agent: SessionAgent | None = None
        last_err: BaseException | None = None
        # 最多 3 次：死桥重置 + token 以 '-' 开头的已知 SDK 桥参数 bug
        for attempt in range(3):
            try:
                agent = _new_agent()
                break
            except AgentRunError as e:
                last_err = e
                log.warning(
                    "open_agent 失败 attempt=%s/%s sid=%s: %s",
                    attempt + 1,
                    3,
                    sid,
                    e,
                )
                try:
                    close_sdk_bridge()
                except Exception:  # noqa: BLE001
                    log.warning(
                        "close_sdk_bridge during open_agent retry failed",
                        exc_info=True,
                    )
                try:
                    sweep_orphan_bridges(grace_s=1.0)
                except Exception:  # noqa: BLE001
                    pass
                # 非「死桥 / token argv」类错误：重试一次后仍失败则抛出
                if attempt == 0:
                    continue
                if is_bridge_auth_token_argv_error(e) and attempt + 1 < 3:
                    continue
                raise
        if agent is None:
            assert last_err is not None
            raise last_err
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
        # 全书导入续聊：finalize / 跳过遗留 / 定点修 / 断点继续
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
            # 跳过遗留问题 → finalize 或结束
            if user_wants_skip_and_continue(text) and has_blocking_open_issues(payload):
                n = len(open_issue_pages(payload))
                payload["open_issues"] = mark_issues_status(
                    payload, status="skipped_by_user"
                )
                self.store.update_session(sid, payload=payload)
                self._say(
                    sid,
                    "system",
                    {"text": f"已跳过 {n} 个遗留问题页；进入收尾。"},
                )
                self._mineru_fullbook_finalize_or_done(sid, rt, perms)
                return
            # 修指定页 / 按建议改（事后定点优化）
            open_pages = open_issue_pages(payload)
            named_pages = mentioned_open_pages(text, open_pages)
            wants_fix = (
                has_blocking_open_issues(payload)
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
                        sid, preface="未识别要修的页。请说明修哪几页，或点「继续自动改」。"
                    )
                    self._status(sid, "awaiting_user")
                    return
                skipped = [p for p in open_pages if p not in set(pages)]
                if skipped:
                    payload["open_issues"] = mark_issues_status(
                        payload, status="skipped_by_user", pages=skipped
                    )
                    self.store.update_session(sid, payload=payload)
                from .segment_state import procedure_hints_instruction

                instr = fix_instruction(
                    job_id=str(payload.get("import_job_id") or ""),
                    pages=pages,
                    edition_id=str(payload.get("edition_id") or ""),
                    procedure_block=procedure_hints_instruction(payload),
                )
                self._execution_phase(sid, rt, instr, perms, soft_chat=True)
                self._sync_issues_from_last_report(sid)
                self._mineru_fullbook_finalize_or_done(sid, rt, perms)
                return
            if want_continue and unfinished:
                self._say(
                    sid,
                    "system",
                    {
                        "text": (
                            "已重连，从 MinerU 全书导入断点继续。"
                            + (self._preview_progress_hint(sid) or "")
                        )
                    },
                )
                self._mineru_fullbook_execution(sid, rt, perms)
                return
            # 自由文本：事后逐页优化
            if not want_continue:
                instruction = (
                    text
                    + "\n\n（会话续聊：直接处理这条用户消息。"
                    "若用户在问问题/看页面：用 page_html、必要时 render_preview，不要擅自改页。"
                    "若用户明确要求修改某页：才可用 get_blocks/replace_page_blocks/"
                    "patch_block/rerender_page/rebuild_ir，并以最终 HTML 验收。"
                    "优先读 layout_escalate_pages 与 layout_rule_id；禁止 35B reprocess refine。"
                    "确认有效的改动用 edition.record_effective_change 记账。"
                    "全书导入已完成时不要再 start_import。）"
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

        if session.get("kind") == "pattern_abstract" and want_continue:
            self._say(sid, "system", {"text": "已重连，接着抽象下一批改进点。"})
            self._pattern_abstract_execution(sid, rt, perms)
            return

        if want_continue and unfinished:
            self._say(
                sid,
                "system",
                {"text": "已重连，从断点继续。" + (self._preview_progress_hint(sid) or "")},
            )
            if unfinished in ("segment", "segment_issues", "finalize_edition"):
                self._mineru_fullbook_execution(sid, rt, perms)
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
                    "检测到未完成的全书导入断点。点「继续」或发送「继续」即可重连并从断点接着做。"
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

        # ---- 阶段 2~4：MinerU 全书导入 + Cursor 自动改（或普通执行）----
        if kind == "import_advice" and self._should_mineru_segment(sid):
            self._mineru_fullbook_execution(sid, rt, perms)
            return
        if kind == "pattern_abstract":
            self._pattern_abstract_execution(sid, rt, perms)
            return
        self._execution_phase(sid, rt, _EXECUTE_INSTRUCTION, perms)

    def _pattern_abstract_execution(
        self, sid: str, rt: SessionRuntime, perms: Permissions
    ) -> None:
        """改进点抽象：按「还剩多少条没挂模式」分批推进，一轮一批。

        单轮预算超限会整轮回滚，所以不让 Agent 一口气扫完 343 条。每批由
        _execution_phase 承担快照、越权检查与排队消息，批间只做进度与终止判断。
        """
        session = self.store.get_session(sid)
        assert session is not None
        payload = dict(session.get("payload") or {})
        fp = str(payload.get("fingerprint_hash") or "").strip()
        batch_size = int(payload.get("batch_size") or PATTERN_ABSTRACT_BATCH)
        client = BuddyApiClient(self.settings)

        def _remaining() -> tuple[int, int]:
            try:
                listing = client.ip_list(
                    fingerprint_hash=fp, status="accepted", prep_status="ready", limit=2000
                )
            except Exception as e:  # noqa: BLE001 - 取不到就退回单轮，不阻断会话
                log.warning("session %s ip_list failed: %s", sid, e)
                return -1, -1
            return unassigned_count(listing)

        remaining, total = _remaining()
        if remaining < 0:
            # 数不出剩余量（接口不通）：退回单轮执行，别把会话卡死在计数上
            self._execution_phase(sid, rt, _EXECUTE_INSTRUCTION, perms)
            return
        if remaining == 0:
            # 全挂完之后这条轨还要用来维护检测器（降噪、补反例、重跑），
            # 所以只在没给具体目标时才收工，否则按目标跑一轮。
            goal = str(payload.get("user_goal") or "").strip()
            if not goal:
                self._say(sid, "system", {"text": f"{total} 条改进点已全部挂到模式，无需抽象。"})
                self._say(
                    sid,
                    "result",
                    {"ok": True, "report": {"status": "done", "summary": "无待抽象改进点"}},
                )
                self._status(sid, "done")
                return
            self._say(
                sid,
                "system",
                {"text": f"{total} 条改进点已全部挂到模式，转为按目标执行检测器维护。"},
            )
            self._execution_phase(
                sid,
                rt,
                goal
                + "\n\n（抽象队列已清空，本轮不抽新改进点，只按上面的目标维护检测器："
                "改 YAML、补正反例、跑 checks.run_examples 与 harness.run_pytest、"
                "再 checks.dry_run 对比命中数。收尾按契约输出最终报告 JSON。）",
                perms,
            )
            return

        for round_no in range(1, PATTERN_ABSTRACT_MAX_ROUNDS + 1):
            if rt.cancelled.is_set() or sid in self._cancelled:
                self._status(sid, "cancelled")
                return
            self._say(
                sid,
                "system",
                {"text": pattern_progress_text(round_no=round_no, remaining=remaining, total=total)},
            )
            self.store.audit(
                sid,
                "orchestrator",
                "pattern_batch_start",
                {"round": round_no, "remaining": remaining, "total": total},
            )
            self._execution_phase(
                sid,
                rt,
                pattern_batch_instruction(
                    round_no=round_no,
                    remaining=remaining,
                    total=total,
                    batch_size=batch_size,
                ),
                perms,
            )
            after = self.store.get_session(sid) or {}
            if str(after.get("status") or "") != "done":
                # failed / blocked / cancelled / awaiting_user：状态与提示已由执行阶段给出
                return

            before = remaining
            remaining, total = _remaining()
            if remaining < 0:
                return
            self.store.audit(
                sid,
                "orchestrator",
                "pattern_batch_done",
                {"round": round_no, "assigned": max(0, before - remaining), "remaining": remaining},
            )
            if remaining == 0:
                self._say(sid, "system", {"text": f"抽象完成：{total} 条改进点全部有归属。"})
                return
            if remaining >= before:
                # 这一批一条都没挂上去，再推也是空转，交回给用户判断
                self._say_next_steps(
                    sid,
                    preface=(
                        f"第 {round_no} 批没有新增归属（仍剩 {remaining} 条）。"
                        "可能是剩下的条目抽不出跨书症状。请指示下一步。"
                    ),
                )
                self._status(sid, "awaiting_user")
                return

        self._say_continue_cta(
            sid,
            f"已推进 {PATTERN_ABSTRACT_MAX_ROUNDS} 批，还剩 {remaining} 条未挂模式。"
            "点「继续」或发送「继续」可接着抽象。",
        )
        self._status(sid, "awaiting_user")

    def _should_mineru_segment(self, sid: str) -> bool:
        """有 PDF + edition → 走 MinerU 全书导入 + Cursor 自动改。"""
        session = self.store.get_session(sid)
        if not session:
            return False
        payload = session.get("payload") or {}
        # 已有导入任务的空绑定会话：只供聚焦改页，禁止再跑全书导入
        if payload.get("attach_existing_job") or payload.get("fullbook_autofix_done"):
            return False
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

    def _emit_mineru_pipeline(
        self, sid: str, job_id: str, **fields: Any
    ) -> None:
        """中栏实时流水线：走既有 import_event，不另开通道。"""
        body: dict[str, Any] = {
            "event": "import_progress",
            "line": "mineru",
            "job_id": job_id,
        }
        for key, value in fields.items():
            if value is not None:
                body[key] = value
        self.store.add_message(sid, "import_event", body)

    def _wait_import_job(
        self,
        client: BuddyApiClient,
        job_id: str,
        *,
        sid: str = "",
        page_ranges: str | None = None,
        page_limit: int | None = None,
        timeout_s: float = 1800,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        last: dict[str, Any] = {}
        last_sig = ""
        while time.monotonic() < deadline:
            last = client.get_import_job(job_id)
            status = str(last.get("status") or "")
            meta = last.get("meta") if isinstance(last.get("meta"), dict) else {}
            ready_n = pages_ready_n(meta)
            phase = str(meta.get("pipeline_phase") or "")
            sig = f"{status}:{phase}:{ready_n}"
            if sid and sig != last_sig:
                last_sig = sig
                self._emit_mineru_pipeline(
                    sid,
                    job_id,
                    status=status,
                    pages_ready=ready_n,
                    pipeline_phase=phase or "mineru_parse",
                    page_limit=page_limit,
                    page_ranges=page_ranges,
                )
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

        否则会把本书写进别人的任务（曾把「第二册」页写进「第一册」job）。
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

    def _mineru_fullbook_execution(
        self, sid: str, rt: SessionRuntime, perms: Permissions
    ) -> None:
        """MinerU 全书一次导入 → 读 escalate → Cursor 自动改 → finalize（无用户门闸）。"""
        self._status(sid, "executing")
        session = self.store.get_session(sid)
        assert session is not None
        payload = dict(session.get("payload") or {})
        self._drop_foreign_job_binding(sid, payload, BuddyApiClient(self.settings))
        minio_key = str(payload.get("minio_key") or "")
        edition_id = str(payload.get("edition_id") or "").strip()
        page_count = int(payload.get("page_count") or 0)
        if not edition_id:
            self._say(
                sid,
                "system",
                {"text": "必须先选择教材方案（edition_id）才能 AI 全书导入。"},
            )
            self._status(sid, "failed")
            return
        payload.setdefault("source_edition_id", edition_id)
        client = BuddyApiClient(self.settings)
        snapshot = self._new_snapshot(perms)
        try:
            if page_count <= 0:
                self._say(
                    sid,
                    "system",
                    {
                        "text": (
                            "缺少 page_count。请在导入面板重新选择 PDF"
                            "（会探测页数）后再开 AI 导入。"
                        )
                    },
                )
                self._status(sid, "failed")
                return

            wait_ranges = f"1-{page_count}"
            timeout_s = float(max(1800, min(page_count * 90, 7200)))
            job_id = str(payload.get("import_job_id") or "").strip()

            # Resume: already autofixed → finalize/done only
            if payload.get("fullbook_autofix_done") and job_id:
                self._mineru_fullbook_finalize_or_done(sid, rt, perms)
                return

            already_ready = False
            if job_id:
                try:
                    job0 = client.get_import_job(job_id)
                    already_ready = import_batch_ready(
                        job0, page_ranges=wait_ranges
                    ) and str(job0.get("status") or "") != "failed"
                except Exception:  # noqa: BLE001
                    already_ready = False

            if not already_ready:
                self._say(
                    sid,
                    "system",
                    {
                        "text": (
                            f"开始 MinerU 全书导入：共 {page_count} 页；"
                            f"方案 `{edition_id}`。导入完成后将按 escalate 清单自动修改。"
                        )
                    },
                )
                try:
                    if not job_id:
                        out = client.start_pdf_import(
                            minio_key=minio_key,
                            edition_id=edition_id,
                            label=str(payload.get("label") or ""),
                            # Full book: no page_ranges / page_limit
                        )
                        job_id = str(out.get("id") or "")
                        payload["import_job_id"] = job_id
                        payload["job_id"] = job_id
                        self.store.update_session(sid, payload=payload)
                        try:
                            self.store.bind_import_job(sid, job_id)
                        except Exception:  # noqa: BLE001
                            log.exception(
                                "bind_import_job failed sid=%s job=%s", sid, job_id
                            )
                        self.store.add_message(
                            sid,
                            "import_event",
                            {
                                "event": "import_started",
                                "line": "mineru",
                                "job_id": job_id,
                                "page_ranges": wait_ranges,
                                "fullbook": True,
                                "edition_id": edition_id,
                            },
                        )
                    job = self._wait_import_job(
                        client,
                        job_id,
                        sid=sid,
                        page_ranges=wait_ranges,
                        timeout_s=timeout_s,
                    )
                    if str(job.get("status") or "") == "failed":
                        self._say_continue_cta(
                            sid,
                            f"MinerU 全书导入失败：{job.get('error')}\n"
                            "点「继续」可重试。",
                        )
                        self.store.update_session(sid, payload=payload)
                        self._status(sid, "failed")
                        return
                    if not import_batch_ready(job, page_ranges=wait_ranges):
                        self._say_continue_cta(
                            sid,
                            "MinerU 全书导入等待超时，页尚未全部就绪。"
                            "点「继续」可重试等待。",
                        )
                        self.store.update_session(sid, payload=payload)
                        self._status(sid, "failed")
                        return
                    note_imported_through(payload, page_count)
                    note_qa_through(payload, page_count)
                    payload["segment_next_page"] = page_count + 1
                    self.store.update_session(sid, payload=payload)
                    self.store.add_message(
                        sid,
                        "import_event",
                        {
                            "event": "import_done",
                            "line": "mineru",
                            "job_id": job_id,
                            "page_ranges": wait_ranges,
                            "fullbook": True,
                            "status": job.get("status"),
                            "ok": True,
                        },
                    )
                    self._say(
                        sid,
                        "system",
                        {
                            "text": (
                                f"✅ MinerU 全书 {page_count} 页已就绪（job `{job_id}`）。"
                                "中栏可浏览；开始读 escalate 并自动修改…"
                            )
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    log.exception("fullbook import failed sid=%s", sid)
                    self._say_continue_cta(
                        sid, f"全书导入异常：{e}\n点「继续」可重试。"
                    )
                    self.store.update_session(sid, payload=payload)
                    self._status(sid, "failed")
                    return
            else:
                note_imported_through(payload, page_count)
                note_qa_through(payload, page_count)
                payload["segment_next_page"] = page_count + 1
                self.store.update_session(sid, payload=payload)
                self._say(
                    sid,
                    "system",
                    {
                        "text": (
                            f"全书已导入（job `{job_id}`），跳过重复 MinerU，"
                            "继续 escalate 分析与自动修改…"
                        )
                    },
                )

            if rt.cancelled.is_set():
                self._status(sid, "cancelled")
                return

            # Pull escalate signals (A-layer cost cascade) + IP procedures
            escalate_pages: list[int] = []
            escalate_summary: dict = {}
            try:
                job = client.get_import_job(job_id)
                meta = job.get("meta") if isinstance(job.get("meta"), dict) else {}
                escalate_pages = [
                    int(p) for p in (meta.get("layout_escalate_pages") or []) if p
                ]
                raw_sum = meta.get("layout_escalate_summary") or {}
                if isinstance(raw_sum, dict):
                    escalate_summary = raw_sum
                procs = meta.get("improvement_procedures") or []
                if isinstance(procs, list) and procs:
                    payload["improvement_procedures"] = procs
                bf = meta.get("book_fingerprint") or {}
                if isinstance(bf, dict) and bf.get("hash"):
                    payload["book_fingerprint"] = bf
                if meta.get("bound_edition_id"):
                    payload["bound_edition_id"] = str(meta["bound_edition_id"])
                self.store.update_session(sid, payload=payload)
            except Exception as exc:  # noqa: BLE001
                log.info("fullbook escalate fetch failed job=%s: %s", job_id, exc)

            esc_hint = self._layout_escalate_hint(job_id)
            if esc_hint:
                self._say(sid, "system", {"text": esc_hint})

            finished = self._fullbook_autofix_until_verified(
                sid,
                rt,
                perms,
                client,
                snapshot,
                payload,
                job_id=job_id,
                page_count=page_count,
                edition_id=edition_id,
                escalate_pages=escalate_pages,
                escalate_summary=escalate_summary,
                wait_ranges=wait_ranges,
            )
            if not finished:
                return
            if payload.get("fullbook_autofix_done"):
                self._mineru_fullbook_finalize_or_done(sid, rt, perms)
        finally:
            if rt.cancelled.is_set() or sid in self._cancelled:
                self._discard_inflight_snapshot(sid, snapshot, reason="已停止")
            else:
                try:
                    ok_files, _bad = snapshot.changed_since()
                except Exception:  # noqa: BLE001
                    ok_files = []
                if ok_files:
                    self._emit_pending_diff(sid, snapshot, ok_files)
                    self._arm_checkpoint(sid, snapshot)
                else:
                    snapshot.cleanup()
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    def _fullbook_autofix_until_verified(
        self,
        sid: str,
        rt: SessionRuntime,
        perms: Permissions,
        client: BuddyApiClient,
        snapshot: WorktreeSnapshot,
        payload: dict[str, Any],
        *,
        job_id: str,
        page_count: int,
        edition_id: str,
        escalate_pages: list[int],
        escalate_summary: dict[str, Any],
        wait_ranges: str,
    ) -> bool:
        """自动改直到活清单没有可改症状。交 JSON 报告不能收口。

        返回 False 表示中途取消/失败，调用方应直接 return。
        """
        from .segment_state import procedure_hints_instruction, symptom_hints_instruction

        last_left: int | None = None
        stall = 0
        for round_no in range(1, AUTOFIX_MAX_ROUNDS + 1):
            if rt.cancelled.is_set():
                self.store.update_session(sid, payload=payload)
                self._status(sid, "cancelled")
                return False
            data = self._load_live_symptoms(client, job_id, refresh=True)
            actionable = remaining_actionable_findings(data)
            left = len(actionable)
            rows = symptom_rows_for_tick(data)
            payload["autofix_round"] = round_no
            payload["autofix_remaining"] = left
            self.store.update_session(sid, payload=payload)
            self._emit_mineru_pipeline(
                sid,
                job_id,
                pipeline_phase="symptom_scan" if left else "verify",
                autofix_round=round_no,
                autofix_remaining=left,
                symptom_rows=rows,
                known_n=len(data.get("known") or []) if isinstance(data, dict) else 0,
                unknown_n=len(data.get("unknown") or []) if isinstance(data, dict) else 0,
            )

            if autofix_may_finish(data):
                payload["fullbook_autofix_done"] = True
                self.store.update_session(sid, payload=payload)
                self._sync_issues_from_last_report(sid)
                self._emit_mineru_pipeline(
                    sid,
                    job_id,
                    pipeline_phase="close",
                    autofix_round=round_no,
                    autofix_remaining=0,
                    autofix_done=True,
                    symptom_rows=rows,
                )
                self._say(
                    sid,
                    "system",
                    {"text": f"活清单已无有手册可改的症状（第 {round_no} 轮核验）。"},
                )
                return True

            if last_left is not None and left >= last_left:
                stall += 1
            else:
                stall = 0
            last_left = left
            if stall >= 2:
                self._park_autofix_leftovers(
                    sid,
                    payload,
                    actionable,
                    wait_ranges,
                    reason="连续两轮数量没降",
                )
                return True

            batch = batch_actionable_findings(actionable)
            types = sorted({str(f.get("suspect_type") or "") for f in batch if f})
            type_hint = f"：{', '.join(types)}" if types else ""
            self._emit_mineru_pipeline(
                sid,
                job_id,
                pipeline_phase="autofix",
                autofix_round=round_no,
                autofix_remaining=left,
                batch_types=types,
                batch_n=len(batch),
                symptom_rows=rows,
            )
            self._say(
                sid,
                "system",
                {
                    "text": (
                        f"自动改第 {round_no} 轮：活清单还剩 {left} 处有手册可改"
                        f"（本批 {len(batch)} 处{type_hint}）。"
                        "交报告后会重跑检测，剩的再开一轮。"
                    )
                },
            )
            instr = fullbook_analyze_and_fix_instruction(
                job_id=job_id,
                page_count=page_count,
                edition_id=edition_id,
                escalate_pages=escalate_pages,
                escalate_summary=escalate_summary,
                open_pages=open_issue_pages(payload),
                procedure_block=procedure_hints_instruction(payload),
                symptom_block=symptom_hints_instruction(
                    data, batch=batch, round_no=round_no, remaining_n=left
                ),
            )
            fix_wall = max(
                int(perms.budgets.run_timeout_s), min(int(page_count) * 20, 1800)
            )
            text = self._ask(sid, rt, instr, perms, run_timeout_s=fix_wall)
            if text is None or rt.cancelled.is_set():
                self.store.update_session(sid, payload=payload)
                if rt.cancelled.is_set():
                    self._status(sid, "cancelled")
                return False
            if self._fs_violation_stop(sid, snapshot):
                self.store.update_session(sid, payload=payload)
                return False
            report = extract_qa_report(text)
            if report:
                apply_qa_report_to_payload(payload, report, batch=wait_ranges)
                self.store.update_session(sid, payload=payload)

        data = self._load_live_symptoms(client, job_id, refresh=True)
        if autofix_may_finish(data):
            payload["fullbook_autofix_done"] = True
            self.store.update_session(sid, payload=payload)
            self._sync_issues_from_last_report(sid)
            self._emit_mineru_pipeline(
                sid,
                job_id,
                pipeline_phase="close",
                autofix_round=AUTOFIX_MAX_ROUNDS,
                autofix_remaining=0,
                autofix_done=True,
                symptom_rows=symptom_rows_for_tick(data),
            )
            return True
        self._park_autofix_leftovers(
            sid,
            payload,
            remaining_actionable_findings(data),
            wait_ranges,
            reason=f"已跑满 {AUTOFIX_MAX_ROUNDS} 轮",
        )
        return True

    def _load_live_symptoms(
        self, client: BuddyApiClient, job_id: str, *, refresh: bool
    ) -> dict[str, Any]:
        try:
            if refresh:
                return client.refresh_job_symptoms(job_id)
            return client.job_symptoms(job_id)
        except Exception as exc:  # noqa: BLE001
            log.info(
                "fullbook symptoms %s failed job=%s: %s",
                "refresh" if refresh else "get",
                job_id,
                exc,
            )
            if refresh:
                try:
                    return client.job_symptoms(job_id)
                except Exception as exc2:  # noqa: BLE001
                    log.info(
                        "fullbook symptoms fallback failed job=%s: %s", job_id, exc2
                    )
            return {}

    def _park_autofix_leftovers(
        self,
        sid: str,
        payload: dict[str, Any],
        leftover: list[dict[str, Any]],
        wait_ranges: str,
        *,
        reason: str,
    ) -> None:
        issues = remaining_as_open_issues(leftover, batch=wait_ranges)
        if issues:
            payload["open_issues"] = merge_open_issues(payload, issues)
        payload["fullbook_autofix_done"] = False
        self.store.update_session(sid, payload=payload)
        self._sync_issues_from_last_report(sid)
        job_id = str(payload.get("import_job_id") or payload.get("job_id") or "")
        if job_id:
            self._emit_mineru_pipeline(
                sid,
                job_id,
                pipeline_phase="parked",
                autofix_remaining=len(leftover),
                autofix_done=False,
                park_reason=reason,
                symptom_rows=symptom_rows_for_tick({"known": leftover, "unknown": []}),
            )
        self._say(
            sid,
            "system",
            {
                "text": (
                    f"自动改未收口：还剩 {len(leftover)} 处有手册可改（{reason}）。"
                    "点「继续」会按活清单再跑，不会把交报告当成全书改完。"
                )
            },
        )
        self._say_next_steps(sid)
        self._status(sid, "awaiting_user")

    def _mineru_fullbook_finalize_or_done(
        self, sid: str, rt: SessionRuntime, perms: Permissions
    ) -> None:
        """账本 → finalize；否则直接 done。无手册的未知疑点不挡结束。"""
        session = self.store.get_session(sid)
        if not session:
            self._status(sid, "failed")
            return
        payload = dict(session.get("payload") or {})
        if (
            has_layout_or_effective_ledger(payload)
            and not payload.get("result_edition_id")
            and not rt.cancelled.is_set()
        ):
            self._say(
                sid,
                "system",
                {"text": "正在根据账本沉淀新排版方案草稿…"},
            )
            fin = self._ask(
                sid,
                rt,
                "全书 MinerU 导入与自动修改已完成。请调用 edition.finalize_from_session"
                "（会写入 layout_change_log），"
                "然后输出最终报告 ```json（含 job_id、import_line=mineru、"
                "result_edition_id）。",
                perms,
            )
            if fin is None and not rt.cancelled.is_set():
                self._say_next_steps(
                    sid, preface="finalize 未完成，可点「生成新排版方案」重试。"
                )
                self._status(sid, "awaiting_user")
                return
            payload = dict((self.store.get_session(sid) or {}).get("payload") or payload)

        self.store.update_session(sid, payload=payload)
        leftover = open_issue_pages(payload)
        leftover_hint = (
            f" 仍有遗留页 {leftover}（可事后在对话里逐页优化）。"
            if leftover
            else ""
        )
        result_eid = str(payload.get("result_edition_id") or "").strip()
        forked = bool(payload.get("result_edition_forked"))
        if result_eid and forked:
            edition_hint = f" 已新建方案草稿：`{result_eid}`。"
        elif result_eid:
            edition_hint = f" 关联方案草稿：`{result_eid}`（非本轮新建，请核对待审议）。"
        else:
            edition_hint = " 无新沉淀，沿用原方案。"
        self._say_next_steps(
            sid,
            preface=(
                f"MinerU 全书导入完成。job_id=`{payload.get('import_job_id')}`。"
                + edition_hint
                + leftover_hint
            ),
        )
        self._say(
            sid,
            "result",
            {
                "ok": True,
                "report": {
                    "status": "done",
                    "summary": "MinerU 全书导入完成",
                    "job_id": payload.get("import_job_id"),
                    "import_line": "mineru",
                    "result_edition_id": payload.get("result_edition_id") or "",
                    "leftover_pages": leftover,
                },
            },
        )
        self._status(sid, "done")

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
            # 预览外层快照：仅在停止时回退；成功收尾的挂起由 _execution_phase 负责
            if rt.cancelled.is_set() or sid in self._cancelled:
                self._discard_inflight_snapshot(sid, snapshot, reason="已停止")
            else:
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
        self._auto_keep_pending(sid)
        snapshot = self._new_snapshot(perms)
        # cleanup | arm（成功挂起待用户确认）| cancel（停止并回退）
        outcome = "cleanup"
        try:
            instruction = first_instruction
            while True:
                report = self._run_round(sid, rt, instruction, perms, soft_chat=soft_chat)
                if report is None:
                    # 终止状态（cancelled/failed）由 _ask 内部置好
                    if rt.cancelled.is_set() or sid in self._cancelled:
                        outcome = "cancel"
                    return

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
                    outcome = "arm"
                    return
                if report.status == "failed":
                    self._say(sid, "system", {"text": self._rollback(snapshot, "任务失败")})
                    self._say(sid, "result", {"ok": False, "report": report.model_dump()})
                    self._status(sid, "failed")
                    return

                # blocked：等用户输入后继续（快照保持，便于最终回退）
                self._say(sid, "result", {"ok": False, "blocked": True, "report": report.model_dump()})
                self._status(sid, "awaiting_user")
                text = self._wait_user(rt)
                if text is None:
                    self._status(sid, "cancelled")
                    outcome = "cancel"
                    return
                self._say(sid, "user", {"text": text})
                self._status(sid, "executing")
                instruction = text + "\n\n（请继续自主执行，收尾时按契约输出最终报告 JSON。）"
        finally:
            if outcome == "arm":
                try:
                    ok_files, _bad = snapshot.changed_since()
                except Exception:  # noqa: BLE001
                    ok_files = []
                if ok_files:
                    self._emit_pending_diff(sid, snapshot, ok_files)
                    self._arm_checkpoint(sid, snapshot)
                else:
                    snapshot.cleanup()
            elif outcome == "cancel":
                self._discard_inflight_snapshot(sid, snapshot, reason="已停止")
            else:
                snapshot.cleanup()

    # ---------- 子流程 ----------

    def _ask(
        self,
        sid: str,
        rt: SessionRuntime,
        prompt: str,
        perms: Permissions,
        *,
        run_timeout_s: int | None = None,
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
                return self._ask_once(
                    sid, rt, prompt, perms, force=force, run_timeout_s=run_timeout_s
                )
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

    def _layout_escalate_hint(self, job_id: str) -> str:
        """Job meta 里低置信页清单 → Cursor SDK 升级入口文案。"""
        if not job_id:
            return ""
        try:
            job = BuddyApiClient(self.settings).get_import_job(job_id)
        except Exception as exc:  # noqa: BLE001
            log.info("layout escalate hint skipped job=%s: %s", job_id, exc)
            return ""
        meta = job.get("meta") if isinstance(job, dict) else None
        if not isinstance(meta, dict):
            return ""
        pages = [int(p) for p in (meta.get("layout_escalate_pages") or []) if p]
        if not pages:
            return ""
        head = ", ".join(str(p) for p in pages[:24])
        more = f" …共 {len(pages)} 页" if len(pages) > 24 else f"（{len(pages)} 页）"
        return (
            f"低置信页待 Cursor 判定{more}：{head}。"
            "请按 page-diagnosis 通道 0 优先处理；"
            "默认改规则 YAML / patch_block，禁止 35B refine。"
        )

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
            return f"当前全书共 {page_count} 页，全书导入尚未写入断点。"
        extra = f"；遗留问题页 {open_n} 个" if open_n else ""
        if page_count > 0 and done >= page_count:
            autofix = "；自动改已完成" if payload.get("fullbook_autofix_done") else ""
            return f"MinerU 全书断点：已导入 {done}/{page_count} 页（全书已齐）{autofix}{extra}。"
        return f"MinerU 全书断点：已导入约 {done}/{page_count} 页{extra}。"

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
        run_timeout_s: int | None = None,
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

        wall = run_timeout_s or perms.budgets.run_timeout_s or self.settings.agent_run_timeout
        idle_limit = perms.budgets.idle_timeout_s
        th = threading.Thread(target=target, daemon=True, name=f"copilot-ask-{sid}")
        started = time.time()
        last_heartbeat = started
        paused = 0.0
        pause_start: float | None = None
        th.start()
        while True:
            th.join(5)
            if not th.is_alive():
                break
            if rt.cancelled.is_set():
                agent.cancel_current()  # 已限时
                th.join(10)
                break
            if self.store.has_pending_approval(sid):
                if pause_start is None:
                    pause_start = time.time()
            elif pause_start is not None:
                paused += time.time() - pause_start
                pause_start = None
            elapsed = wall_elapsed(
                started, now=time.time(), paused=paused, pause_start=pause_start
            )
            # 心跳只打服务端日志，不写入对话气泡（文案易陈旧/张冠李戴，干扰阅读）
            if time.time() - last_heartbeat >= 45:
                idle = self._idle_seconds(sid, activity["ts"])
                hint = self._preview_progress_hint(sid)
                log.info(
                    "copilot run heartbeat sid=%s elapsed=%ss idle=%ss %s",
                    sid,
                    int(elapsed),
                    int(idle),
                    hint or "waiting agent output",
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
            perms.gated_subpaths,
        )
        snapshot.backup_guarded()
        return snapshot

    def _auto_keep_pending(self, sid: str) -> None:
        """续跑前：上一轮待确认改动视为用户已接受（继续对话 = 保留）。"""
        with self._lock:
            snap = self._checkpoints.pop(sid, None)
        if snap is None:
            return
        try:
            snap.cleanup()
        except Exception:  # noqa: BLE001
            log.exception("auto-keep cleanup failed sid=%s", sid)
        self._say(
            sid,
            "system",
            {"text": "继续对话：上一轮文件改动已自动保留。"},
        )
        self._say(sid, "diff_resolved", {"action": "keep", "auto": True})
        self.store.audit(sid, "orchestrator", "fs_auto_keep", {})

    def _arm_checkpoint(self, sid: str, snapshot: WorktreeSnapshot) -> None:
        """挂起快照，等待用户保留/放弃（勿 cleanup）。"""
        with self._lock:
            old = self._checkpoints.pop(sid, None)
            self._checkpoints[sid] = snapshot
        if old is not None:
            try:
                old.cleanup()
            except Exception:  # noqa: BLE001
                log.exception("replace checkpoint cleanup failed sid=%s", sid)

    def _emit_pending_diff(self, sid: str, snapshot: WorktreeSnapshot, files: list[str]) -> None:
        """可操作的 diff 卡：保留 / 放弃并回退。"""
        try:
            patch = snapshot.diff_patch(files[:20])
        except Exception:  # noqa: BLE001
            log.warning("diff_patch failed", exc_info=True)
            patch = ""
        try:
            stat = snapshot.diff_stat(files)[:2000]
        except Exception:  # noqa: BLE001
            stat = ""
        self._say(
            sid,
            "diff",
            {
                "files": files[:20],
                "stat": stat,
                "patch": patch,
                "pending": True,
                "hint": "类似 Cursor：可保留本轮改动，或回退到本轮执行前的版本",
            },
        )

    def _release_checkpoint(
        self, sid: str, *, discard: bool, reason: str = ""
    ) -> dict[str, Any]:
        with self._lock:
            snap = self._checkpoints.pop(sid, None)
        if snap is None:
            return {
                "ok": False,
                "error": "没有待确认的文件改动",
                "action": "discard" if discard else "keep",
                "restored": [],
                "failed": [],
            }
        restored: list[str] = []
        failed: list[str] = []
        try:
            if discard:
                try:
                    ok, bad = snap.changed_since()
                except Exception:  # noqa: BLE001
                    ok, bad = [], []
                files = ok + bad
                if files:
                    r1, f1 = snap.rollback_all_violations(files)
                    restored.extend(r1)
                    failed.extend(f1)
                r2, f2 = snap.rollback_whitelist()
                restored.extend(r2)
                failed.extend(f2)
                text = f"已放弃改动并回退到本轮执行前（{len(restored)} 处）。"
                if reason:
                    text = f"{reason}：{text}"
            else:
                text = "已保留本轮文件改动。"
                if reason:
                    text = f"{text}（{reason}）"
            self._say(sid, "system", {"text": text})
            self._say(
                sid,
                "diff_resolved",
                {
                    "action": "discard" if discard else "keep",
                    "restored": restored[:40],
                    "failed": failed[:10],
                    "reason": reason,
                },
            )
            self.store.audit(
                sid,
                "user" if reason != "用户停止会话" else "user",
                "fs_discard" if discard else "fs_keep",
                {"restored": len(restored), "failed": len(failed), "reason": reason},
            )
            return {
                "ok": True,
                "action": "discard" if discard else "keep",
                "restored": restored,
                "failed": failed,
            }
        finally:
            try:
                snap.cleanup()
            except Exception:  # noqa: BLE001
                log.exception("checkpoint cleanup failed sid=%s", sid)

    def _discard_inflight_snapshot(
        self, sid: str, snapshot: WorktreeSnapshot, *, reason: str
    ) -> None:
        """执行中取消：从内存快照回退，不经过 _checkpoints。"""
        try:
            ok, bad = snapshot.changed_since()
        except Exception:  # noqa: BLE001
            ok, bad = [], []
        files = ok + bad
        restored: list[str] = []
        failed: list[str] = []
        if files:
            restored, failed = snapshot.rollback_all_violations(files)
        r2, f2 = snapshot.rollback_whitelist()
        restored.extend(r2)
        failed.extend(f2)
        if restored or failed or files:
            self._say(
                sid,
                "system",
                {"text": f"{reason}：已回退文件改动（{len(restored)} 处）。"},
            )
            self._say(
                sid,
                "diff_resolved",
                {
                    "action": "discard",
                    "reason": reason,
                    "restored": restored[:40],
                    "failed": failed[:10],
                },
            )
        try:
            snapshot.cleanup()
        except Exception:  # noqa: BLE001
            log.exception("inflight discard cleanup failed sid=%s", sid)

    def _fs_violation_stop(self, sid: str, snapshot: WorktreeSnapshot) -> bool:
        """每轮/每批查一次越权改动：命中即回滚并终止会话。返回 True 表示已终止。"""
        violation, bad_paths = self._fs_check(sid, snapshot)
        if not violation:
            return False
        # 回滚前已记下路径：落入方案「越权」待审议，便于后期集中改 Pipeline
        self._record_authority_item(
            sid,
            authority="pipeline",
            summary="检测到越权文件改动（已回滚）",
            paths=bad_paths,
            mode="fs_violation",
            detail=violation[:800],
        )
        self._say(sid, "system", {"text": violation})
        self._say(
            sid,
            "result",
            {"ok": False, "report": "文件改动越权，已回滚，会话终止。\n" + violation},
        )
        self._status(sid, "failed")
        return True

    def _record_authority_item(
        self,
        sid: str,
        *,
        authority: str,
        summary: str,
        paths: list[str] | None = None,
        mode: str = "",
        detail: str = "",
    ) -> None:
        """把改动记入会话账本；若有绑定方案则 enqueue 待审议（带 authority）。"""
        from .authority import authority_label, classify_authority, stamp_authority
        from .segment_state import append_effective_change

        auth = classify_authority(
            mode=mode, paths=paths, explicit=authority
        )
        sess = self.store.get_session(sid) or {}
        pl = dict(sess.get("payload") or {})
        edition_id = str(
            pl.get("result_edition_id")
            or pl.get("edition_id")
            or pl.get("source_edition_id")
            or ""
        ).strip()
        entry = stamp_authority(
            {
                "summary": summary,
                "paths": list(paths or [])[:20],
                "mode": mode,
                "detail": (detail or "")[:800],
                "proposer": "system",
                "authority": auth,
            }
        )
        # Upstream cause: user request → AI actions (not just the guard effect)
        chain: dict = {}
        if mode == "fs_violation" or auth == "pipeline":
            try:
                from .causal_trace import trace_fs_violation

                chain = trace_fs_violation(
                    self.store.get_messages(sid),
                    paths=list(paths or []),
                    detail=detail or "",
                )
                if chain.get("user_input"):
                    # Keep full user text — do not truncate dialogue
                    entry["user_input"] = str(chain["user_input"])
                # Page comes from user「聚焦目标」dialogue — never invent p0 for a page chat
                if int(chain.get("page_no") or 0) > 0:
                    entry["page"] = int(chain["page_no"])
                entry["causal_chain"] = {
                    "user_requests": [str(x) for x in (chain.get("user_requests") or [])],
                    "ai_summary": str(chain.get("ai_summary") or "")[:1500],
                    "ai_tools": list(chain.get("ai_tools") or [])[:12],
                    "narrative": str(chain.get("narrative") or "")[:2500],
                    "guard_seq": int(chain.get("guard_seq") or 0),
                    "page_no": int(chain.get("page_no") or 0),
                    "block_id": str(chain.get("block_id") or ""),
                    "job_id": str(chain.get("job_id") or ""),
                    "dialogue_slot": str(chain.get("dialogue_slot") or ""),
                    "user_input_display": str(chain.get("user_input_display") or ""),
                }
            except Exception:  # noqa: BLE001
                log.exception("causal_trace failed sid=%s", sid)
                chain = {}
        pl["effective_changes"] = append_effective_change(pl, entry)
        self.store.update_session(sid, payload=pl)

        if not edition_id:
            self._say(
                sid,
                "system",
                {
                    "text": (
                        f"已标注为「{authority_label(auth)}」，但会话未绑定方案，"
                        "未能写入待审议队列。"
                    )
                },
            )
            return
        try:
            client = BuddyApiClient(self.settings)
            import time as _time

            page_no = int(entry.get("page") or chain.get("page_no") or 0)
            block_id = str(chain.get("block_id") or "")
            job_id = str(
                chain.get("job_id")
                or pl.get("import_job_id")
                or pl.get("job_id")
                or ""
            ).strip()
            cand = {
                "id": f"ai.{auth}.{int(_time.time()) % 10_000_000}",
                "facet": "content_correct" if auth == "pipeline" else "layout_rebuild",
                "severity": "should",
                "statement": (summary + (" | " + detail if detail else ""))[:500],
                "authority": auth,
                "mode": mode,
                "paths": list(paths or [])[:20],
                "page": page_no,
                "page_no": page_no,
                "block_ids": [block_id] if block_id else [],
                "user_input": entry.get("user_input") or "",
                "causal_chain": entry.get("causal_chain") or {},
                "tags": [f"authority:{auth}", authority_label(auth), "ai_change"],
            }
            out = client.enqueue_correction(
                edition_id, cand, source="ai_authority", source_ref=sid[:16]
            )
            # One dialogue → one improvement point, page from user focus
            if mode == "fs_violation" and job_id:
                try:
                    dlg_slot = str(
                        chain.get("dialogue_slot")
                        or f"fs:{int(chain.get('guard_seq') or _time.time())}"
                    )
                    client.ip_confirm(
                        {
                            "job_id": job_id,
                            "session_id": sid,
                            "page_no": page_no,
                            "summary": summary,
                            "authority": auth,
                            "kind": mode or "fs_violation",
                            "block_ids": [block_id] if block_id else [],
                            "slot": dlg_slot,
                            "procedure": {
                                "steps": [
                                    {
                                        "tool": mode or "fs_guard",
                                        "args_summary": (detail or summary)[:300],
                                    }
                                ],
                                "verify": "fs_guard_rollback",
                            },
                            "concrete": {
                                "origin": "pipeline_event",
                                "scope": "page" if page_no else "session",
                                "page_no": page_no,
                                "block_id": block_id,
                                "paths": list(paths or [])[:20],
                                "detail": (detail or "")[:2000],
                                "mode": mode,
                                "proposer": "system",
                                "user_input": entry.get("user_input") or "",
                                "causal_chain": entry.get("causal_chain") or {},
                            },
                            "write_correction": False,
                            "origin": "pipeline_event",
                        }
                    )
                except Exception:  # noqa: BLE001
                    log.exception("ip_confirm after fs_violation failed sid=%s", sid)
            corr = str(out.get("id") or "")[:8]
            self._say(
                sid,
                "system",
                {
                    "text": (
                        f"已记入方案 `{edition_id}` 待审议（{authority_label(auth)}"
                        + (f"；{corr}…" if corr else "")
                        + "）。"
                    ),
                    "actions": [
                        {
                            "id": "open_corrections",
                            "label": "打开待审议",
                            "send": f"__ui:open_corrections:{edition_id}",
                        }
                    ],
                },
            )
        except Exception:  # noqa: BLE001
            log.exception("enqueue authority correction failed sid=%s", sid)

    def _approved_edit_paths(self, sid: str) -> list[str]:
        """本会话已获批的写盘路径清单（去重、保序）。

        两张批准卡都算：``fs.request_edit`` 管声明文件，
        ``vocab.request_plugin_edit`` 管 vocab_ext/ 下会被执行的受限 Python。
        后者是唯一能让 ``covered_by_approval`` 放行 gated 子路径的入口。
        """
        paths: list[str] = []
        for tool in ("fs.request_edit", "vocab.request_plugin_edit", "tool_write_scope"):
            for a in self.store.list_approvals(sid, tool):
                if a["status"] != "approved":
                    continue
                action = a.get("action") or {}
                scope = action.get("scope_paths")
                if isinstance(scope, list):
                    paths.extend(str(p) for p in scope)
                    continue
                # 旧会话没有 scope_paths：退回解析 args 摘要里的 paths 字面量。
                raw = str((action.get("args") or {}).get("paths") or "")
                if raw and not raw.endswith("…"):
                    try:
                        parsed = ast.literal_eval(raw)
                    except (ValueError, SyntaxError):
                        parsed = None
                    if isinstance(parsed, (list, tuple)):
                        paths.extend(str(p) for p in parsed)
        return list(dict.fromkeys(paths))

    def _fs_check(self, sid: str, snapshot: WorktreeSnapshot) -> tuple[str | None, list[str]]:
        """终检：白名单外改动、未获批改动、超出爆炸半径的改动 → 回滚。

        批准按路径求交，不按会话整体放行：批了一个 prompt 模板不等于整个白名单
        开闸，这是「越权」最常见的入口。CSS/HTML 等前台显示资产
        （permissions.yaml auto_allow_suffixes）在白名单内免闸门。
        返回 (违规说明 | None, 相关路径)。
        """
        from .authority import classify_authority
        from .tools.fs_guard import covered_by_approval, is_auto_allowed

        ok_files, bad_files = snapshot.changed_since()
        perms = load_permissions(self.settings.agent_env_dir)
        auto_files = [
            f
            for f in ok_files
            if is_auto_allowed(
                f,
                perms.write_whitelist,
                perms.forbidden,
                perms.auto_allow_suffixes,
                perms.gated_subpaths,
            )
        ]
        gated_files = [f for f in ok_files if f not in auto_files]
        approved_paths = self._approved_edit_paths(sid)
        unapproved = [
            f
            for f in gated_files
            if not covered_by_approval(f, approved_paths, perms.gated_subpaths)
        ]
        violation_files = list(bad_files)
        labels = list(bad_files)
        if unapproved:
            violation_files += unapproved
            scope_hint = "、".join(approved_paths[:5]) or "（本会话尚无获批路径）"
            labels += [
                f"{f}（不在已批准范围内；已批准：{scope_hint}）" for f in unapproved
            ]

        blast = self._blast_radius_violation(snapshot, ok_files, perms)
        if blast and not violation_files:
            violation_files = list(ok_files)
            labels = [blast]
        elif blast:
            labels.append(blast)

        if violation_files:
            msg = "fs_guard 查到越权文件改动：\n- " + "\n- ".join(labels[:20])
            return (
                msg + "\n" + self._rollback_violations(snapshot, violation_files),
                list(dict.fromkeys(violation_files)),
            )
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
                {"files": ok_files[:20], "stat": snapshot.diff_stat(ok_files[:20])[:2000], "patch": patch},
            )
            # 获批的核心代码改动 → 标为「越权」落入待审议，便于后期集中改 Pipeline
            pipeline_files = [
                f
                for f in ok_files
                if classify_authority(paths=[f], mode="fs_edit") == "pipeline"
            ]
            if pipeline_files:
                self._record_authority_item(
                    sid,
                    authority="pipeline",
                    summary="已批准的 Pipeline 核心代码改动",
                    paths=pipeline_files,
                    mode="fs_edit",
                )
        return None, []

    def _blast_radius_violation(
        self,
        snapshot: WorktreeSnapshot,
        files: list[str],
        perms: Permissions,
    ) -> str | None:
        """一轮改动面是否超出预算。超限说明 Agent 在大范围重写，不是在修一处。"""
        max_files = perms.budgets.max_changed_files
        if max_files and len(files) > max_files:
            return f"改动面超限：本轮改了 {len(files)} 个文件（上限 {max_files}）"
        max_lines = perms.budgets.max_changed_lines
        if not max_lines:
            return None
        try:
            lines = snapshot.changed_line_count(files)
        except Exception:  # noqa: BLE001 - 统计失败不该顶替真实违规判定
            log.warning("changed_line_count failed", exc_info=True)
            return None
        if lines > max_lines:
            return f"改动面超限：本轮改了 {lines} 行（上限 {max_lines}）"
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
