"""HTTP routers. ``api_router`` carries everything under ``/api/v1``."""

from __future__ import annotations

from fastapi import APIRouter

from planner_api.routers import (
    absence_types,
    absences,
    api_tokens,
    assignments,
    audit,
    auth,
    demo,
    health,
    ingest,
    integrations,
    members,
    roles,
    skills,
    tasks,
    teams,
    users,
)
from planner_api.routers import planning as planning_router
from planner_api.routers import settings as settings_router

api_router = APIRouter(prefix="/api/v1")
for module in (
    auth,
    settings_router,
    demo,
    audit,
    users,
    roles,
    api_tokens,
    teams,
    skills,
    members,
    absence_types,
    tasks,
    assignments,
    absences,
    planning_router,
    integrations,
    ingest,
):
    api_router.include_router(module.router)

health_router = health.router

__all__ = ["api_router", "health_router"]
