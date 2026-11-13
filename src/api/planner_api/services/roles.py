"""Role management (FR-ADM-02): custom roles and the mapping of external roles to them."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence

from sqlalchemy import delete

from planner_api.auth.permissions import (
    ALL_PERMISSIONS,
    DESCRIPTIONS,
    ROLE_ADMIN,
    TEAM_SCOPED,
    USER_ADMIN,
)
from planner_api.errors import ErrorCode, ProblemError, blocked
from planner_api.models import ExternalRoleMapping, Role, RolePermission
from planner_api.repositories import admin as repo
from planner_api.schemas.admin import (
    MappingOut,
    MappingsIn,
    MappingsOut,
    PermissionOut,
    RoleIn,
    RoleOut,
    RoleUpdate,
)
from planner_api.schemas.common import Page
from planner_api.services.common import snapshot
from planner_api.services.context import Ctx, Expected
from planner_api.services.users import ensure_local_admin_remains

AUDITED = ("key", "name", "description", "builtin")
ORDER = {permission: index for index, permission in enumerate(ALL_PERMISSIONS)}


def _may_read(ctx: Ctx) -> None:
    """The user editor lists roles, so ``user:admin`` may read them too."""
    if not (ctx.principal.has(ROLE_ADMIN) or ctx.principal.has(USER_ADMIN)):
        ctx.principal.require(ROLE_ADMIN)


def _permissions(role: Role) -> list[str]:
    return sorted(
        (link.permission for link in role.permission_links), key=lambda p: ORDER.get(p, 99)
    )


def _checked(permissions: Collection[str]) -> list[str]:
    wanted = sorted(set(permissions), key=lambda p: ORDER.get(p, 99))
    if unknown := [p for p in wanted if p not in ORDER]:
        raise ProblemError(
            422,
            ErrorCode.VALIDATION_ERROR,
            f"Unknown permission '{unknown[0]}'.",
            errors=[{"field": "permissions", "message": "Unknown permission.", "code": "unknown"}],
        )
    return wanted


def _out(role: Role, users: int, mappings: int) -> RoleOut:
    return RoleOut(
        id=role.id,
        version=role.version,
        created_at=role.created_at,
        updated_at=role.updated_at,
        key=role.key,
        name=role.name,
        description=role.description,
        builtin=role.builtin,
        permissions=_permissions(role),
        user_count=users,
        mapping_count=mappings,
    )


async def _one(ctx: Ctx, role: Role) -> RoleOut:
    users, mappings = await repo.role_counts(ctx.session, [role.id])
    return _out(role, users.get(role.id, 0), mappings.get(role.id, 0))


def permission_catalogue(ctx: Ctx) -> list[PermissionOut]:
    _may_read(ctx)
    return [
        PermissionOut(
            permission=permission,
            description=DESCRIPTIONS[permission],
            team_scoped=permission in TEAM_SCOPED,
        )
        for permission in ALL_PERMISSIONS
    ]


async def list_roles(
    ctx: Ctx, *, q: str | None, page: int, page_size: int, sort: str | None
) -> Page[RoleOut]:
    _may_read(ctx)
    rows, total = await repo.list_roles(ctx.session, q=q, page=page, page_size=page_size, sort=sort)
    users, mappings = await repo.role_counts(ctx.session, [r.id for r in rows])
    return Page[RoleOut](
        items=[_out(r, users.get(r.id, 0), mappings.get(r.id, 0)) for r in rows], total=total
    )


async def get_role(ctx: Ctx, role_id: uuid.UUID) -> RoleOut:
    _may_read(ctx)
    return await _one(ctx, await repo.get_role(ctx.session, role_id))


async def create_role(ctx: Ctx, body: RoleIn) -> RoleOut:
    ctx.principal.require(ROLE_ADMIN)
    permissions = _checked(body.permissions)
    role = Role(
        key=body.key,
        name=body.name,
        description=body.description,
        builtin=False,
        permission_links=[RolePermission(permission=p) for p in permissions],
    )
    ctx.session.add(role)
    await ctx.uow.flush()
    ctx.audit(
        "create", "role", role.id, after={**snapshot(role, AUDITED), "permissions": permissions}
    )
    out = _out(role, 0, 0)
    await ctx.uow.finish()
    return out


def _refuse_builtin(role: Role) -> None:
    if role.builtin:
        raise ProblemError(
            409,
            ErrorCode.BUILTIN_ROLE,
            "A built-in role cannot be changed or deleted. Create a custom role instead.",
        )


async def update_role(
    ctx: Ctx, role_id: uuid.UUID, body: RoleUpdate, expected: Expected
) -> RoleOut:
    ctx.principal.require(ROLE_ADMIN)
    role = await repo.get_role(ctx.session, role_id)
    expected.check(role.version)
    _refuse_builtin(role)
    permissions = _checked(body.permissions)
    had_admin = await repo.count_local_admins(ctx.session) > 0
    before = {**snapshot(role, AUDITED), "permissions": _permissions(role)}
    role.name = body.name
    role.description = body.description
    have = {link.permission for link in role.permission_links}
    for link in [link for link in role.permission_links if link.permission not in permissions]:
        role.permission_links.remove(link)
    role.permission_links.extend(RolePermission(permission=p) for p in permissions if p not in have)
    await ctx.uow.flush()
    await ensure_local_admin_remains(ctx, had_admin)
    ctx.audit(
        "update",
        "role",
        role.id,
        before,
        {**snapshot(role, AUDITED), "permissions": permissions},
    )
    out = await _one(ctx, role)
    await ctx.uow.finish()
    return out


async def delete_role(ctx: Ctx, role_id: uuid.UUID, expected: Expected) -> None:
    ctx.principal.require(ROLE_ADMIN)
    role = await repo.get_role(ctx.session, role_id)
    expected.check(role.version)
    _refuse_builtin(role)
    if blockers := await repo.role_blockers(ctx.session, role.id):
        raise blocked("role", blockers)
    before = {**snapshot(role, AUDITED), "permissions": _permissions(role)}
    await ctx.session.delete(role)
    ctx.audit("delete", "role", role_id, before)
    await ctx.uow.finish()


# --- the mapping of external roles ---------------------------------------------------------


def _mapping_out(provider_id: str, rows: Sequence[tuple[ExternalRoleMapping, Role]]) -> MappingsOut:
    return MappingsOut(
        provider_id=provider_id,
        mappings=[
            MappingOut(
                external_role=m.external_role, role_id=r.id, role_key=r.key, role_name=r.name
            )
            for m, r in rows
        ],
    )


def _shape(rows: Sequence[tuple[ExternalRoleMapping, Role]]) -> list[str]:
    return sorted(f"{m.external_role} -> {r.key}" for m, r in rows)


async def read_mappings(ctx: Ctx) -> MappingsOut:
    ctx.principal.require(ROLE_ADMIN)
    provider_id = ctx.settings.oidc_provider_id
    return _mapping_out(provider_id, await repo.list_mappings(ctx.session, provider_id))


async def replace_mappings(ctx: Ctx, body: MappingsIn) -> MappingsOut:
    """Make the mapping of the configured identity provider exactly ``body``."""
    ctx.principal.require(ROLE_ADMIN)
    provider_id = ctx.settings.oidc_provider_id
    wanted = {(m.external_role, m.role_id) for m in body.mappings}
    roles = await repo.roles_by_id(ctx.session, {role_id for _, role_id in wanted})
    if missing := [role_id for _, role_id in wanted if role_id not in roles]:
        raise ProblemError(
            422,
            ErrorCode.VALIDATION_ERROR,
            f"Unknown role {missing[0]}.",
            errors=[{"field": "mappings", "message": "Unknown role.", "code": "unknown"}],
        )
    current = await repo.list_mappings(ctx.session, provider_id)
    before = _shape(current)
    await ctx.session.execute(
        delete(ExternalRoleMapping).where(ExternalRoleMapping.provider_id == provider_id)
    )
    for external_role, role_id in sorted(wanted, key=lambda pair: (pair[0].lower(), str(pair[1]))):
        ctx.session.add(
            ExternalRoleMapping(
                provider_id=provider_id, external_role=external_role, role_id=role_id
            )
        )
    await ctx.uow.flush()
    rows = await repo.list_mappings(ctx.session, provider_id)
    ctx.audit(
        "update",
        "role-mapping",
        provider_id,
        {"mappings": before},
        {"mappings": _shape(rows)},
    )
    out = _mapping_out(provider_id, rows)
    await ctx.uow.finish()
    return out
