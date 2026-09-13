"""CI service pre-flight: verify PostgreSQL + Redis reachability with retries.

Service containers can take a few seconds to become fully ready after the
runner reports them as initialized. Rather than failing the build on a
transient race (and leaving no diagnostic), each check retries for up to
60 seconds and, if it still fails, prints the underlying exception so the
cause is visible in the step output.
"""

from __future__ import annotations

import time

PG_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/app"
REDIS_URL = "redis://localhost:6379/0"
TIMEOUT_S = 60.0


def check_postgresql() -> None:
    deadline = time.monotonic() + TIMEOUT_S
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            from sqlalchemy import create_engine, text

            engine = create_engine(PG_URL)
            with engine.connect() as conn:
                one = conn.execute(text("SELECT 1")).scalar()
            engine.dispose()
            print(f"PostgreSQL OK (SELECT 1 -> {one})")
            return
        except Exception as exc:  # noqa: BLE001 - surface the exact cause
            last_err = exc
            print(f"PostgreSQL not ready yet ({exc.__class__.__name__}); retrying...")
            time.sleep(3)
    raise SystemExit(f"PostgreSQL unreachable after {TIMEOUT_S:.0f}s: {last_err}")


def check_redis() -> None:
    deadline = time.monotonic() + TIMEOUT_S
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            import redis

            ping = redis.Redis.from_url(REDIS_URL).ping()
            print(f"Redis OK (ping -> {ping})")
            return
        except Exception as exc:  # noqa: BLE001 - surface the exact cause
            last_err = exc
            print(f"Redis not ready yet ({exc.__class__.__name__}); retrying...")
            time.sleep(3)
    raise SystemExit(f"Redis unreachable after {TIMEOUT_S:.0f}s: {last_err}")


if __name__ == "__main__":
    check_postgresql()
    check_redis()
