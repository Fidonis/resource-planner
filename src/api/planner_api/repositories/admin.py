"""Database access for the administration of users, roles, mappings and API tokens."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from planner_api.auth.permissions import USER_ADMIN
from planner_api.errors import ErrorCode, ProblemError, not_found
from planner_api.models import (
    ApiToken,
    AppUser,
    ExternalRoleMapping,
    Member,
    Role,
    RolePermission,
    UserRole,
)
from planner_api.repositories.base import like as _like
from planner_api.repositories.base import page_of, page_rows

LOCAL = "local"

# --- users -------------------------------------------------------------------------------


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> AppUser:
    stmt = (
        select(AppUser)
        .where(AppUser.id == user_id)
        .options(selectinload(AppUser.user_roles).selectinload(UserRole.teams))
    )
    user = (await session.scalars(stmt)).one_or_none()
    if user is None:
        raise not_found("User", user_id)
    return user


async def list_users(
    session: AsyncSession,
    *,
    q: str | None,
    local: bool | None,
    active: bool | None,
    page: int,
    page_size: int,
    sort: str | None,
) -> tuple[Sequence[AppUser], int]:
    stmt = select(AppUser).options(selectinload(AppUser.user_roles).selectinload(UserRole.teams))
    if q:
        pattern = _like(q)
        stmt = stmt.where(
            AppUser.username.ilike(pattern, escape="\\")
            | AppUser.display_name.ilike(pattern, escape="\\")
            | AppUser.email.ilike(pattern, escape="\\")
        )
    if local is not None:
        stmt = stmt.where(
            AppUser.auth_provider == LOCAL if local else AppUser.auth_provider != LOCAL
        )
    if active is not None:
        stmt = stmt.where(AppUser.active == active)
    return await page_of(
        session,
        stmt,
        page=page,
        page_size=page_size,
        sort=sort,
        sorts={
            "username": func.lower(AppUser.username),
            "displayName": func.lower(AppUser.display_name),
            "lastLoginAt": AppUser.last_login_at,
            "createdAt": AppUser.created_at,
        },
        default_sort="username",
    )


async def members_of_users(
    session: AsyncSession, user_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, tuple[uuid.UUID, str]]:
    if not user_ids:
        return {}
    rows = await session.execute(
        select(Member.user_id, Member.id, Member.display_name).where(Member.user_id.in_(user_ids))
    )
    return {user_id: (member_id, name) for user_id, member_id, name in rows.all() if user_id}


async def link_member(
    session: AsyncSession, user_id: uuid.UUID, member_id: uuid.UUID | None
) -> None:
    """Make ``member_id`` the member of the account, and nobody else's."""
    for other in (await session.scalars(select(Member).where(Member.user_id == user_id))).all():
        if other.id != member_id:
            other.user_id = None
    if member_id is None:
        return
    member = await session.get(Member, member_id)
    if member is None:
        raise ProblemError(
            422,
            ErrorCode.VALIDATION_ERROR,
            "The member does not exist.",
            errors=[{"field": "memberId", "message": "Unknown member.", "code": "unknown"}],
        )
    if member.user_id not in (None, user_id):
        raise ProblemError(
            409,
            ErrorCode.DUPLICATE,
            "The member belongs to another account already.",
            errors=[{"field": "memberId", "message": "Already linked.", "code": "duplicate"}],
        )
    member.user_id = user_id


async def count_local_admins(session: AsyncSession) -> int:
    """Active local accounts that hold ``user:admin`` through a role."""
    stmt = (
        select(func.count(func.distinct(AppUser.id)))
        .select_from(AppUser)
        .join(UserRole, UserRole.user_id == AppUser.id)
        .join(RolePermission, RolePermission.role_id == UserRole.role_id)
        .where(
            AppUser.auth_provider == LOCAL,
            AppUser.active.is_(True),
            RolePermission.permission == USER_ADMIN,
        )
    )
    return (await session.execute(stmt)).scalar_one()


