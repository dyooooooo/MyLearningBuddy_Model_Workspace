"""Buddy Copilot 配置。

所有值可用环境变量覆盖；ADMIN_TOKEN 缺省时自动从 learning-buddy-c2/.env 读取，
避免令牌散落两处。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=SERVICE_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Cursor SDK
    cursor_api_key: str = ""
    # 默认「自动」：让服务端选模型；前端可从 /models 列表另选
    cursor_model: str = "auto"

    # 服务
    copilot_host: str = "127.0.0.1"
    copilot_port: int = 8200

    # 工作区与下游服务
    workspace_root: Path = SERVICE_ROOT.parent
    buddy_api_base: str = "http://127.0.0.1:8100"
    gateway_base: str = "http://127.0.0.1:8000"  # c2-gateway（minio.stat 预检用）
    admin_token: str = ""
    # 网关 /api/v1/studio/* 走 StudioAuthMiddleware，缺令牌一律 401
    studio_token: str = ""

    # 本地 vLLM（三角裁决 35B 主力；宿主机直连）
    vllm_base_url: str = "http://127.0.0.1:8088/v1"
    vllm_model: str = "qwen38-27b-nvfp4"
    vllm_api_token: str = ""

    # MinerU 辅助锚点
    mineru_api_token: str = ""
    mineru_api_base_url: str = "https://mineru.net"
    mineru_model_version: str = "vlm"
    mineru_language: str = "ch"
    mineru_enable_formula: bool = True
    mineru_enable_table: bool = True
    mineru_is_ocr: bool = True
    mineru_timeout_s: int = 180
    # structural=快（PyMuPDF）；auto=有 Token 先 MinerU Cloud 失败回退；cloud=强制 Cloud
    layout_anchor_mode: str = "structural"

    # 存储
    copilot_db_path: Path = Path("data/copilot.db")

    # 闭环控制
    max_loop_iterations: int = 3
    agent_run_timeout: int = 900

    @property
    def db_path(self) -> Path:
        p = self.copilot_db_path
        return p if p.is_absolute() else SERVICE_ROOT / p

    @property
    def agent_env_dir(self) -> Path:
        return SERVICE_ROOT / "agent-env"

    def _c2_env(self, key: str) -> str:
        env_file = self.workspace_root / "learning-buddy-c2" / ".env"
        if not env_file.is_file():
            return ""
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
        return ""

    def resolve_admin_token(self) -> str:
        """ADMIN_TOKEN 优先取显式配置，否则回读 learning-buddy-c2/.env。"""
        return self.admin_token or self._c2_env("ADMIN_TOKEN")

    def resolve_studio_token(self) -> str:
        """Studio 令牌，回退顺序与网关 studio_auth._expected_token 保持一致。"""
        return (
            self.studio_token
            or self._c2_env("STUDIO_TOKEN")
            or self.resolve_admin_token()
        )

    def studio_auth_headers(self) -> dict[str, str]:
        """网关 `/api/v1/studio/*` 要用的 Authorization 头；没令牌就空，由调用方降级。"""
        token = self.resolve_studio_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def resolve_mineru_token(self) -> str:
        return self.mineru_api_token or self._c2_env("MINERU_API_TOKEN")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
