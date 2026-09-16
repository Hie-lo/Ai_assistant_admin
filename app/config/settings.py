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
    # "template" (default, offline/deterministic), "openai_compatible", or
    # "openrouter". Real credentials are configured operationally via
    # environment; keys are never logged (AI spec + directive rule 14).
    ai_provider: str = "template"
    ai_openai_base_url: str = ""
    ai_openai_api_key: str = ""
    ai_openai_model: str = "gpt-4o-mini"
    ai_request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    # --- OpenRouter (owner choice 2026-09-15) ---
    # OpenAI-compatible gateway (AI_PROVIDER=openrouter). Model slugs use
    # OpenRouter's vendor/model naming, e.g. "openai/gpt-4o-mini" or
    # "anthropic/claude-3.5-sonnet". The key (sk-or-...) is an operational
    # secret: environment only, never stored, never logged.
    ai_openrouter_base_url: str = "https://openrouter.ai/api/v1"
    ai_openrouter_api_key: str = ""
    ai_openrouter_model: str = "openai/gpt-4o-mini"

    # --- Telegram (Phase 5) ---
    # Shared organization bot (owner decision 2026-09-15): ONE platform-level
    # bot managed by the operator; each business adds it to its own
    # channel/group as admin and connects that target. The token is an
    # operational secret (environment), never stored per business, never
    # logged (developer directive rule 14).
    telegram_bot_token: str = ""
    telegram_api_base_url: str = "https://api.telegram.org"
    telegram_request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    telegram_webhook_secret: str = Field(
        default="", description="Secret token for Telegram webhook verification"
    )
    # Shared organization BALE bot (same model: platform-level, env-only).
    bale_bot_token: str = ""
    bale_api_base_url: str = "https://tapi.bale.ai"
    bale_request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    bale_webhook_secret: str = Field(
        default="", description="Secret token for Bale webhook verification"
    )

    # --- Backup off-site (Phase 10) ---
    backup_s3_bucket: str = ""
    backup_s3_prefix: str = "backups/"
    backup_s3_region: str = "us-east-1"

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor (settings are immutable at runtime)."""
    return Settings()
