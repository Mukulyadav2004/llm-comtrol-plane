from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "AI Gateway Broker"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://broker:broker@postgres:5432/broker_db"
    sync_database_url: str = "postgresql://broker:broker@postgres:5432/broker_db"

    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    control_plane_url: str = "http://control-plane:8001"
    gateway_url: str = "http://gateway:8002"

    # S3 for template context (optional)
    s3_bucket: str = ""
    aws_region: str = "us-east-1"

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    otlp_endpoint: str = "http://otel-collector:4317"

    class Config:
        env_file = ".env"


settings = Settings()
