"""Members and their skills (FR-MD-03, DM-01, DM-02, DM-04)."""

from __future__ import annotations

import uuid
from collections.abc import Collection

from planner_api.auth.permissions import MEMBER_READ, MEMBER_WRITE
from planner_api.errors import blocked, validation
from planner_api.models import Member, MemberSkill
from planner_api.models.base import touch
from planner_api.repositories import org as repo
from planner_api.repositories import settings as settings_repo
from planner_api.schemas.common import Page
from planner_api.schemas.org import (
    MemberCreate,
    MemberOut,
    MemberSkillsIn,
    MemberSkillsResult,
    MemberUpdate,
    SkillConflict,
)
from planner_api.services.common import reject_locked, snapshot
from planner_api.services.context import Ctx, Expected

MANAGED_FIELDS = ["displayName", "email"]
AUDITED = (
    "team_id",
    "display_name",
    "email",
    "allocation_percent",
    "active",
    "note",
    "external_source",
    "external_id",
)


def to_out(member: Member, team_name: str, ctx: Ctx) -> MemberOut:
    return MemberOut(
        id=member.id,
        version=member.version,
        created_at=member.created_at,
        updated_at=member.updated_at,
        external_source=member.external_source,
        external_id=member.external_id,
        managed_fields=MANAGED_FIELDS if member.external_source else [],
        team_id=member.team_id,
        team_name=team_name,
        display_name=member.display_name,
        email=member.email,
        allocation_percent=member.allocation_percent,
        active=member.active,
        note=member.note,
        user_id=member.user_id,
        skill_ids=sorted(member.skill_ids, key=str),
        writable=ctx.principal.allows_team(MEMBER_WRITE, member.team_id),
    )


async def _team_name(ctx: Ctx, team_id: uuid.UUID) -> str:
    names = await repo.team_names(ctx.session, [team_id])
    if team_id not in names:
        raise validation("The team does not exist.", field="teamId", code="not_found")
    return names[team_id]


async def _require_skills(ctx: Ctx, skill_ids: Collection[uuid.UUID]) -> None:
    found = await repo.get_skills(ctx.session, set(skill_ids))
    missing = sorted(str(s) for s in set(skill_ids) - found.keys())
    if missing:
        raise validation(
            f"Unknown skills: {', '.join(missing)}.", field="skillIds", code="not_found"
        )


async def list_members(
    ctx: Ctx,
    *,
    q: str | None,
    team_id: uuid.UUID | None,
    skill_id: uuid.UUID | None,
    active: bool | None,
    page: int,
    page_size: int,
    sort: str | None,
) -> Page[MemberOut]:
    ctx.principal.require(MEMBER_READ)
    rows, total = await repo.list_members(
        ctx.session,
        q=q,
        team_id=team_id,
        skill_id=skill_id,
        active=active,
        page=page,
        page_size=page_size,
        sort=sort,
    )
    names = await repo.team_names(ctx.session, {m.team_id for m in rows})
    return Page[MemberOut](items=[to_out(m, names[m.team_id], ctx) for m in rows], total=total)


async def get_member(ctx: Ctx, member_id: uuid.UUID) -> MemberOut:
    ctx.principal.require(MEMBER_READ)
    member = await repo.get_member(ctx.session, member_id)
    return to_out(member, await _team_name(ctx, member.team_id), ctx)


async def create_member(ctx: Ctx, body: MemberCreate) -> MemberOut:
    ctx.principal.require(MEMBER_WRITE)
    ctx.principal.require_team(MEMBER_WRITE, body.team_id)
    team_name = await _team_name(ctx, body.team_id)
    await _require_skills(ctx, body.skill_ids)
    allocation = body.allocation_percent
    if allocation is None:
        allocation = (await settings_repo.load_settings(ctx.session))["default_allocation_percent"]
    member = Member(
        team_id=body.team_id,
        display_name=body.display_name,
        email=body.email,
        allocation_percent=allocation,
        active=body.active,
        note=body.note,
        skill_links=[MemberSkill(skill_id=s) for s in dict.fromkeys(body.skill_ids)],
    )
    ctx.session.add(member)
    await ctx.uow.flush()
    after = snapshot(member, AUDITED) | {"skill_ids": member.skill_ids}
    ctx.audit("create", "member", member.id, after=after)
    out = to_out(member, team_name, ctx)
    await ctx.uow.finish()
    return out


