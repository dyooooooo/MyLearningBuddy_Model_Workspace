from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    vllm_base_url: str = "http://vllm-qwen36-35b:8088/v1"
    vllm_model: str = "qwen36-35b-nvfp4"
    vllm_max_concurrent: int = 2
    vllm_temperature: float = 0.0
    vllm_acquire_timeout_s: float = 120.0
    host: str = "0.0.0.0"
    port: int = 8000
    request_timeout_s: float = 300.0
    buddy_api_url: str = "http://buddy-api:8100"
    buddy_api_token: str = "change-me-api"
    buddy_admin_token: str = "change-me-admin"
    refine_enabled: bool = True
    redis_url: str = "redis://redis:6379/0"
    session_ttl_seconds: int = 86400
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "companion-frames"
    minio_secure: bool = False


settings = Settings()
