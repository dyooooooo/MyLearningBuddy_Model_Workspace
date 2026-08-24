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
    cursor_model: str = "composer-2.5"
    cursor_eval_timeout_s: float = 300.0
    cursor_stall_timeout_s: float = 45.0

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


settings = Settings()
