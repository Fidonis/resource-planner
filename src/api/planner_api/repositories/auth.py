"""Database access for users, roles, sessions and API tokens."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from planner_api.auth.principal import RoleGrant
from planner_api.models import (
    ApiToken,
    AppSession,
    AppUser,
    ExternalRoleMapping,
    LoginState,
    Member,
    Role,
    UserRole,
)


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> AppUser | None:
    return await session.get(AppUser, user_id)


async def get_user_with_roles(session: AsyncSession, user_id: uuid.UUID) -> AppUser | None:
    stmt = (
        select(AppUser)
        .where(AppUser.id == user_id)
        .options(selectinload(AppUser.user_roles).selectinload(UserRole.teams))
    )
    return (await session.scalars(stmt)).one_or_none()


async def get_user_by_username(session: AsyncSession, username: str) -> AppUser | None:
    stmt = select(AppUser).where(func.lower(AppUser.username) == username.lower())
    return (await session.scalars(stmt)).one_or_none()


async def get_user_by_subject(session: AsyncSession, provider: str, subject: str) -> AppUser | None:
    stmt = select(AppUser).where(
        AppUser.auth_provider == provider, AppUser.external_subject == subject
    )
    return (await session.scalars(stmt)).one_or_none()


async def count_users(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(AppUser))).scalar_one()


async def member_of_user(session: AsyncSession, user_id: uuid.UUID) -> Member | None:
    stmt = select(Member).where(Member.user_id == user_id)
    return (await session.scalars(stmt)).first()


async def role_grants_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[RoleGrant]:
    links = (
        await session.scalars(
            select(UserRole)
            .where(UserRole.user_id == user_id)
            .options(selectinload(UserRole.teams))
        )
    ).all()
    if not links:
        return []
    roles = {
        r.id: r
        for r in (
            await session.scalars(
                select(Role)
                .where(Role.id.in_([link.role_id for link in links]))
                .options(selectinload(Role.permission_links))
            )
        ).all()
    }
    grants: list[RoleGrant] = []
    for link in links:
        role = roles[link.role_id]
        teams = frozenset(t.team_id for t in link.teams) or None
        grants.append(RoleGrant(frozenset(p.permission for p in role.permission_links), teams))
    return grants


async def role_grants_for_external(
    session: AsyncSession, provider_id: str, external_roles: Sequence[str]
) -> list[RoleGrant]:
    """Roles an identity provider's role names map to. These apply to every team."""
    if not external_roles:
        return []
    role_ids = (
        await session.scalars(
            select(ExternalRoleMapping.role_id).where(
                ExternalRoleMapping.provider_id == provider_id,
                ExternalRoleMapping.external_role.in_(list(external_roles)),
            )
        )
    ).all()
    if not role_ids:
        return []
    roles = (
        await session.scalars(
            select(Role)
            .where(Role.id.in_(set(role_ids)))
            .options(selectinload(Role.permission_links))
        )
    ).all()
    return [RoleGrant(frozenset(p.permission for p in r.permission_links), None) for r in roles]


async def get_session_by_hash(session: AsyncSession, token_hash: str) -> AppSession | None:
    stmt = select(AppSession).where(AppSession.token_hash == token_hash)
    return (await session.scalars(stmt)).one_or_none()


async def delete_session(session: AsyncSession, token_hash: str) -> None:
    await session.execute(delete(AppSession).where(AppSession.token_hash == token_hash))


async def delete_user_sessions(
    session: AsyncSession, user_id: uuid.UUID, *, except_hash: str | None = None
) -> None:
    stmt = delete(AppSession).where(AppSession.user_id == user_id)
    if except_hash:
        stmt = stmt.where(AppSession.token_hash != except_hash)
    await session.execute(stmt)


async def delete_expired_sessions(session: AsyncSession, now: datetime) -> None:
    await session.execute(delete(AppSession).where(AppSession.expires_at < now))
    await session.execute(delete(LoginState).where(LoginState.expires_at < now))


async def touch_session(session: AsyncSession, token_hash: str, now: datetime) -> None:
    await session.execute(
        update(AppSession).where(AppSession.token_hash == token_hash).values(last_seen_at=now)
    )


async def get_api_token_by_hash(session: AsyncSession, token_hash: str) -> ApiToken | None:
    stmt = select(ApiToken).where(ApiToken.token_hash == token_hash)
    return (await session.scalars(stmt)).one_or_none()


async def touch_api_token(session: AsyncSession, token_id: uuid.UUID, now: datetime) -> None:
    await session.execute(update(ApiToken).where(ApiToken.id == token_id).values(last_used_at=now))


async def get_login_state(session: AsyncSession, state: str) -> LoginState | None:
    return await session.get(LoginState, state)


async def record_login(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> None:
    """Note a successful sign-in. Written without the ORM so the version of the account stays put:
    an administrator editing the account must not clash with the account signing in."""
    await session.execute(
        update(AppUser)
        .where(AppUser.id == user_id)
        .values(failed_attempts=0, locked_until=None, last_login_at=now)
    )


async def record_failed_login(
    session: AsyncSession, user_id: uuid.UUID, attempts: int, locked_until: datetime | None
) -> None:
    values: dict[str, object] = {"failed_attempts": attempts}
    if locked_until is not None:
        values["locked_until"] = locked_until
    await session.execute(update(AppUser).where(AppUser.id == user_id).values(**values))
