"""Database infrastructure: base, engine, and session factory wiring."""

from __future__ import annotations

from app.infrastructure.db import Base, create_engine_from_url, create_session_factory
from app.infrastructure.db.base import register_models
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine


def test_base_metadata_is_available() -> None:
    assert isinstance(Base.metadata, MetaData)


def test_register_models_is_idempotent() -> None:
    # No models yet in the baseline; calling repeatedly must be safe.
    register_models()
    register_models()


def test_engine_and_session_factory_from_url() -> None:
    engine: Engine = create_engine_from_url("sqlite://")
    factory = create_session_factory(engine)
    session = factory()
    try:
        # A trivial transaction proves the session is usable.
        session.rollback()
    finally:
        session.close()
        engine.dispose()
