"""Shared pytest fixtures.

- ``db_engine`` / ``db_session`` / ``db_client``: DB-backed tests. Locally
  they run on an in-memory SQLite engine (patched into the process-wide
  session machinery); in CI they can target PostgreSQL by setting
  ``TEST_BACKEND=postgres`` + ``DATABASE_URL``. Service-level invariants are
  asserted on both; Postgres-specific DB constraints are an added backstop.
- ``client``: HTTP client without a database (health endpoint contract).
"""

from __future__ import annotations

import os
import uuid

import app.infrastructure.db.session as db_session_mod
import pytest
from app.config.settings import Settings, get_settings
from app.infrastructure.db.base import Base, register_models
from app.infrastructure.db.session import create_session_factory, reset_engine
from app.interfaces.http import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test a deterministic internal token + fresh settings cache."""
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-internal-token")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def settings() -> Settings:
    """A deterministic in-memory settings instance for unit tests."""
    return Settings(
        environment="test",
        debug=False,
        secret_key="test-secret-key-0123456789",
        database_url="sqlite://",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/1",
        celery_result_backend="redis://localhost:6379/2",
        log_level="INFO",
        internal_api_token="test-internal-token",
    )


def _build_engine() -> Engine:
    register_models()
    if os.environ.get("TEST_BACKEND") == "postgres" and os.environ.get("DATABASE_URL"):
        engine = create_engine(os.environ["DATABASE_URL"])
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    else:
        engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
    _seed_business_types(engine)
    return engine


def _seed_business_types(engine: Engine) -> None:
    """Mirror the migration seed (create_all does not run migrations)."""
    from app.infrastructure.db.models import BusinessType
    from sqlalchemy import select

    with engine.connect() as conn:
        if conn.execute(select(BusinessType.key)).first() is None:
            conn.execute(
                BusinessType.__table__.insert().values(
                    [
                        {
                            "key": "general",
                            "name": "General business",
                            "description": "Default business type.",
                            "is_active": True,
                        }
                    ]
                )
            )
            conn.commit()


@pytest.fixture()
def db_engine() -> Engine:
    """A clean database engine with all tables created (and patched in)."""
    engine = _build_engine()
    db_session_mod._engine = engine
    db_session_mod._session_factory = create_session_factory(engine)
    yield engine
    reset_engine()
    engine.dispose()


@pytest.fixture()
def db_session(db_engine: Engine) -> Session:
    """A session bound to the test database for direct use-case tests."""
    factory = db_session_mod.get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def client() -> TestClient:
    """HTTP client WITHOUT a database (for health-endpoint contract tests)."""
    reset_engine()
    app = create_app()
    with TestClient(app) as c:
        yield c
    reset_engine()


@pytest.fixture()
def db_client(db_engine: Engine) -> TestClient:
    """HTTP client wired to the test database (full-stack Phase 1 tests)."""
    app = create_app()
    with TestClient(app) as c:
        yield c


# --- Small helpers shared by test modules ---


def make_user(db: Session, email: str, password: str = "correct-horse-battery-1") -> uuid.UUID:
    from app.application import auth

    user = auth.register_user(db, email=email, password=password, display_name=email.split("@")[0])
    db.commit()
    return user.user_id


def login(client: TestClient, email: str, password: str = "correct-horse-battery-1") -> TestClient:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return client