async def update_member(
    ctx: Ctx, member_id: uuid.UUID, body: MemberUpdate, expected: Expected
) -> MemberOut:
    ctx.principal.require(MEMBER_WRITE)
    member = await repo.get_member(ctx.session, member_id)
    # Moving a member needs the right in both the old and the new team (DM-01).
    ctx.principal.require_team(MEMBER_WRITE, member.team_id)
    ctx.principal.require_team(MEMBER_WRITE, body.team_id)
    expected.check(member.version)
    reject_locked(
        member.external_source,
        MANAGED_FIELDS,
        {
            "displayName": (member.display_name, body.display_name),
            "email": (member.email, body.email),
        },
    )
    team_name = await _team_name(ctx, body.team_id)
    before = snapshot(member, AUDITED)
    member.team_id = body.team_id
    member.display_name = body.display_name
    member.email = body.email
    member.allocation_percent = body.allocation_percent
    member.active = body.active
    member.note = body.note
    await ctx.uow.flush()
    ctx.audit("update", "member", member.id, before, snapshot(member, AUDITED))
    out = to_out(member, team_name, ctx)
    await ctx.uow.finish()
    return out


async def set_member_skills(
    ctx: Ctx, member_id: uuid.UUID, body: MemberSkillsIn, expected: Expected
) -> MemberSkillsResult:
    ctx.principal.require(MEMBER_WRITE)
    member = await repo.get_member(ctx.session, member_id)
    ctx.principal.require_team(MEMBER_WRITE, member.team_id)
    expected.check(member.version)
    wanted = set(body.skill_ids)
    await _require_skills(ctx, wanted)
    current = member.skill_ids
    removed = current - wanted
    added = wanted - current
    for link in [link for link in member.skill_links if link.skill_id in removed]:
        member.skill_links.remove(link)
    member.skill_links.extend(MemberSkill(skill_id=s) for s in sorted(added, key=str))
    touch(member)
    await ctx.uow.flush()
    # The member lost a skill but still has work booked for it: report it, keep it (DM-02).
    orphaned = await repo.assignments_for_skills(ctx.session, member.id, removed)
    ctx.audit(
        "update-skills",
        "member",
        member.id,
        {"skill_ids": current},
        {"skill_ids": wanted},
        note=f"{len(orphaned)} assignment group(s) use a removed skill" if orphaned else None,
    )
    out = to_out(member, await _team_name(ctx, member.team_id), ctx)
    await ctx.uow.finish()
    return MemberSkillsResult(
        member=out,
        conflicts=[
            SkillConflict(
                task_id=o.task_id,
                task_key=o.task_key,
                skill_id=o.skill_id,
                skill_key=o.skill_key,
                assignment_count=o.count,
                person_days=o.person_days,
            )
            for o in orphaned
        ],
    )


async def delete_member(ctx: Ctx, member_id: uuid.UUID, expected: Expected) -> None:
    ctx.principal.require(MEMBER_WRITE)
    member = await repo.get_member(ctx.session, member_id)
    ctx.principal.require_team(MEMBER_WRITE, member.team_id)
    expected.check(member.version)
    count = await repo.assignment_count_for_member(ctx.session, member.id)
    if count:
        raise blocked("member", [{"kind": "assignments", "count": count}])
    absences = await repo.absence_count_for_member(ctx.session, member.id)
    before = snapshot(member, AUDITED)
    await ctx.session.delete(member)
    ctx.audit(
        "delete",
        "member",
        member.id,
        before,
        note=f"{absences} absence(s) of the member were removed with it" if absences else None,
    )
    await ctx.uow.finish()
