"""Small parsers shared by the resource descriptions."""

from __future__ import annotations

from datetime import date
from uuid import UUID


def uuid_or_none(value: str | None) -> UUID | None:
    try:
        return UUID(value) if value else None
    except ValueError:
        return None


def flag(value: str | None) -> bool | None:
    return {"yes": True, "no": False}.get(value or "")


def date_or_none(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def id_or_none(value: str | None) -> str | None:
    found = uuid_or_none(value)
    return str(found) if found else None


def days(text: str) -> float | None:
    """Person-days typed with a decimal point or comma; ``None`` when it is not a number."""
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def plain(value: float) -> str:
    """A figure without trailing zeros: ``2.0`` becomes ``2``."""
    return f"{value:g}"
