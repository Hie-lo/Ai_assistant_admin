"""Cryptography primitives (no DB)."""

from __future__ import annotations

import re

from app.infrastructure.security.crypto import (
    constant_time_equals,
    generate_invite_code,
    generate_one_time_code,
    generate_session_token,
    hash_invite_code,
    hash_password,
    hash_token,
    verify_password,
)


def test_password_roundtrip() -> None:
    h = hash_password("s3cure-passphrase!")
    assert verify_password("s3cure-passphrase!", h) is True
    assert verify_password("wrong-password", h) is False
    # Hashes are salted: two hashes of the same password differ.
    assert hash_password("s3cure-passphrase!") != h


def test_session_token_shape_and_uniqueness() -> None:
    a = generate_session_token()
    b = generate_session_token()
    assert a != b
    assert len(a) >= 43  # 256 bits, url-safe


def test_token_hash_is_sha256_hex() -> None:
    d = hash_token("some-token")
    assert re.fullmatch(r"[0-9a-f]{64}", d)
    assert hash_token("some-token") == d
    assert hash_token("other") != d


def test_one_time_code_is_six_digits() -> None:
    for _ in range(50):
        assert re.fullmatch(r"\d{6}", generate_one_time_code())


def test_invite_code_alphabet_and_hash() -> None:
    code = generate_invite_code()
    assert len(code) == 8
    assert re.fullmatch(r"[A-HJ-KM-NP-Z2-9]{8}", code)
    assert hash_invite_code(code) == hash_invite_code(code.upper())


def test_constant_time_equals() -> None:
    assert constant_time_equals("abc", "abc") is True
    assert constant_time_equals("abc", "abd") is False
