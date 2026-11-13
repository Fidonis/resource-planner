"""Skills (FR-MD-02)."""

from __future__ import annotations

import uuid

from planner_api.auth.permissions import SKILL_READ, SKILL_WRITE
from planner_api.errors import blocked
from planner_api.models import Skill
from planner_api.repositories import org as repo
from planner_api.schemas.common import Page
from planner_api.schemas.org import SkillIn, SkillOut, SkillUpdate
from planner_api.services.common import reject_locked, snapshot
from planner_api.services.context import Ctx, Expected

MANAGED_FIELDS = ["key", "name"]
AUDITED = ("key", "name", "external_source", "external_id")


def to_out(skill: Skill, member_count: int, ctx: Ctx) -> SkillOut:
    return SkillOut(
        id=skill.id,
        version=skill.version,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
        external_source=skill.external_source,
        external_id=skill.external_id,
        managed_fields=MANAGED_FIELDS if skill.external_source else [],
        key=skill.key,
        name=skill.name,
        member_count=member_count,
        writable=ctx.principal.has(SKILL_WRITE),
    )


async def list_skills(
    ctx: Ctx, *, q: str | None, page: int, page_size: int, sort: str | None
) -> Page[SkillOut]:
    ctx.principal.require(SKILL_READ)
    rows, total = await repo.list_skills(
        ctx.session, q=q, page=page, page_size=page_size, sort=sort
    )
    counts = await repo.member_counts_by_skill(ctx.session, [s.id for s in rows])
    return Page[SkillOut](items=[to_out(s, counts.get(s.id, 0), ctx) for s in rows], total=total)


async def get_skill(ctx: Ctx, skill_id: uuid.UUID) -> SkillOut:
    ctx.principal.require(SKILL_READ)
    skill = await repo.get_skill(ctx.session, skill_id)
    counts = await repo.member_counts_by_skill(ctx.session, [skill.id])
    return to_out(skill, counts.get(skill.id, 0), ctx)


async def create_skill(ctx: Ctx, body: SkillIn) -> SkillOut:
    ctx.principal.require(SKILL_WRITE)
    skill = Skill(key=body.key, name=body.name)
    ctx.session.add(skill)
    await ctx.uow.flush()
    ctx.audit("create", "skill", skill.id, after=snapshot(skill, AUDITED))
    out = to_out(skill, 0, ctx)
    await ctx.uow.finish()
    return out


async def update_skill(
    ctx: Ctx, skill_id: uuid.UUID, body: SkillUpdate, expected: Expected
) -> SkillOut:
    ctx.principal.require(SKILL_WRITE)
    skill = await repo.get_skill(ctx.session, skill_id)
    expected.check(skill.version)
    reject_locked(
        skill.external_source,
        MANAGED_FIELDS,
        {"key": (skill.key, body.key), "name": (skill.name, body.name)},
    )
    before = snapshot(skill, AUDITED)
    skill.key = body.key
    skill.name = body.name
    await ctx.uow.flush()
    ctx.audit("update", "skill", skill.id, before, snapshot(skill, AUDITED))
    counts = await repo.member_counts_by_skill(ctx.session, [skill.id])
    out = to_out(skill, counts.get(skill.id, 0), ctx)
    await ctx.uow.finish()
    return out


async def delete_skill(ctx: Ctx, skill_id: uuid.UUID, expected: Expected) -> None:
    ctx.principal.require(SKILL_WRITE)
    skill = await repo.get_skill(ctx.session, skill_id)
    expected.check(skill.version)
    blockers = await repo.skill_blockers(ctx.session, skill.id)
    if blockers:
        raise blocked("skill", blockers)
    before = snapshot(skill, AUDITED)
    await ctx.session.delete(skill)
    ctx.audit("delete", "skill", skill.id, before)
    await ctx.uow.finish()
