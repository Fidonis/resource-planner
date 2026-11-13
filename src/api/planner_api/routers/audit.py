"""Audit log endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from planner_api.auth.deps import CtxDep, guard
from planner_api.auth.permissions import AUDIT_READ
from planner_api.routers.params import PageDep
from planner_api.schemas.audit import AuditEntry
from planner_api.schemas.common import ErrorResponses, Page
from planner_api.services import audit as service

router = APIRouter(prefix="/audit-log", tags=["audit"], responses=ErrorResponses)


@router.get(
    "",
    operation_id="audit_list",
    summary="Read the audit log",
    description=(
        "Newest first. Every change records how it reached the API (`via`) and the name of the "
        "calling client (`clientName`), so changes made by other programs are recognisable."
    ),
    response_model=Page[AuditEntry],
    **guard(AUDIT_READ),
)
async def list_audit(
    ctx: CtxDep,
    paging: PageDep,
    entity_type: Annotated[str | None, Query(alias="entityType", examples=["member"])] = None,
    entity_id: Annotated[str | None, Query(alias="entityId")] = None,
    actor_user_id: Annotated[uuid.UUID | None, Query(alias="actorUserId")] = None,
    action: Annotated[str | None, Query(examples=["update"])] = None,
    via: Annotated[str | None, Query(examples=["session"])] = None,
    client_name: Annotated[str | None, Query(alias="clientName")] = None,
    since: Annotated[datetime | None, Query(description="Entries at or after this time.")] = None,
    until: Annotated[datetime | None, Query(description="Entries before this time.")] = None,
) -> Page[AuditEntry]:
    return await service.list_entries(
        ctx,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        action=action,
        via=via,
        client_name=client_name,
        since=since,
        until=until,
        page=paging.page,
        page_size=paging.page_size,
        sort=paging.sort,
    )
