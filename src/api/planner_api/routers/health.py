"""Liveness and readiness (NFR-API-06). Not part of the versioned API."""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text

from planner_api.auth.deps import UowDep, perm

router = APIRouter(prefix="/health", tags=["health"])


class Health(BaseModel):
    status: str


@router.get(
    "/live",
    operation_id="health_live",
    summary="The process is up",
    response_model=Health,
    openapi_extra=perm("public"),
)
async def live() -> Health:
    return Health(status="ok")


@router.get(
    "/ready",
    operation_id="health_ready",
    summary="The process can reach the database",
    response_model=Health,
    responses={503: {"model": Health, "description": "The database is not reachable."}},
    openapi_extra=perm("public"),
)
async def ready(uow: UowDep, response: Response) -> Health:
    try:
        await uow.session.execute(text("SELECT 1"))
    except Exception:
        response.status_code = 503
        return Health(status="database unavailable")
    return Health(status="ok")
