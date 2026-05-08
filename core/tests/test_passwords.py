"""Tests for the argon2-cffi-backed password helpers."""

from __future__ import annotations

from curfew.passwords import hash_password, verify_password


def test_hash_then_verify_roundtrip() -> None:
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True


def test_verify_rejects_wrong_password() -> None:
    h = hash_password("correct horse battery staple")
    assert verify_password("wrong horse", h) is False


def test_hash_includes_argon2id_prefix() -> None:
    """Hash format starts with $argon2id$ — sanity check on algorithm choice."""
    h = hash_password("anything")
    assert h.startswith("$argon2id$")


def test_two_hashes_of_same_password_differ() -> None:
    """Per-hash random salt means identical inputs produce different hashes."""
    a = hash_password("same-input")
    b = hash_password("same-input")
    assert a != b
    # ...but both verify.
    assert verify_password("same-input", a)
    assert verify_password("same-input", b)
