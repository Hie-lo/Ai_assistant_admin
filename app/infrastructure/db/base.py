"""Declarative base for all ORM models.

Every model in the project subclasses ``Base``. Alembic autogenerate and
explicit migrations both rely on ``Base.metadata``.

Models are intentionally absent in this Phase 0 baseline; they are added
phase by phase (first: User/Business/Membership in Phase 1).
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Common base class for all declarative models."""


def register_models() -> None:
    """Import model modules so their tables register on ``Base.metadata``.

    Called by Alembic's env and by the application at startup. As models are
    added, import them here (or via an explicit registry) so autogenerate and
    migrations see the full metadata.
    """

    # Phase 1 will register:
    # from app.infrastructure.db import models  # noqa: F401
