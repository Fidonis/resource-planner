"""User management (FR-ADM-01): local accounts, their roles and the link to a member."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy.orm.attributes import flag_modified

from planner_api.auth.passwords import hash_password, new_secret, policy_violations
from planner_api.auth.permissions import USER_ADMIN
from planner_api.errors import ErrorCode, ProblemError
from planner_api.models import AppUser, UserRole, UserRoleTeam
from planner_api.repositories import admin as repo
from planner_api.repositories import auth as auth_repo
from planner_api.repositories import org as org_repo
from planner_api.schemas.admin import (
    PasswordReset,
    ResetPasswordIn,
    UserCreated,
    UserIn,
    UserOut,
    UserRoleIn,
    UserRoleOut,
    UserUpdate,
)
from planner_api.schemas.common import Page
from planner_api.services.common import reject_locked, snapshot
from planner_api.services.context import Ctx, Expected, utcnow

AUDITED = ("username", "display_name", "email", "auth_provider", "active")
PROVIDER_MANAGED = ["displayName", "email"]


def _problem(field: str, message: str, code: str = "invalid") -> ProblemError:
    return ProblemError(
        422,
        ErrorCode.VALIDATION_ERROR,
        message,
        errors=[{"field": field, "message": message, "code": code}],
    )


async def _to_out(ctx: Ctx, users: Sequence[AppUser]) -> list[UserOut]:
    session = ctx.session
    link_ids = {link.role_id for user in users for link in user.user_roles}
    team_ids = {t.team_id for user in users for link in user.user_roles for t in link.teams}
    roles = await repo.roles_by_id(session, link_ids)
    teams = await org_repo.team_names(session, team_ids)
    members = await repo.members_of_users(session, [u.id for u in users])
    now = utcnow()
    out: list[UserOut] = []
    for user in users:
        role_rows = []
        for link in sorted(user.user_roles, key=lambda link: roles[link.role_id].name.lower()):
            role = roles[link.role_id]
            ids = sorted((t.team_id for t in link.teams), key=lambda t: teams.get(t, ""))
            role_rows.append(
                UserRoleOut(
                    role_id=role.id,
                    role_key=role.key,
                    role_name=role.name,
                    team_ids=ids,
                    team_names=[teams.get(t, "") for t in ids],
                )
            )
        member = members.get(user.id)
        out.append(
            UserOut(
                id=user.id,
                version=user.version,
                created_at=user.created_at,
                updated_at=user.updated_at,
                username=user.username,
                display_name=user.display_name,
                email=user.email,
                auth_provider=user.auth_provider,
                active=user.active,
                must_change_password=user.must_change_password,
                locked=bool(user.locked_until and user.locked_until > now),
                last_login_at=user.last_login_at,
                locale=user.locale,
                member_id=member[0] if member else None,
                member_name=member[1] if member else None,
                roles=role_rows,
                external_roles=list(user.external_roles or []),
            )
        )
    return out


async def _one(ctx: Ctx, user_id: uuid.UUID) -> UserOut:
    user = await repo.get_user(ctx.session, user_id)
    return (await _to_out(ctx, [user]))[0]


async def list_users(
    ctx: Ctx,
    *,
    q: str | None,
    local: bool | None,
    active: bool | None,
    page: int,
    page_size: int,
    sort: str | None,
) -> Page[UserOut]:
    ctx.principal.require(USER_ADMIN)
    rows, total = await repo.list_users(
        ctx.session, q=q, local=local, active=active, page=page, page_size=page_size, sort=sort
    )
    return Page[UserOut](items=await _to_out(ctx, rows), total=total)


async def get_user(ctx: Ctx, user_id: uuid.UUID) -> UserOut:
    ctx.principal.require(USER_ADMIN)
    return await _one(ctx, user_id)


async def _check_roles(ctx: Ctx, roles: Iterable[UserRoleIn]) -> list[UserRoleIn]:
    """The roles and teams must exist, and a role can be given only once."""
    wanted = list(roles)
    role_ids = [r.role_id for r in wanted]
    if len(set(role_ids)) != len(role_ids):
        raise _problem("roles", "A role can be given only once.", "duplicate")
    known = await repo.roles_by_id(ctx.session, set(role_ids))
    if missing := [r for r in role_ids if r not in known]:
        raise _problem("roles", f"Unknown role {missing[0]}.", "unknown")
    team_ids = {t for r in wanted for t in r.team_ids}
    names = await org_repo.team_names(ctx.session, team_ids)
    if unknown := [t for t in team_ids if t not in names]:
        raise _problem("roles", f"Unknown team {unknown[0]}.", "unknown")
    return wanted


def _role_rows(roles: Iterable[UserRoleIn]) -> list[UserRole]:
    return [
        UserRole(
            role_id=r.role_id, teams=[UserRoleTeam(team_id=t) for t in sorted(set(r.team_ids))]
        )
        for r in roles
    ]


def _role_shape(user: AppUser) -> list[dict[str, object]]:
    return sorted(
        (
            {"roleId": str(link.role_id), "teamIds": sorted(str(t.team_id) for t in link.teams)}
            for link in user.user_roles
        ),
        key=lambda row: str(row["roleId"]),
    )


async def ensure_local_admin_remains(ctx: Ctx, had_admin: bool) -> None:
    """Refuse a change that would leave nobody who can sign in with a password and manage users.

    It runs after the change was flushed, so the count sees the new state; raising rolls it back.
    """
    if not had_admin or not ctx.settings.auth_local_enabled:
        return
    await ctx.uow.flush()
    if await repo.count_local_admins(ctx.session) == 0:
        raise ProblemError(
            409,
            ErrorCode.LAST_ADMIN,
            "This would leave no active local account that can manage users. "
            "Give the role to another account first.",
        )


def _new_password(ctx: Ctx, username: str) -> str:
    for _ in range(20):
        candidate = new_secret(12)
        if not policy_violations(
            candidate, minimum=ctx.settings.password_min_length, username=username
        ):
            return candidate
    raise ProblemError(500, ErrorCode.INTERNAL_ERROR, "No acceptable password could be generated.")


def _checked_password(ctx: Ctx, username: str, password: str, field: str) -> str:
    problems = policy_violations(
        password, minimum=ctx.settings.password_min_length, username=username
    )
    if problems:
        raise ProblemError(
            422,
            ErrorCode.PASSWORD_POLICY,
            problems[0],
            errors=[{"field": field, "message": m, "code": "policy"} for m in problems],
        )
    return password


async def create_user(ctx: Ctx, body: UserIn) -> UserCreated:
    ctx.principal.require(USER_ADMIN)
    if await auth_repo.get_user_by_username(ctx.session, body.username) is not None:
        raise ProblemError(
            409,
            ErrorCode.DUPLICATE,
            "The user name is already in use.",
            errors=[{"field": "username", "message": "Already in use.", "code": "duplicate"}],
        )
    roles = await _check_roles(ctx, body.roles)
    generated = body.password is None
    password = (
        _new_password(ctx, body.username)
        if body.password is None
        else _checked_password(ctx, body.username, body.password, "password")
    )
    user = AppUser(
        username=body.username,
        display_name=body.display_name,
        email=body.email,
        auth_provider=repo.LOCAL,
        password_hash=hash_password(password),
        active=body.active,
        must_change_password=True,
        user_roles=_role_rows(roles),
    )
    ctx.session.add(user)
    await ctx.uow.flush()
    await repo.link_member(ctx.session, user.id, body.member_id)
    await ctx.uow.flush()
    ctx.audit(
        "create",
        "user",
        user.id,
        after={**snapshot(user, AUDITED), "roles": _role_shape(user), "memberId": body.member_id},
    )
    out = (await _to_out(ctx, [await repo.get_user(ctx.session, user.id)]))[0]
    result = UserCreated(**out.model_dump(), temporary_password=password if generated else None)
    await ctx.uow.finish()
    return result


async def update_user(
    ctx: Ctx, user_id: uuid.UUID, body: UserUpdate, expected: Expected
) -> UserOut:
    ctx.principal.require(USER_ADMIN)
    user = await repo.get_user(ctx.session, user_id)
    expected.check(user.version)
    managed_by = None if user.auth_provider == repo.LOCAL else user.auth_provider
    reject_locked(
        managed_by,
        PROVIDER_MANAGED,
        {
            "displayName": (user.display_name, body.display_name),
            "email": (user.email, body.email),
        },
    )
    roles = await _check_roles(ctx, body.roles)
    had_admin = await repo.count_local_admins(ctx.session) > 0
    linked = (await repo.members_of_users(ctx.session, [user.id])).get(user.id)
    before = {
        **snapshot(user, AUDITED),
        "roles": _role_shape(user),
        "memberId": linked[0] if linked else None,
    }
    if managed_by is None:
        user.display_name = body.display_name
        user.email = body.email
    user.active = body.active
    flag_modified(user, "active")  # the roles belong to the record: a change bumps its version
    user.user_roles.clear()
    await ctx.uow.flush()
    user.user_roles.extend(_role_rows(roles))
    await repo.link_member(ctx.session, user.id, body.member_id)
    await ctx.uow.flush()
    await ensure_local_admin_remains(ctx, had_admin)
    ctx.audit(
        "update",
        "user",
        user.id,
        before,
        {**snapshot(user, AUDITED), "roles": _role_shape(user), "memberId": body.member_id},
    )
    out = await _one(ctx, user.id)
    await ctx.uow.finish()
    return out


async def delete_user(ctx: Ctx, user_id: uuid.UUID, expected: Expected) -> None:
    ctx.principal.require(USER_ADMIN)
    user = await repo.get_user(ctx.session, user_id)
    expected.check(user.version)
    if user.id == ctx.principal.user_id:
        raise ProblemError(
            409, ErrorCode.OWN_ACCOUNT, "You cannot delete the account you are signed in with."
        )
    had_admin = await repo.count_local_admins(ctx.session) > 0
    before = {**snapshot(user, AUDITED), "roles": _role_shape(user)}
    await ctx.session.delete(user)
    await ctx.uow.flush()
    await ensure_local_admin_remains(ctx, had_admin)
    ctx.audit("delete", "user", user_id, before)
    await ctx.uow.finish()


async def reset_password(ctx: Ctx, user_id: uuid.UUID, body: ResetPasswordIn) -> PasswordReset:
    ctx.principal.require(USER_ADMIN)
    user = await repo.get_user(ctx.session, user_id)
    if user.auth_provider != repo.LOCAL:
        raise ProblemError(
            409, ErrorCode.AUTH_MODE_DISABLED, "The password of this account is managed elsewhere."
        )
    generated = body.new_password is None
    password = (
        _new_password(ctx, user.username)
        if body.new_password is None
        else _checked_password(ctx, user.username, body.new_password, "newPassword")
    )
    user.password_hash = hash_password(password)
    user.must_change_password = True
    user.failed_attempts = 0
    user.locked_until = None
    await auth_repo.delete_user_sessions(ctx.session, user.id)
    ctx.audit("password.reset", "user", user.id)
    await ctx.uow.finish()
    return PasswordReset(temporary_password=password if generated else None)
