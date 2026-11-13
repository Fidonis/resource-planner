"""Absence types and absences."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from planner_api.models.base import Base, IdMixin, TimestampMixin, VersionedMixin


class AbsenceType(IdMixin, TimestampMixin, VersionedMixin, Base):
    __tablename__ = "absence_type"

    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    color: Mapped[str] = mapped_column(String(7), default="#2a78d6")
    # Shown as a letter next to the colour so the type never relies on colour alone.
    letter: Mapped[str] = mapped_column(String(2), default="A", server_default="A")
    reduces_capacity: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    __table_args__ = (CheckConstraint("color ~ '^#[0-9a-fA-F]{6}$'", name="color_hex"),)


class Absence(IdMixin, TimestampMixin, VersionedMixin, Base):
    __tablename__ = "absence"

    # NULL applies to every member (company holiday).
    member_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("member.id", ondelete="CASCADE"))
    absence_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("absence_type.id", ondelete="RESTRICT")
    )
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    start_part: Mapped[str] = mapped_column(String(4), default="FULL", server_default="FULL")
    end_part: Mapped[str] = mapped_column(String(4), default="FULL", server_default="FULL")
    note: Mapped[str | None] = mapped_column(Text)
    external_source: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="range"),
        CheckConstraint("start_part IN ('FULL', 'AM', 'PM')", name="start_part"),
        CheckConstraint("end_part IN ('FULL', 'AM', 'PM')", name="end_part"),
        CheckConstraint("start_date <> end_date OR start_part = end_part", name="single_day_part"),
        Index("ix_absence_member_id_start_date_end_date", "member_id", "start_date", "end_date"),
        Index("ix_absence_start_date_end_date", "start_date", "end_date"),
        Index(
            "uq_absence_external",
            "external_source",
            "external_id",
            unique=True,
            postgresql_where="external_source IS NOT NULL",
        ),
    )
