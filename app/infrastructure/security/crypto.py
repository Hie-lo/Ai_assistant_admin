"""Cryptographic primitives for auth, sessions, and one-time codes.

Uses the OS CSPRNG only. Passwords use Argon2id (memory-hard, OWASP
recommended). Session tokens are 256-bit random strings; only their SHA-256
digest is ever persisted, so a database leak does not yield usable sessions.
One-time/link codes are 6 digits drawn from an unbiased CSPRNG.
"""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

# Tuned for server-side verification with a strong security margin while
# keeping interactive login latency acceptable on a weak VPS.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except Argon2Error:
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the hash should be re-derived with current parameters."""
    return _hasher.check_needs_rehash(password_hash)


def generate_session_token() -> str:
    """URL-safe 256-bit session token (raw value kept only client-side)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex digest used for at-rest token storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_one_time_code() -> str:
    """6-digit numeric code, uniform, without leading-zero bias."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def generate_invite_code() -> str:
    """Human-friendly invite code: 8 uppercase alphanumeric chars.

    Ambiguous characters (0/O, 1/I/L) are excluded for verbal sharing.
    """
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def hash_invite_code(code: str) -> str:
    return hashlib.sha256(code.upper().encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
