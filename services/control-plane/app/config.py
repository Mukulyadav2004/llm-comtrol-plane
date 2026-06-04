from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "LLM Control Plane"
    debug: bool = False

    broker_url: str = "http://broker:8000"
    redis_url: str = "redis://redis:6379/0"

    # S3 for supplemental context (static configs, allowlists, etc.)
    s3_bucket: str = ""
    aws_region: str = "us-east-1"

    # How often (seconds) to pull fresh context from broker
    context_refresh_interval: int = 30

    class Config:
        env_file = ".env"


settings = Settings()
