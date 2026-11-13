"""API tokens (NFR-API-08): long-lived credentials for programs, limited to a set of permissions."""

from __future__ import annotations

import uuid
from typing import Any

from planner_api.auth.passwords import hash_token, new_secret
from planner_api.auth.permissions import ALL_PERMISSIONS, TOKEN_ADMIN
from planner_api.errors import ErrorCode, ProblemError
from planner_api.models import ApiToken
from planner_api.repositories import admin as repo
from planner_api.repositories import auth as auth_repo
from planner_api.schemas.admin import ApiTokenCreated, ApiTokenIn, ApiTokenOut, TokenStatus
from planner_api.schemas.common import Page
from planner_api.services import accounts
from planner_api.services.context import Ctx, utcnow

TOKEN_PREFIX = "rpt_"
PREFIX_LENGTH = 12


def _status(token: ApiToken) -> TokenStatus:
    if token.revoked_at is not None:
        return "revoked"
    if token.expires_at is not None and token.expires_at <= utcnow():
        return "expired"
    return "active"


def _out(token: ApiToken, username: str) -> ApiTokenOut:
    return ApiTokenOut(
        id=token.id,
        user_id=token.user_id,
        username=username,
        name=token.name,
        prefix=token.prefix,
        permissions=list(token.permissions),
        status=_status(token),
        created_at=token.created_at,
        last_used_at=token.last_used_at,
        expires_at=token.expires_at,
        revoked_at=token.revoked_at,
    )


def _problem(field: str, message: str) -> ProblemError:
    return ProblemError(
        422,
        ErrorCode.VALIDATION_ERROR,
        message,
        errors=[{"field": field, "message": message, "code": "invalid"}],
    )


async def list_tokens(
    ctx: Ctx,
    *,
    user_id: uuid.UUID | None,
    include_revoked: bool,
    page: int,
    page_size: int,
    sort: str | None,
) -> Page[ApiTokenOut]:
    ctx.principal.require(TOKEN_ADMIN)
    rows, total = await repo.list_tokens(
        ctx.session,
        user_id=user_id,
        include_revoked=include_revoked,
        page=page,
        page_size=page_size,
        sort=sort,
    )
    return Page[ApiTokenOut](items=[_out(token, name) for token, name in rows], total=total)


async def create_token(ctx: Ctx, body: ApiTokenIn) -> ApiTokenCreated:
    ctx.principal.require(TOKEN_ADMIN)
    owner = await auth_repo.get_user(ctx.session, body.user_id)
    if owner is None:
        raise _problem("userId", "The account does not exist.")
    if not owner.active:
        raise _problem("userId", "The account is disabled.")
    permissions = sorted(set(body.permissions))
    if unknown := [p for p in permissions if p not in ALL_PERMISSIONS]:
        raise _problem("permissions", f"Unknown permission '{unknown[0]}'.")
    held = await accounts.build_principal(ctx.session, ctx.settings, owner)
    if beyond := [p for p in permissions if not held.has(p)]:
        raise _problem(
            "permissions", f"The account does not hold '{beyond[0]}', so a token cannot grant it."
        )
    if body.expires_at is not None and body.expires_at <= utcnow():
        raise _problem("expiresAt", "The expiry must lie in the future.")
    secret = TOKEN_PREFIX + new_secret(32)
    token = ApiToken(
        user_id=owner.id,
        name=body.name,
        token_hash=hash_token(secret),
        prefix=secret[:PREFIX_LENGTH],
        permissions=permissions,
        expires_at=body.expires_at,
    )
    ctx.session.add(token)
    await ctx.uow.flush()
    after: dict[str, Any] = {
        "name": token.name,
        "user": owner.username,
        "prefix": token.prefix,
        "permissions": permissions,
        "expiresAt": token.expires_at,
    }
    ctx.audit("create", "api-token", token.id, after=after)
    out = ApiTokenCreated(**_out(token, owner.username).model_dump(), token=secret)
    await ctx.uow.finish()
    return out


async def revoke_token(ctx: Ctx, token_id: uuid.UUID) -> None:
    """Switch a token off for good. Revoking twice is not an error."""
    ctx.principal.require(TOKEN_ADMIN)
    token = await repo.get_token(ctx.session, token_id)
    if token.revoked_at is None:
        token.revoked_at = utcnow()
        ctx.audit(
            "revoke", "api-token", token.id, before={"name": token.name, "prefix": token.prefix}
        )
    await ctx.uow.finish()
