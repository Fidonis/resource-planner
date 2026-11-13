"""Helpers the resource services share."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from planner_api.errors import ErrorCode, ProblemError


def jsonable(value: Any) -> Any:
    """Make a value storable in the audit log."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, set | frozenset):
        return sorted(jsonable(v) for v in value)
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    return value


def snapshot(entity: object, fields: Iterable[str]) -> dict[str, Any]:
    return {name: jsonable(getattr(entity, name)) for name in fields}


def reject_locked(
    external_source: str | None,
    managed: Iterable[str],
    changes: Mapping[str, tuple[Any, Any]],
) -> None:
    """Refuse changes to fields that the source system of an imported record owns.

    ``changes`` maps an API field name to ``(current, requested)``; an unchanged value is fine,
    so a client may send back the whole record.
    """
    if external_source is None:
        return
    locked = [name for name in managed if name in changes and changes[name][0] != changes[name][1]]
    if locked:
        raise ProblemError(
            409,
            ErrorCode.FIELD_LOCKED,
            f"{', '.join(locked)} is managed by '{external_source}' and cannot be changed here.",
            errors=[
                {"field": name, "message": f"Managed by {external_source}.", "code": "locked"}
                for name in locked
            ],
        )
