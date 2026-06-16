from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "MCP Gateway"
    debug: bool = False

    control_plane_url: str = "http://control-plane:8001"
    redis_url: str = "redis://redis:6379/0"
    config_poll_interval: int = 30

    # Used by intelligent routing / argument extraction. The MCP Gateway calls the
    # LLM Gateway (not Ollama directly) so routing inherits its policy + tracing.
    llm_gateway_url: str = "http://gateway:8002"
    routing_route: str = "auto"          # which LLM Gateway route to use for tool selection
    routing_llm_enabled: bool = True     # if False, routing is purely lexical (no LLM call)

    # Lexical score (0..1) above which the fast-path router skips the LLM entirely.
    routing_keyword_threshold: float = 0.5

    default_tool_rpm: int = 120

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"

    class Config:
        env_file = ".env"


settings = Settings()
