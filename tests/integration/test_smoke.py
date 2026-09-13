"""Smoke integration: real PostgreSQL + Redis connectivity.

Runs only in the CI integration job (service containers provide
``DATABASE_URL`` and ``REDIS_URL``). Proves the locked dependency set works
against real infrastructure and that the engine/session/redis wiring is sound.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


def test_postgresql_select_one() -> None:
    from app.infrastructure.db import create_engine_from_url

    url = os.environ["DATABASE_URL"]
    engine = create_engine_from_url(url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        engine.dispose()


def test_redis_ping() -> None:
    import redis

    client = redis.Redis.from_url(os.environ["REDIS_URL"])
    try:
        assert client.ping() is True
    finally:
        client.close()
