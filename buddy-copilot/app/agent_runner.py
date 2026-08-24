"""Cursor SDK 封装：会话级 Agent 的创建、发送、最终报告提取、取消。

- 本地运行时（LocalAgentOptions.cwd = 工作区根），setting_sources=[] 钉扎；
- 全部工具经 inline MCP（stdio → app.mcp_server）暴露，会话 id / 场景经环境变量注入；
- resume 时 MCP 不持久化，必须重新传入（SDK 约束）。
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from pathlib import Path
from typing import Callable

# 必须在 import cursor_sdk 使用 Bridge 之前打补丁（token 以 '-' 开头会炸桥）
from .sdk_patches import apply_cursor_sdk_patches

apply_cursor_sdk_patches()

from cursor_sdk import (
    Agent,
    AgentOptions,
    CursorAgentError,
    LocalAgentOptions,
    LocalSendOptions,
    SendOptions,
    StdioMcpServerConfig,
)
from pydantic import ValidationError

from .config import SERVICE_ROOT, Settings
from .schemas import FinalReport

log = logging.getLogger("copilot.agent")

_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


class AgentRunError(RuntimeError):
    pass


def _run_bounded(fn: Callable[[], None], timeout: float, what: str) -> bool:
    """在守护线程中限时执行 SDK RPC（cancel/close 可能因桥接进程无响应而永久阻塞，
    绝不能让监督线程陪着挂死）。返回是否在限时内完成。"""
    t = threading.Thread(target=fn, daemon=True, name=f"copilot-bounded-{what}")
    t.start()
    t.join(timeout)
    if t.is_alive():
        log.warning("%s 超过 %ss 未返回，放弃等待（守护线程自生自灭）", what, timeout)
        return False
    return True


def extract_final_report(text: str) -> FinalReport | None:
    """取最后一个 ```json 块并校验为最终报告；无块或不合规返回 None。"""
    matches = _JSON_BLOCK.findall(text)
    if not matches:
        return None
    try:
        data = json.loads(matches[-1])
        return FinalReport.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        return None


def build_mcp_servers(
    settings: Settings, session_id: str = "", kind: str = ""
) -> dict[str, StdioMcpServerConfig]:
    """构造 buddy-copilot MCP（stdio）。create / resume 都必须传；
    会话 id / 场景经环境变量注入，供 gate、step 落库与场景级工具可见性使用。"""
    python = str(Path(sys.executable))
    env = {
        "PYTHONPATH": str(SERVICE_ROOT),
        "WORKSPACE_ROOT": str(settings.workspace_root),
        "BUDDY_API_BASE": settings.buddy_api_base,
        "GATEWAY_BASE": settings.gateway_base,
        "ADMIN_TOKEN": settings.resolve_admin_token(),
        "COPILOT_DB_PATH": str(settings.db_path),
        "COPILOT_SESSION_ID": session_id,
        "COPILOT_SESSION_KIND": kind,
    }
    return {
        "buddy-copilot": StdioMcpServerConfig(
            command=python,
            args=["-m", "app.mcp_server"],
            cwd=str(SERVICE_ROOT),
            env=env,
        )
    }


def resolve_model(requested: str | None, settings: Settings) -> str:
    """会话模型：请求值 > 配置默认 > auto（服务端自选）。"""
    m = (requested or "").strip()
    if not m or m in ("auto", "自动", "default"):
        m = (settings.cursor_model or "").strip()
    if not m or m in ("自动", "default"):
        m = "auto"
    return m


class SessionAgent:
    """一个 Copilot 会话对应一个 Cursor Agent（多轮，可 resume，可取消）。"""

    def __init__(
        self,
        settings: Settings,
        agent_id: str | None = None,
        model: str | None = None,
        session_id: str = "",
        kind: str = "",
        *,
        recreate_on_fail: bool = False,
    ) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._current_run = None
        self.recreated = False  # resume 失败后是否新建了 Agent
        self.model = resolve_model(model, settings)
        mcp_servers = build_mcp_servers(settings, session_id=session_id, kind=kind)

        def _opts(aid: str | None) -> AgentOptions:
            return AgentOptions(
                model=self.model,
                api_key=settings.cursor_api_key,
                agent_id=aid,
                mcp_servers=mcp_servers,
                local=LocalAgentOptions(
                    cwd=str(settings.workspace_root),
                    setting_sources=[],  # 钉扎：不读 ambient
                ),
            )

        try:
            if agent_id:
                # inline MCP 不随 resume 持久化，必须再次传入
                self._agent = Agent.resume(agent_id, _opts(agent_id))
            else:
                self._agent = Agent.create(_opts(None))
        except CursorAgentError as e:
            if agent_id and recreate_on_fail:
                log.warning("Agent.resume(%s) 失败，新建 Agent 承接：%s", agent_id, e)
                try:
                    self._agent = Agent.create(_opts(None))
                    self.recreated = True
                except CursorAgentError as e2:
                    raise AgentRunError(f"Cursor Agent 启动失败: {e2}") from e2
            else:
                raise AgentRunError(f"Cursor Agent 启动失败: {e}") from e

    @property
    def agent_id(self) -> str:
        return str(self._agent.agent_id)

    def ask(
        self,
        prompt: str,
        on_text: Callable[[str], None] | None = None,
        *,
        force: bool = False,
    ) -> str:
        """发送一轮消息（Agent 在这一轮内自主连续调工具），返回最终 assistant 文本。

        force=True：Local 侧 expire 卡住的 active run 再发（官方 SendOptions.local.force），
        用于重连/超时恢复，避免僵尸 run 阻塞下一轮 send。
        """
        with self._lock:
            try:
                opts = (
                    SendOptions(local=LocalSendOptions(force=True)) if force else None
                )
                run = self._agent.send(prompt, opts) if opts else self._agent.send(prompt)
                self._current_run = run
                log.info(
                    "run started: agent=%s run=%s force=%s",
                    self.agent_id,
                    run.id,
                    force,
                )
                chunks: list[str] = []
                for message in run.messages():
                    if getattr(message, "type", "") == "assistant":
                        for block in message.message.content:
                            if getattr(block, "type", "") == "text":
                                chunks.append(block.text)
                                if on_text:
                                    on_text(block.text)
                result = run.wait()
            except CursorAgentError as e:
                # NetworkError / Bridge RemoteProtocol 等也归入此类，由 loop 判定是否可重连
                raise AgentRunError(f"Agent 运行失败: {e}") from e
            except Exception as e:  # noqa: BLE001 - httpx 等底层断连偶发不包进 CursorAgentError
                raise AgentRunError(f"Agent 运行失败: {type(e).__name__}: {e}") from e
            finally:
                self._current_run = None
        if result.status == "error":
            raise AgentRunError(f"Agent 运行失败（run={result.id}）")
        return "".join(chunks)

    def cancel_current(self) -> None:
        """取消进行中的 run（用户取消 / 超时护栏）。失败降级为 close。

        所有 SDK RPC 都限时执行：桥接进程若已挂死，绝不拖垮调用线程。
        """
        run = self._current_run

        def do_cancel() -> None:
            try:
                if run is not None and hasattr(run, "cancel"):
                    run.cancel()
                    return
            except Exception:  # noqa: BLE001
                log.warning("run cancel failed", exc_info=True)
            try:
                self._agent.close()
            except Exception:  # noqa: BLE001
                log.warning("agent close failed during cancel", exc_info=True)

        _run_bounded(do_cancel, 15, "cancel_current")

    def close(self) -> None:
        def do_close() -> None:
            try:
                self._agent.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响主流程
                log.warning("agent close failed", exc_info=True)

        _run_bounded(do_close, 10, "agent_close")
