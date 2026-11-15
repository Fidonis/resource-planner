"""Tasks, per-skill efforts and assignments."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from planner_api.models.base import Base, IdMixin, TimestampMixin, VersionedMixin


class TaskEffort(Base):
    __tablename__ = "task_effort"

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skill.id", ondelete="RESTRICT"), primary_key=True
    )
    person_days: Mapped[Decimal] = mapped_column(Numeric(6, 2))

    __table_args__ = (CheckConstraint("person_days > 0", name="positive"),)


class Task(IdMixin, TimestampMixin, VersionedMixin, Base):
    __tablename__ = "task"

    key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    owner_team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("team.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    external_source: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(255))
    external_url: Mapped[str | None] = mapped_column(String(1000))
    external_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_fields: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # What the source system knows about the task besides its effort. Only sources write these.
    due_date: Mapped[date | None] = mapped_column(Date)
    start_date: Mapped[date | None] = mapped_column(Date)
    original_estimate_hours: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    story_points: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    priority: Mapped[str | None] = mapped_column(String(64))
    assignee: Mapped[str | None] = mapped_column(String(160))
    reporter: Mapped[str | None] = mapped_column(String(160))

    efforts: Mapped[list[TaskEffort]] = relationship(cascade="all, delete-orphan", lazy="raise")

    __table_args__ = (
        CheckConstraint("status IN ('open', 'done', 'cancelled')", name="status"),
        Index("uq_task_source_key", func.coalesce(external_source, ""), key, unique=True),
        Index(
            "uq_task_external",
            "external_source",
            "external_id",
            unique=True,
            postgresql_where="external_source IS NOT NULL",
        ),
        Index("ix_task_status", "status"),
    )


class Assignment(IdMixin, TimestampMixin, VersionedMixin, Base):
    __tablename__ = "assignment"

    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("task.id", ondelete="RESTRICT"))
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skill.id", ondelete="RESTRICT"))
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("member.id", ondelete="RESTRICT"))
    week_start: Mapped[date] = mapped_column(Date)
    person_days: Mapped[Decimal] = mapped_column(Numeric(6, 2))

    __table_args__ = (
        CheckConstraint("person_days > 0", name="positive"),
        CheckConstraint("extract(isodow from week_start) = 1", name="monday"),
        UniqueConstraint(
            "task_id", "skill_id", "member_id", "week_start", name="uq_assignment_key"
        ),
        Index("ix_assignment_week_start_member_id", "week_start", "member_id"),
        Index("ix_assignment_task_id_skill_id", "task_id", "skill_id"),
    )
