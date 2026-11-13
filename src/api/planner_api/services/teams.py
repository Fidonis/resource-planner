"""Teams (FR-MD-01)."""

from __future__ import annotations

import uuid

from planner_api.auth.permissions import TEAM_READ, TEAM_WRITE
from planner_api.errors import blocked
from planner_api.models import Team
from planner_api.repositories import org as repo
from planner_api.schemas.common import Page
from planner_api.schemas.org import TeamIn, TeamOut, TeamUpdate
from planner_api.services.common import reject_locked, snapshot
from planner_api.services.context import Ctx, Expected

MANAGED_FIELDS = ["name"]
AUDITED = ("name", "color", "external_source", "external_id")


def to_out(team: Team, member_count: int, ctx: Ctx) -> TeamOut:
    return TeamOut(
        id=team.id,
        version=team.version,
        created_at=team.created_at,
        updated_at=team.updated_at,
        external_source=team.external_source,
        external_id=team.external_id,
        managed_fields=MANAGED_FIELDS if team.external_source else [],
        name=team.name,
        color=team.color,
        member_count=member_count,
        writable=ctx.principal.has(TEAM_WRITE),
    )


async def list_teams(
    ctx: Ctx, *, q: str | None, page: int, page_size: int, sort: str | None
) -> Page[TeamOut]:
    ctx.principal.require(TEAM_READ)
    rows, total = await repo.list_teams(ctx.session, q=q, page=page, page_size=page_size, sort=sort)
    counts = await repo.member_counts_by_team(ctx.session, [t.id for t in rows])
    return Page[TeamOut](items=[to_out(t, counts.get(t.id, 0), ctx) for t in rows], total=total)


async def get_team(ctx: Ctx, team_id: uuid.UUID) -> TeamOut:
    ctx.principal.require(TEAM_READ)
    team = await repo.get_team(ctx.session, team_id)
    counts = await repo.member_counts_by_team(ctx.session, [team.id])
    return to_out(team, counts.get(team.id, 0), ctx)


async def create_team(ctx: Ctx, body: TeamIn) -> TeamOut:
    ctx.principal.require(TEAM_WRITE)
    team = Team(name=body.name, color=body.color)
    ctx.session.add(team)
    await ctx.uow.flush()
    ctx.audit("create", "team", team.id, after=snapshot(team, AUDITED))
    out = to_out(team, 0, ctx)
    await ctx.uow.finish()
    return out


async def update_team(
    ctx: Ctx, team_id: uuid.UUID, body: TeamUpdate, expected: Expected
) -> TeamOut:
    ctx.principal.require(TEAM_WRITE)
    team = await repo.get_team(ctx.session, team_id)
    expected.check(team.version)
    reject_locked(team.external_source, MANAGED_FIELDS, {"name": (team.name, body.name)})
    before = snapshot(team, AUDITED)
    team.name = body.name
    team.color = body.color
    await ctx.uow.flush()
    ctx.audit("update", "team", team.id, before, snapshot(team, AUDITED))
    counts = await repo.member_counts_by_team(ctx.session, [team.id])
    out = to_out(team, counts.get(team.id, 0), ctx)
    await ctx.uow.finish()
    return out


async def delete_team(ctx: Ctx, team_id: uuid.UUID, expected: Expected) -> None:
    ctx.principal.require(TEAM_WRITE)
    team = await repo.get_team(ctx.session, team_id)
    expected.check(team.version)
    blockers = await repo.team_blockers(ctx.session, team.id)
    if blockers:
        raise blocked("team", blockers)
    before = snapshot(team, AUDITED)
    await ctx.session.delete(team)
    ctx.audit("delete", "team", team.id, before)
    await ctx.uow.finish()
