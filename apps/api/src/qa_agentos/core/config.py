from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    PROJECT_NAME: str = "QA AgentOS"
    API_V1_STR: str = "/v1"

    # Security & Auth
    SECRET_KEY: str = "dev-secret-key-change-this-in-production-min-32-chars-long"
    JWT_ALGORITHM: str = "RS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 # 24 hours
    
    RSA_PRIVATE_KEY_PEM: Optional[str] = None
    RSA_PUBLIC_KEY_PEM: Optional[str] = None

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/qa_agentos"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # MinIO
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ROOT_USER: str = "minioadmin"
    MINIO_ROOT_PASSWORD: str = "minioadmin"
    MINIO_BUCKET: str = "qa-agentos-artifacts"
    MINIO_SECURE: bool = False

    # LiteLLM Gateway
    LITELLM_ENDPOINT: str = "http://localhost:4000"
    LITELLM_MASTER_KEY: str = "sk-litellm-dev-master-key"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
