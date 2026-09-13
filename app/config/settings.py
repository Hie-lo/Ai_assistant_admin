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

    # --- Auth / interfaces ---
    session_cookie_name: str = "ai_session"
    # Shared token for the internal bot-verify contract (Phase 9). Must be
    # set to a long random value in real environments.
    internal_api_token: str = Field(default="", min_length=0)

    # --- Subscription / billing ---
    # Owner-approved (2026-09-13): after the billing period ends the
    # subscription stays usable in GRACE this many days before EXPIRED.
    subscription_grace_days: int = Field(default=7, ge=0, le=90)

    # --- AI provider (Phase 4) ---
    # "template" (default, offline/deterministic) or "openai_compatible".
    # Real credentials are configured operationally via environment; the
    # key is never logged (AI spec + developer directive rule 14).
    ai_provider: str = "template"
    ai_openai_base_url: str = ""
    ai_openai_api_key: str = ""
    ai_openai_model: str = "gpt-4o-mini"
    ai_request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    # --- Telegram (Phase 5) ---
    # Shared organization bot (owner decision 2026-09-15): ONE platform-level
    # bot managed by the operator; each business adds it to its own
    # channel/group as admin and connects that target. The token is an
    # operational secret (environment), never stored per business, never
    # logged (developer directive rule 14).
    telegram_bot_token: str = ""
    telegram_api_base_url: str = "https://api.telegram.org"
    telegram_request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor (settings are immutable at runtime)."""
    return Settings()
