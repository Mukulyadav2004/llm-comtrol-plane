from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Agent Gateway"
    debug: bool = False

    llm_gateway_url: str = "http://gateway:8002"
    mcp_gateway_url: str = "http://mcp-gateway:8005"
    control_plane_url: str = "http://control-plane:8001"
    redis_url: str = "redis://redis:6379/0"

    config_poll_interval: int = 30

    # ReAct loop limits — governance controls
    max_agent_steps: int = 10
    max_session_tokens: int = 16000
    session_ttl_seconds: int = 3600

    class Config:
        env_file = ".env"


settings = Settings()
