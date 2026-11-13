"""Declarative base and column mixins shared by all tables."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, MetaData, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class VersionedMixin:
    """Optimistic locking. SQLAlchemy adds ``WHERE version = :loaded`` to every UPDATE and
    DELETE of the row and raises ``StaleDataError`` when another writer got there first."""

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    @declared_attr.directive
    @classmethod
    def __mapper_args__(cls) -> dict[str, Any]:
        # ``eager_defaults`` reads ``updated_at`` back with RETURNING, so a row can be
        # serialised after a flush without a lazy load (which an async session forbids).
        return {"version_id_col": cls.version, "eager_defaults": True}


def touch(entity: Any) -> None:
    """Bump ``version`` and ``updated_at`` of a row whose own columns did not change, because
    something it owns (skills of a member, efforts of a task) did."""
    entity.updated_at = func.now()
