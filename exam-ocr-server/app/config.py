from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    vllm_base_url: str = "http://vllm-qwen36-35b:8088/v1"
    vllm_model: str = "qwen38-27b-nvfp4"
    vllm_max_concurrent: int = 2
    vllm_temperature: float = 0.0
    vllm_acquire_timeout_s: float = 120.0
    # LAN OpenAI proxy (/v1 → vLLM) for WSL WTR; token required when enabled
    vllm_lan_proxy_enabled: bool = True
    vllm_lan_token: str = ""
    vllm_lan_timeout_s: float = 600.0
    host: str = "0.0.0.0"
    port: int = 8000
    request_timeout_s: float = 300.0
    buddy_api_url: str = "http://buddy-api:8100"
    buddy_api_token: str = "change-me-api"
    buddy_admin_token: str = "change-me-admin"
    # Browser → gateway Studio API. Empty = fall back to buddy_admin_token.
    studio_token: str = ""
    # Buddy Copilot 宿主机编排服务（Cursor SDK），经 extra_hosts 访问宿主机
    copilot_base_url: str = "http://host.docker.internal:8200"
    textbook_agent_url: str = "http://textbook-agent:8200"
    textbook_agent_token: str = ""
    refine_enabled: bool = True
    redis_url: str = "redis://redis:6379/0"
    session_ttl_seconds: int = 86400
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "companion-frames"
    minio_secure: bool = False


settings = Settings()
