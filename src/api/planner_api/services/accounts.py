"""Sign-in, sessions, principals and password changes."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from planner_api.auth.passwords import (
    hash_password,
    hash_token,
    new_secret,
    policy_violations,
    verify_password,
)
from planner_api.auth.principal import Principal, grants_from_roles
from planner_api.config import Settings
from planner_api.errors import ErrorCode, ProblemError
from planner_api.models import AppSession, AppUser, AuditLog
from planner_api.repositories import auth as repo
from planner_api.services.context import Ctx, utcnow

LOCAL_PROVIDER = "local"


def oidc_provider_name(settings: Settings) -> str:
    return f"oidc:{settings.oidc_provider_id}"


async def build_principal(
    session: AsyncSession, settings: Settings, user: AppUser, *, csrf: str | None = None
) -> Principal:
    """Resolve what ``user`` may do right now.

    Identity-provider users get their roles through the current mapping of the external roles
    stored at their last sign-in, so a changed mapping applies to the next request.
    """
    if user.auth_provider == LOCAL_PROVIDER:
        roles = await repo.role_grants_for_user(session, user.id)
    else:
        provider_id = user.auth_provider.partition(":")[2]
        roles = await repo.role_grants_for_external(session, provider_id, user.external_roles)
        if settings.oidc_merge_local_roles:
            roles += await repo.role_grants_for_user(session, user.id)
    member = await repo.member_of_user(session, user.id)
    return Principal(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        grants=grants_from_roles(roles),
        member_id=member.id if member else None,
        must_change_password=user.must_change_password,
        locale=user.locale,
        session_csrf=csrf,
    )


async def login_local(
    session: AsyncSession, settings: Settings, username: str, password: str
) -> AppUser:
    """Check a user name and password, counting failures and locking the account."""
    if not settings.auth_local_enabled:
        raise ProblemError(
            403, ErrorCode.AUTH_MODE_DISABLED, "Local sign-in is switched off on this server."
        )
    now = utcnow()
    user = await repo.get_user_by_username(session, username)
    if user is not None and user.locked_until and user.locked_until > now:
        raise ProblemError(
            429,
            ErrorCode.ACCOUNT_LOCKED,
            "Too many failed attempts. Try again later.",
            headers={"Retry-After": str(int((user.locked_until - now).total_seconds()) + 1)},
        )
    local = user is not None and user.auth_provider == LOCAL_PROVIDER
    ok = verify_password(user.password_hash if user and local else None, password)
    if user is None or not local or not ok:
        if user is not None and local:
            attempts, locked_until = user.failed_attempts + 1, None
            if attempts >= settings.login_max_attempts:
                attempts = 0
                locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            await repo.record_failed_login(session, user.id, attempts, locked_until)
            await session.commit()
        raise ProblemError(
            401, ErrorCode.INVALID_CREDENTIALS, "The user name or the password is wrong."
        )
    if not user.active:
        raise ProblemError(403, ErrorCode.ACCOUNT_DISABLED, "The account is disabled.")
    await repo.record_login(session, user.id, now)
    return user


async def start_session(
    session: AsyncSession,
    settings: Settings,
    user: AppUser,
    *,
    id_token_hint: str | None = None,
) -> tuple[str, AppSession]:
    token = new_secret(32)
    now = utcnow()
    row = AppSession(
        token_hash=hash_token(token),
        user_id=user.id,
        csrf_token=new_secret(24),
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
        id_token_hint=id_token_hint,
    )
    session.add(row)
    await repo.delete_expired_sessions(session, now)
    await session.flush()
    return token, row


async def session_user(
    session: AsyncSession, token: str, now: datetime
) -> tuple[AppUser, AppSession] | None:
    row = await repo.get_session_by_hash(session, hash_token(token))
    if row is None or row.expires_at <= now:
        return None
    user = await repo.get_user(session, row.user_id)
    if user is None or not user.active:
        return None
    return user, row


def csrf_matches(expected: str, given: str | None) -> bool:
    return given is not None and secrets.compare_digest(expected, given)


def login_event(user: AppUser, *, via: str, client_name: str | None = None) -> AuditLog:
    return AuditLog(
        actor_user_id=user.id,
        actor_name=user.username,
        via=via,
        client_name=client_name,
        action="login",
        entity_type="user",
        entity_id=str(user.id),
    )


async def end_session(ctx: Ctx, token_hash: str) -> str | None:
    """Delete a session. Returns its ID token ('' if too long to keep) when it came from an IdP."""
    row = await repo.get_session_by_hash(ctx.session, token_hash)
    hint = row.id_token_hint if row is not None else None
    await repo.delete_session(ctx.session, token_hash)
    ctx.audit("logout", "user", ctx.principal.user_id)
    return hint


async def change_password(
    ctx: Ctx, user: AppUser, current: str | None, new: str, *, keep_session: str | None
) -> None:
    """Set a new local password. Other sessions of the user end."""
    if user.auth_provider != LOCAL_PROVIDER:
        raise ProblemError(
            403, ErrorCode.AUTH_MODE_DISABLED, "The password of this account is managed elsewhere."
        )
    if current is not None and not verify_password(user.password_hash, current):
        raise ProblemError(
            422,
            ErrorCode.INVALID_CREDENTIALS,
            "The current password is wrong.",
            errors=[{"field": "currentPassword", "message": "Wrong.", "code": "wrong"}],
        )
    problems = policy_violations(
        new, minimum=ctx.settings.password_min_length, username=user.username
    )
    if problems:
        raise ProblemError(
            422,
            ErrorCode.PASSWORD_POLICY,
            problems[0],
            errors=[{"field": "newPassword", "message": m, "code": "policy"} for m in problems],
        )
    user.password_hash = hash_password(new)
    user.must_change_password = False
    await repo.delete_user_sessions(
        ctx.session, user.id, except_hash=hash_token(keep_session) if keep_session else None
    )
    ctx.audit("password.change", "user", user.id)
