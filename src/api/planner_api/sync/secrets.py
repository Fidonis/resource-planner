"""Secrets of an integration at rest (FR-INT-07).

The fields a provider marks ``writeOnly`` in its schema are stored encrypted with a key derived
from ``APP_ENCRYPTION_KEY`` and are never returned by the API.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel

from planner_api.errors import validation

PREFIX = "enc:v1:"


class SecretError(Exception):
    """A stored secret cannot be read: the key changed or the value was damaged."""


class SecretBox:
    def __init__(self, key: str) -> None:
        self._fernet: Fernet | None = None
        if key:
            try:
                self._fernet = Fernet(key.encode())  # a generated Fernet key is used as it is
            except ValueError:
                digest = hashlib.sha256(key.encode()).digest()
                self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plain: str) -> str:
        if self._fernet is None:
            raise validation(
                "Set APP_ENCRYPTION_KEY before secrets can be stored.",
                field="config",
                code="encryption_key_missing",
            )
        return PREFIX + self._fernet.encrypt(plain.encode()).decode()

    def decrypt(self, stored: str) -> str:
        if not stored.startswith(PREFIX):
            raise SecretError("The stored secret is not encrypted.")
        if self._fernet is None:
            raise SecretError("APP_ENCRYPTION_KEY is not set, so the stored secret cannot be read.")
        try:
            return self._fernet.decrypt(stored[len(PREFIX) :].encode()).decode()
        except InvalidToken as exc:
            raise SecretError(
                "The stored secret cannot be read: APP_ENCRYPTION_KEY has changed."
            ) from exc


def secret_fields(model: type[BaseModel]) -> set[str]:
    properties = model.model_json_schema().get("properties", {})
    return {name for name, spec in properties.items() if spec.get("writeOnly")}


def seal(
    model: type[BaseModel],
    given: dict[str, Any],
    previous: dict[str, Any] | None,
    box: SecretBox,
) -> dict[str, Any]:
    """The configuration as stored: secrets encrypted, an omitted secret kept as it was."""
    stored = dict(given)
    for name in secret_fields(model):
        value = given.get(name)
        if isinstance(value, str) and value:
            stored[name] = box.encrypt(value)
        elif previous and previous.get(name):
            stored[name] = previous[name]
        else:
            stored.pop(name, None)
    return stored


def reveal(model: type[BaseModel], stored: dict[str, Any], box: SecretBox) -> dict[str, Any]:
    plain = dict(stored)
    for name in secret_fields(model):
        if isinstance(plain.get(name), str):
            plain[name] = box.decrypt(plain[name])
    return plain


def public(model: type[BaseModel], stored: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """What the API returns: no secret, and the names of those that are set."""
    secrets = secret_fields(model)
    shown = {k: v for k, v in stored.items() if k not in secrets}
    return shown, sorted(n for n in secrets if stored.get(n))
