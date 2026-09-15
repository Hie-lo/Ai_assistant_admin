"""Domain layer — pure contracts and invariants.

This package must remain framework-agnostic (no FastAPI/SQLAlchemy/Celery
imports at module import time for domain logic). Domain objects and state
machines for each bounded context will be introduced per roadmap phase,
starting with User/Business/Membership in Phase 1.
"""
