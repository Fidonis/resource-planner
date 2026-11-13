"""Demo data endpoint (§6)."""

from __future__ import annotations

from fastapi import APIRouter

from planner_api.auth.deps import CtxDep, guard
from planner_api.auth.permissions import SETTINGS_ADMIN
from planner_api.schemas.common import ErrorResponses
from planner_api.schemas.planning import DemoSeedResult
from planner_api.services import demo as service

router = APIRouter(prefix="/demo-data", tags=["settings"], responses=ErrorResponses)


@router.post(
    "",
    operation_id="demo_seed",
    summary="Load the demo data",
    description=(
        "Three teams, twelve members, eight skills, thirteen tasks, assignments and absences "
        "around the current week. Only an empty installation accepts it (DEMO_SEED_NOT_EMPTY), "
        "and only when the server has `ENABLE_DEMO_SEED` on (DEMO_SEED_DISABLED)."
    ),
    status_code=201,
    response_model=DemoSeedResult,
    **guard(SETTINGS_ADMIN),
)
async def seed_demo(ctx: CtxDep) -> DemoSeedResult:
    return await service.load(ctx)
