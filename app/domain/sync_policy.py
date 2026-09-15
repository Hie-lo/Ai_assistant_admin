"""Configuration-driven policies for Phase 8 sync orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class SyncPolicy:
    """Safe defaults; deployments can construct a different policy.

    The policy is deliberately independent from Celery and SQLAlchemy so
    decisions remain unit-testable and can later be managed per Source.
    """

    max_attempts: int = 3
    backoff_base_seconds: int = 60
    backoff_max_seconds: int = 3600
    stale_after_seconds: int = 900

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.backoff_base_seconds < 0 or self.backoff_max_seconds < 0:
            raise ValueError("backoff values cannot be negative")
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ValueError("backoff_max_seconds cannot be less than base")
        if self.stale_after_seconds < 1:
            raise ValueError("stale_after_seconds must be positive")

    def backoff(self, attempt: int) -> timedelta:
        if attempt < 1:
            raise ValueError("attempt must be positive")
        seconds = min(
            self.backoff_max_seconds,
            self.backoff_base_seconds * (2 ** (attempt - 1)),
        )
        return timedelta(seconds=seconds)

    def exhausted(self, attempt: int) -> bool:
        return attempt >= self.max_attempts

    def stale_delta(self) -> timedelta:
        return timedelta(seconds=self.stale_after_seconds)
