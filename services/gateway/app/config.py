from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "LLM Gateway"
    debug: bool = False

    control_plane_url: str = "http://control-plane:8001"
    redis_url: str = "redis://redis:6379/0"

    # How often (seconds) to poll control plane for config updates
    config_poll_interval: int = 30

    otlp_endpoint: str = "http://otel-collector:4317"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"

    # Semantic routing classifier — uses a fast local Ollama model to classify intent
    classifier_base_url: str = "http://ollama:11434"
    classifier_model: str = "llama3.2:1b"

    class Config:
        env_file = ".env"


settings = Settings()
