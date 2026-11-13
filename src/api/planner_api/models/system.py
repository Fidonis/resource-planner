"""Integrations, sync runs, audit log and settings."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from planner_api.models.base import Base, IdMixin, TimestampMixin, VersionedMixin


class IntegrationConfig(IdMixin, TimestampMixin, VersionedMixin, Base):
    __tablename__ = "integration_config"

    type: Mapped[str] = mapped_column(String(32))
    provider_key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Secret fields (marked writeOnly in the provider's schema) are stored encrypted.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    schedule: Mapped[str | None] = mapped_column(String(120))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_status: Mapped[str | None] = mapped_column(String(24))


class SyncRun(IdMixin, Base):
    __tablename__ = "sync_run"

    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_config.id", ondelete="CASCADE")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="running")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    full: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    log: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    triggered_by: Mapped[str | None] = mapped_column(String(160))

    __table_args__ = (
        Index("ix_sync_run_integration_id_started_at", "integration_id", "started_at"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    actor_name: Mapped[str | None] = mapped_column(String(160))
    via: Mapped[str] = mapped_column(String(16))
    client_name: Mapped[str | None] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_audit_log_at", "at"),
        Index("ix_audit_log_entity_type_entity_id", "entity_type", "entity_id"),
        Index("ix_audit_log_actor_user_id", "actor_user_id"),
    )


class AppSetting(Base):
    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
