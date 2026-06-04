from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "MCP Gateway"
    debug: bool = False

    control_plane_url: str = "http://control-plane:8001"
    redis_url: str = "redis://redis:6379/0"
    config_poll_interval: int = 30

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"

    class Config:
        env_file = ".env"


settings = Settings()
