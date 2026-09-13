"""Settings load, validate, and expose expected fields."""

from __future__ import annotations

from app.config.settings import Settings


def test_settings_defaults_are_sane(settings: Settings) -> None:
    assert settings.app_name == "ai-assistant-admin"
    assert settings.environment == "test"
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.redis_url.startswith("redis://")


def test_settings_secret_key_has_minimum_length() -> None:
    import pytest

    with pytest.raises(ValueError):
        Settings(secret_key="short")


def test_is_prod_flag() -> None:
    assert Settings(environment="prod").is_prod is True
    assert Settings(environment="dev").is_prod is False
