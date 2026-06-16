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

    # OpenAI-compatible API auth. When require_auth is False (default) any
    # api_key is accepted, so the OpenAI SDK works out of the box. Set
    # GATEWAY_API_KEYS to a comma-separated list and GATEWAY_REQUIRE_AUTH=true
    # to lock the gateway down to known keys.
    require_auth: bool = False
    api_keys: str = ""  # comma-separated list of accepted Bearer keys

    # CORS origins allowed to call the gateway from a browser. "*" by default
    # so the dashboard / playground work locally.
    cors_origins: str = "*"

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()] or ["*"]

    # Semantic routing classifier — uses a fast local Ollama model to classify intent
    classifier_base_url: str = "http://ollama:11434"
    classifier_model: str = "llama3.2:1b"

    class Config:
        env_file = ".env"


settings = Settings()
