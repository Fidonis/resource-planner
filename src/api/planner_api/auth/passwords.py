"""Password hashing (argon2id) and the password policy."""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2id with the library's current defaults

# Verified against when the user name is unknown, so the response time does not tell whether the
# account exists.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored or _DUMMY_HASH, password) and stored is not None
    except (VerificationError, InvalidHashError):
        return False


def policy_violations(password: str, *, minimum: int, username: str | None = None) -> list[str]:
    problems: list[str] = []
    if len(password) < minimum:
        problems.append(f"The password needs at least {minimum} characters.")
    if username and username.lower() in password.lower():
        problems.append("The password must not contain the user name.")
    if password.isdigit():
        problems.append("The password must not consist of digits only.")
    return problems


def new_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Session and API tokens are random, so a plain SHA-256 is enough to store them."""
    return hashlib.sha256(token.encode()).hexdigest()
