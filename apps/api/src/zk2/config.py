"""Application settings.

Single source of truth, loaded once via `get_settings()`.
All env vars are validated via Pydantic.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py is at apps/api/src/zk2/config.py — repo root is 4 parents up
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DOTENV = _REPO_ROOT / ".env"

# Tests must not inherit the developer's .env: it carries real provider keys,
# SMTP credentials and an OTLP endpoint. Set ZK2_DISABLE_DOTENV=1 to make the
# process read environment variables only.
_DOTENV_DISABLED = os.getenv("ZK2_DISABLE_DOTENV", "").lower() in {"1", "true", "yes"}
_ENV_FILE = str(_DOTENV) if (_DOTENV.exists() and not _DOTENV_DISABLED) else None


class _Base(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


class AppSettings(_Base):
    env: Literal["dev", "test", "staging", "prod"] = Field("dev", alias="APP_ENV")
    name: str = Field("zk2-chatbot", alias="APP_NAME")
    secret_key: SecretStr = Field(..., alias="APP_SECRET_KEY")
    base_url: str = Field("http://localhost:3000", alias="APP_BASE_URL")
    api_base_url: str = Field("http://localhost:8000", alias="API_BASE_URL")
    log_level: str = Field("INFO", alias="LOG_LEVEL")


class DatabaseSettings(_Base):
    host: str = Field("localhost", alias="DB_HOST")
    port: int = Field(5432, alias="DB_PORT")
    name: str = Field("zk2", alias="DB_NAME")
    user: str = Field("zk2", alias="DB_USER")
    password: SecretStr = Field(..., alias="DB_PASSWORD")
    pool_size: int = Field(10, alias="DB_POOL_SIZE")
    max_overflow: int = Field(20, alias="DB_MAX_OVERFLOW")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dsn(self) -> PostgresDsn:
        return PostgresDsn(
            f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_dsn(self) -> str:
        """For Alembic which uses sync driver."""
        return (
            f"postgresql+psycopg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class RedisSettings(_Base):
    url: RedisDsn = Field("redis://localhost:6379/0", alias="REDIS_URL")  # type: ignore[assignment]


class AuthSettings(_Base):
    jwt_access_ttl_seconds: int = Field(900, alias="JWT_ACCESS_TTL_SECONDS")
    jwt_refresh_ttl_seconds: int = Field(2_592_000, alias="JWT_REFRESH_TTL_SECONDS")
    magic_link_ttl_seconds: int = Field(900, alias="MAGIC_LINK_TTL_SECONDS")
    invite_ttl_seconds: int = Field(604_800, alias="INVITE_TTL_SECONDS")
    allowed_email_domains: str = Field("*", alias="ALLOWED_EMAIL_DOMAINS")

    rate_limit_login_per_min: int = Field(10, alias="RATE_LIMIT_LOGIN_PER_MIN")
    rate_limit_access_request_per_hour_per_email: int = Field(
        1, alias="RATE_LIMIT_ACCESS_REQUEST_PER_HOUR_PER_EMAIL"
    )
    rate_limit_access_request_per_hour_per_ip: int = Field(
        5, alias="RATE_LIMIT_ACCESS_REQUEST_PER_HOUR_PER_IP"
    )

    @property
    def allowed_email_domains_list(self) -> list[str] | None:
        """`None` means "any domain"."""
        if self.allowed_email_domains.strip() in ("*", ""):
            return None
        return [d.strip().lower() for d in self.allowed_email_domains.split(",") if d.strip()]


class OAuthSettings(_Base):
    google_client_id: str | None = Field(None, alias="GOOGLE_OAUTH_CLIENT_ID")
    google_client_secret: SecretStr | None = Field(None, alias="GOOGLE_OAUTH_CLIENT_SECRET")
    github_client_id: str | None = Field(None, alias="GITHUB_OAUTH_CLIENT_ID")
    github_client_secret: SecretStr | None = Field(None, alias="GITHUB_OAUTH_CLIENT_SECRET")


class MailSettings(_Base):
    smtp_server: str = Field("smtp.mailgun.org", alias="SMTP_SERVER")
    smtp_port: int = Field(465, alias="SMTP_PORT")
    smtp_use_ssl: bool = Field(True, alias="SMTP_USE_SSL")
    sender: str = Field("zk2@mailgun.zeka.kiev.ua", alias="EMAIL_SENDER")
    password: SecretStr | None = Field(None, alias="EMAIL_PASSWORD")


class LLMSettings(_Base):
    openai_api_key: SecretStr | None = Field(None, alias="OPENAI_API_KEY")
    anthropic_api_key: SecretStr | None = Field(None, alias="ANTHROPIC_API_KEY")
    gemini_api_key: SecretStr | None = Field(None, alias="GEMINI_API_KEY")
    ollama_base_url: str = Field("http://localhost:11434", alias="OLLAMA_BASE_URL")


class ObservabilitySettings(_Base):
    langfuse_public_key: SecretStr | None = Field(None, alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: SecretStr | None = Field(None, alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field("http://localhost:3030", alias="LANGFUSE_HOST")
    otel_endpoint: str | None = Field(None, alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    otel_service_name: str = Field("zk2-api", alias="OTEL_SERVICE_NAME")
    sentry_dsn: str | None = Field(None, alias="SENTRY_DSN")


class IngestSettings(_Base):
    """Limits for user-supplied files and URLs."""

    max_upload_bytes: int = Field(26_214_400, alias="MAX_UPLOAD_BYTES")  # 25 MiB
    max_fetch_bytes: int = Field(10_485_760, alias="MAX_FETCH_BYTES")  # 10 MiB
    fetch_timeout_seconds: float = Field(15.0, alias="FETCH_TIMEOUT_SECONDS")
    max_redirects: int = Field(5, alias="MAX_REDIRECTS")
    sitemap_default_limit: int = Field(100, alias="SITEMAP_DEFAULT_LIMIT")
    sitemap_max_limit: int = Field(500, alias="SITEMAP_MAX_LIMIT")
    # Only for local testing against a private address. Never enable in prod.
    allow_private_networks: bool = Field(False, alias="SSRF_ALLOW_PRIVATE_NETWORKS")
    embedding_batch_size: int = Field(100, alias="EMBEDDING_BATCH_SIZE")
    # pgvector cannot build HNSW above 2000 dimensions - see ADR-0004
    embedding_dimensions: int = Field(1536, alias="EMBEDDING_DIMENSIONS", le=2000)


class ChatSettings(_Base):
    max_ws_message_bytes: int = Field(32_768, alias="MAX_WS_MESSAGE_BYTES")
    ws_idle_timeout_seconds: float = Field(900.0, alias="WS_IDLE_TIMEOUT_SECONDS")


class StorageSettings(_Base):
    kind: Literal["local", "s3"] = Field("local", alias="STORAGE_KIND")
    local_upload_dir: str = Field("./uploads", alias="LOCAL_UPLOAD_DIR")
    s3_bucket: str | None = Field(None, alias="S3_BUCKET")
    s3_region: str | None = Field(None, alias="S3_REGION")


class SuperAdminSeedSettings(_Base):
    email: str | None = Field(None, alias="SUPER_ADMIN_EMAIL")
    password: SecretStr | None = Field(None, alias="SUPER_ADMIN_PASSWORD")


class Settings:
    """Aggregated settings.

    Sub-sections instantiated lazily — env is read once per process.
    """

    def __init__(self) -> None:
        self.app = AppSettings()
        self.db = DatabaseSettings()
        self.redis = RedisSettings()
        self.auth = AuthSettings()
        self.oauth = OAuthSettings()
        self.mail = MailSettings()
        self.llm = LLMSettings()
        self.observability = ObservabilitySettings()
        self.ingest = IngestSettings()
        self.chat = ChatSettings()
        self.storage = StorageSettings()
        self.super_admin = SuperAdminSeedSettings()

    @property
    def is_dev(self) -> bool:
        return self.app.env == "dev"

    @property
    def is_test(self) -> bool:
        return self.app.env == "test"

    @property
    def is_prod(self) -> bool:
        return self.app.env == "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
