"""Schemas of the audit log."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from planner_api.schemas.common import ApiModel


class AuditEntry(ApiModel):
    id: int
    at: datetime
    actor_user_id: uuid.UUID | None
    actor_name: str | None
    via: str = Field(description="session, api-token, bearer or system.")
    client_name: str | None = Field(
        description="Name the calling client gave in X-Client-Name, e.g. planner-ui."
    )
    action: str
    entity_type: str
    entity_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    note: str | None
