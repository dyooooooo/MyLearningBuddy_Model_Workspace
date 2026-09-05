from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8200
    agent_home: str = "/data/agent"
    textbook_agent_token: str = "change-me-textbook-agent"
    cursor_api_key: str = ""
    cursor_model: str = "grok-4.6"
    cursor_model_effort: str = "high"
    cursor_model_fast: bool = True
    cursor_task_model: str = "grok-4.6"
    cursor_task_effort: str = "high"
    cursor_task_fast: bool = True
    # Agent runs use a long-lived HTTP/2 bidi stream. Inside Docker that stream
    # gets silently dropped by NAT / proxy idle timeouts and the run dies with
    # "Connection stalled repeatedly", so default the bridge to HTTP/1.1.
    cursor_use_http1: bool = True
    cursor_eval_timeout_s: float = 1800.0
    cursor_stall_timeout_s: float = 45.0
    cursor_send_timeout_s: float = 180.0
    # One shared local CursorClient. Official local runtime has no N-stream
    # product on that bridge; cap 4 let four chats abort each other. Queue
    # extras instead. Waiters are not "on the bridge" — see wait_send_slot.
    max_concurrent_sends: int = 1
    buddy_api_url: str = "http://buddy-api:8100"
    buddy_admin_token: str = ""
    page_sync_watch: bool = True
    page_sync_debounce_ms: int = 400

    @property
    def home(self) -> Path:
        return Path(self.agent_home)

    @property
    def textbooks_dir(self) -> Path:
        return self.home / "textbooks"

    @property
    def cursor_dir(self) -> Path:
        return self.home / ".cursor"

    @property
    def tools_dir(self) -> Path:
        return self.home / "tools"

    @property
    def traces_dir(self) -> Path:
        return self.home / "traces"

    @property
    def inbox_dir(self) -> Path:
        return self.home / "inbox"

    @property
    def prompts_dir(self) -> Path:
        return self.home / "prompts"

    @property
    def tasks_dir(self) -> Path:
        return self.home / "tasks"

    @property
    def memory_dir(self) -> Path:
        return self.home / "memory"

    @property
    def configs_dir(self) -> Path:
        return self.home / "configs"


settings = Settings()

# 侧栏标题是 16 字归纳，不走对话默认的 Grok Extra High。
TITLE_MODEL = "composer-2.5"


def default_model() -> str:
    return (settings.cursor_model or "grok-4.6").strip() or "grok-4.6"


def default_model_params() -> list[dict[str, str]]:
    effort = (settings.cursor_model_effort or "high").strip() or "high"
    return [
        {"id": "effort", "value": effort},
        {"id": "fast", "value": "true" if settings.cursor_model_fast else "false"},
    ]


def task_model() -> str:
    return (settings.cursor_task_model or default_model()).strip() or default_model()


def task_model_params() -> list[dict[str, str]]:
    effort = (settings.cursor_task_effort or settings.cursor_model_effort or "high").strip() or "high"
    return [
        {"id": "effort", "value": effort},
        {"id": "fast", "value": "true" if settings.cursor_task_fast else "false"},
    ]


def title_model() -> str:
    return TITLE_MODEL


def title_model_params() -> list[dict[str, str]]:
    return [{"id": "fast", "value": "true"}]