# --- roles -------------------------------------------------------------------------------


async def get_role(session: AsyncSession, role_id: uuid.UUID) -> Role:
    stmt = select(Role).where(Role.id == role_id).options(selectinload(Role.permission_links))
    role = (await session.scalars(stmt)).one_or_none()
    if role is None:
        raise not_found("Role", role_id)
    return role


async def roles_by_id(
    session: AsyncSession, role_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, Role]:
    if not role_ids:
        return {}
    stmt = select(Role).where(Role.id.in_(role_ids)).options(selectinload(Role.permission_links))
    return {role.id: role for role in (await session.scalars(stmt)).all()}


async def list_roles(
    session: AsyncSession, *, q: str | None, page: int, page_size: int, sort: str | None
) -> tuple[Sequence[Role], int]:
    stmt = select(Role).options(selectinload(Role.permission_links))
    if q:
        pattern = _like(q)
        stmt = stmt.where(
            Role.name.ilike(pattern, escape="\\") | Role.key.ilike(pattern, escape="\\")
        )
    return await page_of(
        session,
        stmt,
        page=page,
        page_size=page_size,
        sort=sort,
        sorts={"name": func.lower(Role.name), "key": Role.key, "createdAt": Role.created_at},
        default_sort="name",
    )


async def role_counts(
    session: AsyncSession, role_ids: Collection[uuid.UUID]
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    """Accounts per role and mappings per role."""
    if not role_ids:
        return {}, {}
    users = await session.execute(
        select(UserRole.role_id, func.count())
        .where(UserRole.role_id.in_(role_ids))
        .group_by(UserRole.role_id)
    )
    mappings = await session.execute(
        select(ExternalRoleMapping.role_id, func.count())
        .where(ExternalRoleMapping.role_id.in_(role_ids))
        .group_by(ExternalRoleMapping.role_id)
    )
    return dict(users.tuples().all()), dict(mappings.tuples().all())


async def role_blockers(session: AsyncSession, role_id: uuid.UUID) -> list[dict[str, Any]]:
    users, mappings = await role_counts(session, [role_id])
    blockers = []
    if users.get(role_id):
        blockers.append({"kind": "users", "count": users[role_id]})
    if mappings.get(role_id):
        blockers.append({"kind": "mappings", "count": mappings[role_id]})
    return blockers


async def list_mappings(
    session: AsyncSession, provider_id: str
) -> Sequence[tuple[ExternalRoleMapping, Role]]:
    stmt = (
        select(ExternalRoleMapping, Role)
        .join(Role, Role.id == ExternalRoleMapping.role_id)
        .where(ExternalRoleMapping.provider_id == provider_id)
        .order_by(func.lower(ExternalRoleMapping.external_role), Role.key)
    )
    return [(m, r) for m, r in (await session.execute(stmt)).all()]


# --- API tokens --------------------------------------------------------------------------


async def get_token(session: AsyncSession, token_id: uuid.UUID) -> ApiToken:
    token = await session.get(ApiToken, token_id)
    if token is None:
        raise not_found("API token", token_id)
    return token


async def list_tokens(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    include_revoked: bool,
    page: int,
    page_size: int,
    sort: str | None,
) -> tuple[Sequence[Any], int]:
    stmt = select(ApiToken, AppUser.username).join(AppUser, AppUser.id == ApiToken.user_id)
    if user_id is not None:
        stmt = stmt.where(ApiToken.user_id == user_id)
    if not include_revoked:
        stmt = stmt.where(ApiToken.revoked_at.is_(None))
    return await page_rows(
        session,
        stmt,
        page=page,
        page_size=page_size,
        sort=sort,
        sorts={
            "createdAt": ApiToken.created_at,
            "name": func.lower(ApiToken.name),
            "lastUsedAt": ApiToken.last_used_at,
        },
        default_sort="-createdAt",
    )
