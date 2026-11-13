"""Query helpers shared by the repositories."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Row, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import QueryableAttribute
from sqlalchemy.sql.elements import ColumnElement

MAX_PAGE_SIZE = 200


def like(text: str) -> str:
    """A LIKE pattern that matches ``text`` anywhere, with the wildcards of ``text`` escaped."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def page_rows(
    session: AsyncSession,
    stmt: Select[*tuple[Any, ...]],
    *,
    page: int,
    page_size: int,
    sort: str | None,
    sorts: Mapping[str, ColumnElement[Any] | QueryableAttribute[Any]],
    default_sort: str,
) -> tuple[Sequence[Row[*tuple[Any, ...]]], int]:
    """Like :func:`page_of` for a statement that selects several columns or entities."""
    total = (
        await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ).scalar_one()
    keys = [part.strip() for part in (sort or default_sort).split(",") if part.strip()]
    ordered = stmt
    for key in keys:
        column = sorts.get(key.lstrip("-"))
        if column is not None:
            ordered = ordered.order_by(column.desc() if key.startswith("-") else column.asc())
    size = max(1, min(page_size, MAX_PAGE_SIZE))
    ordered = ordered.limit(size).offset((max(1, page) - 1) * size)
    return (await session.execute(ordered)).all(), total


async def page_of[T](
    session: AsyncSession,
    stmt: Select[T],
    *,
    page: int,
    page_size: int,
    sort: str | None,
    sorts: Mapping[str, ColumnElement[Any] | QueryableAttribute[Any]],
    default_sort: str,
) -> tuple[Sequence[T], int]:
    """Run ``stmt`` for one page. ``sort`` holds keys of ``sorts``; ``-`` makes one descending."""
    total = (
        await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ).scalar_one()
    keys = [part.strip() for part in (sort or default_sort).split(",") if part.strip()]
    ordered = stmt
    for key in keys:
        descending = key.startswith("-")
        column = sorts.get(key.lstrip("-"))
        if column is None:
            continue
        ordered = ordered.order_by(column.desc() if descending else column.asc())
    size = max(1, min(page_size, MAX_PAGE_SIZE))
    ordered = ordered.limit(size).offset((max(1, page) - 1) * size)
    rows = (await session.scalars(ordered)).all()
    return rows, total
