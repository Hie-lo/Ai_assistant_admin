"""Shared pytest fixtures for the baseline.

Kept dependency-light: unit tests must run without a live database/queue.
Integration tests (tests/integration) rely on the service containers wired
in CI and use ``DATABASE_URL``/``REDIS_URL`` from the environment.
"""

from __future__ import annotations

import pytest
from app.config.settings import Settings, get_settings
from app.infrastructure.db import reset_engine
from app.interfaces.http import create_app
from fastapi.testclient import TestClient


@pytest.fixture()
def settings() -> Settings:
    """A deterministic in-memory settings instance for unit tests."""
    return Settings(
        environment="test",
        debug=False,
        secret_key="test-secret-key-0123456789",
        database_url="postgresql+psycopg://postgres:postgres@localhost:5432/ai_assistant_test",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/1",
        celery_result_backend="redis://localhost:6379/2",
        log_level="INFO",
    )


@pytest.fixture(autouse=True)
def _isolate_engine():
    """Ensure no engine leaks between tests."""
    reset_engine()
    yield
    reset_engine()


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    """FastAPI test client for the baseline app (no live DB required)."""
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as c:
        yield c
