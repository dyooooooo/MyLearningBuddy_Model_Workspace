from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    vllm_base_url: str = "http://vllm-qwen36-35b:8088/v1"
    vllm_model: str = "qwen36-35b-nvfp4"
    host: str = "0.0.0.0"
    port: int = 8000
    request_timeout_s: float = 300.0
    companion_data_dir: str = "/data/companion"


settings = Settings()
