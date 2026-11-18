from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    api_auth_token: str = "incubrix-demo-token"
    rate_limit_per_minute: int = 60

    llm_provider: str = "mock"
    footage_provider: str = "mock"
    tts_provider: str = "mock"
    asr_provider: str = "mock"

    gemini_api_key: str = ""
    pixabay_api_key: str = ""

    gemini_model: str = "gemini-2.5-flash"
    whisper_model_size: str = "base"
    max_audio_duration_sec: int = 600
    min_scene_duration_sec: float = 2.0

    storage_backend: str = "local"
    local_storage_dir: str = "./outputs"
    s3_endpoint_url: str = "http://minio:9000"
    s3_public_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "incubrix-broll"


settings = Settings()