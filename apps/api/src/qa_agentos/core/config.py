from typing import FrozenSet, Optional

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    PROJECT_NAME: str = "QA AgentOS"
    API_V1_STR: str = "/v1"

    # Security & Auth
    SECRET_KEY: str = "dev-secret-key-change-this-in-production-min-32-chars-long"
    JWT_ALGORITHM: str = "RS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

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
    LITELLM_MASTER_KEY: SecretStr = SecretStr("sk-litellm-dev-master-key")
    LITELLM_TIMEOUT_SECONDS: float = 60.0

    # Model provider allowlist (comma-separated provider identifiers from models.yaml).
    # Secure default: real providers only. The "mock" provider is additionally
    # restricted to ENVIRONMENT == "test" by the model resolver, even if listed here.
    MODEL_PROVIDER_ALLOWLIST: str = "nvidia_nim,gemini"

    # Dispatcher Configuration
    RUN_DISPATCHER: str = "inline"  # "inline" or "hatchet"

    # Hatchet Durable Execution Configuration
    HATCHET_CLIENT_TOKEN: Optional[SecretStr] = None
    HATCHET_CLIENT_HOST_PORT: Optional[str] = "localhost:7077"
    HATCHET_CLIENT_SERVER_URL: Optional[str] = "http://localhost:8080"
    HATCHET_CLIENT_TENANT_ID: Optional[str] = None
    HATCHET_CLIENT_TLS_STRATEGY: str = "none"  # "none", "tls", or "mtls"
    HATCHET_WORKER_CONCURRENCY: int = 10
    HATCHET_TASK_TIMEOUT_MINUTES: int = 15
    HATCHET_TASK_RETRIES: int = 2
    HATCHET_SWEEPER_INTERVAL_SECONDS: int = 60
    HATCHET_SWEEPER_THRESHOLD_SECONDS: int = 30
    # A preparing/running run whose heartbeat is older than this is considered abandoned.
    # Unset = one task timeout plus a 5-minute grace period; a healthy attempt refreshes its
    # heartbeat after every graph superstep and can never outlive the task timeout.
    RUN_STALE_AFTER_SECONDS: Optional[int] = None
    # Durable LangGraph checkpoints for the Hatchet worker (PostgreSQL, schema "langgraph").
    WORKER_CHECKPOINTER_ENABLED: bool = True

    @property
    def run_stale_after_seconds(self) -> int:
        if self.RUN_STALE_AFTER_SECONDS is not None:
            return max(60, self.RUN_STALE_AFTER_SECONDS)
        return self.HATCHET_TASK_TIMEOUT_MINUTES * 60 + 300

    @property
    def hatchet_max_attempts(self) -> int:
        """Total Hatchet attempts per task run (initial attempt + retries)."""
        return self.HATCHET_TASK_RETRIES + 1

    # Langfuse Observability
    LANGFUSE_PUBLIC_KEY: Optional[str] = None
    LANGFUSE_SECRET_KEY: Optional[SecretStr] = None
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    @property
    def langfuse_enabled(self) -> bool:
        return bool(
            self.LANGFUSE_PUBLIC_KEY
            and self.LANGFUSE_PUBLIC_KEY.strip()
            and self.LANGFUSE_SECRET_KEY
            and self.LANGFUSE_SECRET_KEY.get_secret_value().strip()
        )

    @property
    def model_provider_allowlist(self) -> FrozenSet[str]:
        return frozenset(p.strip() for p in self.MODEL_PROVIDER_ALLOWLIST.split(",") if p.strip())

    @model_validator(mode="after")
    def validate_dispatcher_configuration(self) -> "Settings":
        if self.RUN_DISPATCHER.lower() == "hatchet":
            if not self.HATCHET_CLIENT_TOKEN or not self.HATCHET_CLIENT_TOKEN.get_secret_value().strip():
                raise ValueError(
                    "RUN_DISPATCHER is configured as 'hatchet', but HATCHET_CLIENT_TOKEN is not set or empty. "
                    "A valid Hatchet client token is required for durable task execution."
                )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
