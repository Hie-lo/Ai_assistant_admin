"""Engine and session factory.

The database is the source of truth for durable business state. Engines and
session factories are created lazily from settings so that importing the
package never requires a live database (important for tests and workers).
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from app.config.settings import Settings, get_settings


def create_engine_from_url(url: str, *, echo: bool = False) -> Engine:
    """Create a SQLAlchemy engine for the given URL.

    ``pool_pre_ping`` guards against stale connections after server/DB
    restarts (failure-first: reconnect rather than assume liveness).
    A bounded ``QueuePool`` is used explicitly for server databases; SQLite
    keeps its default pool selection (in-memory SQLite requires the
    in-memory pool). NOTE: ``sqlalchemy.pool.Pool`` itself is an abstract
    base class and must never be passed as ``poolclass`` (connections raise
    ``NotImplementedError``).
    """
    from app.config.settings import get_settings

    settings = get_settings()
    kwargs: dict[str, object] = {"echo": echo, "pool_pre_ping": True}
    if not url.startswith("sqlite"):
        kwargs["poolclass"] = QueuePool
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
        kwargs["pool_recycle"] = 300
        kwargs["pool_timeout"] = 30
    return create_engine(url, **kwargs)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to ``engine``.

    ``expire_on_commit=False`` keeps objects usable after commit, which the
    publication/sync use cases rely on to read remote identifiers immediately
    after persisting attempts.
    """
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine(settings: Settings | None = None) -> Engine:
    """Return the process-wide engine, creating it on first use."""
    global _engine, _session_factory
    if _engine is None:
        settings = settings or get_settings()
        _engine = create_engine_from_url(settings.database_url, echo=settings.database_echo)
        _session_factory = create_session_factory(_engine)
    return _engine


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    """Return the process-wide session factory (creates the engine if needed)."""
    get_engine(settings)
    assert _session_factory is not None
    return _session_factory


def get_db() -> Generator[Session]:
    """FastAPI dependency yielding a scoped session.

    Each request gets its own session; it is always closed, even on error.
    """
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


def reset_engine() -> None:
    """Test hook: drop the cached engine/session factory."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None
