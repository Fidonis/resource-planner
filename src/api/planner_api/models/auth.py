"""Accounts, roles, sessions and API tokens."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from planner_api.models.base import Base, IdMixin, TimestampMixin, VersionedMixin


class UserRoleTeam(Base):
    """Restricts one role assignment to a team. No rows means every team."""

    __tablename__ = "user_role_team"

    user_role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_role.id", ondelete="CASCADE"), primary_key=True
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), primary_key=True
    )


class UserRole(IdMixin, Base):
    __tablename__ = "user_role"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("role.id", ondelete="CASCADE"))

    teams: Mapped[list[UserRoleTeam]] = relationship(cascade="all, delete-orphan", lazy="raise")

    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_role_pair"),)


class RolePermission(Base):
    __tablename__ = "role_permission"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("role.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(String(64), primary_key=True)


class Role(IdMixin, TimestampMixin, VersionedMixin, Base):
    __tablename__ = "role"

    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(300))
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    permission_links: Mapped[list[RolePermission]] = relationship(
        cascade="all, delete-orphan", lazy="raise"
    )


class ExternalRoleMapping(IdMixin, Base):
    __tablename__ = "external_role_mapping"

    provider_id: Mapped[str] = mapped_column(String(64))
    external_role: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("role.id", ondelete="CASCADE"))

    __table_args__ = (
        UniqueConstraint("provider_id", "external_role", "role_id", name="uq_external_role_pair"),
    )


class AppUser(IdMixin, TimestampMixin, VersionedMixin, Base):
    __tablename__ = "app_user"

    username: Mapped[str] = mapped_column(String(160), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(255))
    auth_provider: Mapped[str] = mapped_column(String(80), default="local")
    password_hash: Mapped[str | None] = mapped_column(String(255))
    external_subject: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    locale: Mapped[str | None] = mapped_column(String(8))
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Roles of the identity provider as seen at the last sign-in. Permissions are resolved from
    # them through the mapping on every request, so a changed mapping applies at once.
    external_roles: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )

    user_roles: Mapped[list[UserRole]] = relationship(cascade="all, delete-orphan", lazy="raise")

    __table_args__ = (
        Index(
            "uq_app_user_subject",
            "auth_provider",
            "external_subject",
            unique=True,
            postgresql_where="external_subject IS NOT NULL",
        ),
    )


class AppSession(IdMixin, Base):
    __tablename__ = "app_session"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    # Identity-provider session id for RP-initiated logout (OIDC only).
    id_token_hint: Mapped[str | None] = mapped_column(String(4000))

    __table_args__ = (Index("ix_app_session_user_id", "user_id"),)


class ApiToken(IdMixin, Base):
    __tablename__ = "api_token"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    permissions: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_api_token_user_id", "user_id"),)


class LoginState(Base):
    """State of an OIDC authorization request until the callback arrives."""

    __tablename__ = "login_state"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(64))
    code_verifier: Mapped[str] = mapped_column(String(128))
    binding_hash: Mapped[str] = mapped_column(String(64))
    return_to: Mapped[str] = mapped_column(String(500), default="/")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
