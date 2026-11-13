"""Teams, skills and members."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from planner_api.models.base import Base, IdMixin, TimestampMixin, VersionedMixin


class Team(IdMixin, TimestampMixin, VersionedMixin, Base):
    __tablename__ = "team"

    name: Mapped[str] = mapped_column(String(120), unique=True)
    color: Mapped[str] = mapped_column(String(7), default="#1b5e8c")
    external_source: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        CheckConstraint("color ~ '^#[0-9a-fA-F]{6}$'", name="color_hex"),
        Index(
            "uq_team_external",
            "external_source",
            "external_id",
            unique=True,
            postgresql_where="external_source IS NOT NULL",
        ),
    )


class Skill(IdMixin, TimestampMixin, VersionedMixin, Base):
    __tablename__ = "skill"

    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    external_source: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        Index("uq_skill_key_lower", func.lower(key), unique=True),
        Index(
            "uq_skill_external",
            "external_source",
            "external_id",
            unique=True,
            postgresql_where="external_source IS NOT NULL",
        ),
    )


class MemberSkill(Base):
    __tablename__ = "member_skill"

    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("member.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skill.id", ondelete="RESTRICT"), primary_key=True
    )


class Member(IdMixin, TimestampMixin, VersionedMixin, Base):
    __tablename__ = "member"

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team.id", ondelete="RESTRICT"))
    display_name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(255))
    allocation_percent: Mapped[int] = mapped_column(Integer, default=80, server_default="80")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    external_source: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(255))
    # Values written by the last sync. A local edit survives the next sync as long as the field
    # still differs from this snapshot (a field is overwritten only if it still equals it).
    external_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)

    skill_links: Mapped[list[MemberSkill]] = relationship(
        cascade="all, delete-orphan", lazy="raise"
    )

    __table_args__ = (
        CheckConstraint("allocation_percent BETWEEN 10 AND 100", name="allocation_range"),
        Index(
            "uq_member_email_lower",
            func.lower(email),
            unique=True,
            postgresql_where="email IS NOT NULL",
        ),
        Index(
            "uq_member_external",
            "external_source",
            "external_id",
            unique=True,
            postgresql_where="external_source IS NOT NULL",
        ),
        Index("ix_member_team_id", "team_id"),
    )

    @property
    def skill_ids(self) -> set[uuid.UUID]:
        return {link.skill_id for link in self.skill_links}
