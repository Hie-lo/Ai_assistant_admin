"""Smoke integration: real PostgreSQL + Redis connectivity.

Runs only in the CI integration job (service containers provide
``DATABASE_URL`` and ``REDIS_URL``). Proves the locked dependency set works
against real infrastructure and that the engine/session/redis wiring is sound.
Skipped locally where no service environment is configured.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

requires_services = pytest.mark.skipif(
    not (os.environ.get("DATABASE_URL") and os.environ.get("REDIS_URL")),
    reason="requires CI service containers (DATABASE_URL/REDIS_URL)",
)


@requires_services
def test_postgresql_select_one() -> None:
    from app.infrastructure.db import create_engine_from_url

    url = os.environ["DATABASE_URL"]
    engine = create_engine_from_url(url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        engine.dispose()


@requires_services
def test_redis_ping() -> None:
    import redis

    client = redis.Redis.from_url(os.environ["REDIS_URL"])
    try:
        assert client.ping() is True
    finally:
        client.close()
