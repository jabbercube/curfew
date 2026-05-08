"""Password hashing helpers backed by argon2-cffi.

Argon2id is the modern default for password storage (memory-hard, side-channel
resistant). ``argon2-cffi`` ships sensible defaults; we use ``PasswordHasher()``
without overriding parameters because the library bumps them as recommended
practice evolves and the homelab scale doesn't demand custom tuning.

``verify_password`` returns a bool rather than raising on mismatch — callers
care about the answer, not the exception type. The argon2 library raises on
mismatch by design (so timing differs minimally between paths); we catch and
return False so the route layer can answer 401 cleanly.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    """Return an Argon2 hash of ``plain``. Hash includes algorithm + params."""
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if ``plain`` matches ``hashed``; False otherwise."""
    try:
        _hasher.verify(hashed, plain)
    except VerifyMismatchError:
        return False
    return True
