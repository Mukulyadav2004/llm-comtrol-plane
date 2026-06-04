from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "MCP Tool Registry"
    control_plane_url: str = "http://control-plane:8001"

    class Config:
        env_file = ".env"


settings = Settings()
