"""Application settings.

All configuration is loaded from environment variables (optionally via a
`.env` file) and validated with Pydantic. No secrets are hard-coded.
Critical state is never derived from cache-only sources; settings describe
connections, not business state.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "test", "staging", "prod"]


class Settings(BaseSettings):
    """Validated application settings.

    Field names map to upper-case environment variables, e.g.
    ``DATABASE_URL`` -> ``database_url``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Core ---
    app_name: str = "ai-assistant-admin"
    environment: Environment = "dev"
    debug: bool = False
    # Secret material must come from the environment / secret store.
    secret_key: str = Field(default="change-me-in-real-environments", min_length=8)

    # --- Database ---
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ai_assistant"
    database_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- Redis / queue ---
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # --- Observability ---
    log_level: str = "INFO"

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor (settings are immutable at runtime)."""
    return Settings()
