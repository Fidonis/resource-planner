"""The day the planner treats as today. Tests and the demo data can pin it."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

_pinned: date | None = None


def today() -> date:
    return _pinned if _pinned is not None else date.today()


@contextmanager
def pinned(day: date) -> Iterator[None]:
    """Make :func:`today` return ``day`` inside the block."""
    global _pinned
    previous, _pinned = _pinned, day
    try:
        yield
    finally:
        _pinned = previous
