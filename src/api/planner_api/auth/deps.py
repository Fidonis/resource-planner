"""FastAPI dependencies: who is calling, and the context every service receives."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from planner_api.auth.passwords import hash_token
from planner_api.auth.principal import Principal
from planner_api.config import Settings
from planner_api.errors import ErrorCode, ProblemError
from planner_api.repositories import auth as repo
from planner_api.services import accounts
from planner_api.services.context import Ctx, Expected, Uow, parse_if_match, utcnow

SESSION_COOKIE = "rp_session"
API_TOKEN_PREFIX = "rpt_"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# How often the time of last use of an API token is written.
TOKEN_TOUCH_INTERVAL = timedelta(minutes=1)
# What a user who still has to choose a new password may call.
PASSWORD_CHANGE_ALLOWED = frozenset(
    {"/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/auth/change-password"}
)
_CLIENT_NAME = re.compile(r"[^A-Za-z0-9._\- ]")


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _client_name(raw: str | None) -> str | None:
    if not raw:
        return None
    return _CLIENT_NAME.sub("", raw)[:80] or None


async def _open_uow(request: Request, *, dry_run: bool) -> AsyncIterator[Uow]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield Uow(session, dry_run=dry_run)


async def get_uow(request: Request) -> AsyncIterator[Uow]:
    async for uow in _open_uow(request, dry_run=False):
        yield uow


async def get_dry_run_uow(
    request: Request,
    dry_run: Annotated[
        bool,
        Query(
            alias="dryRun",
            description="Validate the change and compute its effect, but persist nothing.",
        ),
    ] = False,
) -> AsyncIterator[Uow]:
    async for uow in _open_uow(request, dry_run=dry_run):
        yield uow


async def _authenticate(request: Request, uow: Uow, settings: Settings) -> tuple[Principal, str]:
    """Return the principal and how it authenticated (session, api-token or bearer)."""
    session = uow.session
    now = utcnow()
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if token.startswith(API_TOKEN_PREFIX):
            return await _api_token_principal(session, settings, token, now), "api-token"
        from planner_api.auth.bearer import bearer_principal

        return await bearer_principal(request, session, settings, token), "bearer"

    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        found = await accounts.session_user(session, cookie, now)
        if found is not None:
            user, row = found
            if request.method in UNSAFE_METHODS and not accounts.csrf_matches(
                row.csrf_token, request.headers.get("x-csrf-token")
            ):
                raise ProblemError(
                    403, ErrorCode.CSRF_FAILED, "The CSRF token is missing or wrong."
                )
            principal = await accounts.build_principal(session, settings, user, csrf=row.csrf_token)
            return principal, "session"
    raise ProblemError(
        401,
        ErrorCode.AUTH_REQUIRED,
        "Sign in or send valid credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _api_token_principal(
    session: AsyncSession, settings: Settings, token: str, now: datetime
) -> Principal:
    row = await repo.get_api_token_by_hash(session, hash_token(token))
    invalid = ProblemError(
        401, ErrorCode.AUTH_REQUIRED, "The API token is invalid, expired or revoked."
    )
    if row is None or row.revoked_at is not None:
        raise invalid
    if row.expires_at is not None and row.expires_at <= now:
        raise invalid
    user = await repo.get_user(session, row.user_id)
    if user is None or not user.active:
        raise invalid
    owner = await accounts.build_principal(session, settings, user)
    if row.last_used_at is None or now - row.last_used_at >= TOKEN_TOUCH_INTERVAL:
        # Saved at once: a read-only call never commits, and the time of use is worth keeping.
        await repo.touch_api_token(session, row.id, now)
        await session.commit()
    # A token can only narrow what its owner may do.
    return owner.restrict(row.permissions)


async def build_ctx(request: Request, uow: Uow, *, allow_pending_password: bool) -> Ctx:
    settings = get_settings_dep(request)
    principal, via = await _authenticate(request, uow, settings)
    path = request.url.path
    # Only a password session has to change the password first: a token or a bearer token is not
    # the password, and a service account that only uses tokens would never get past it.
    if (
        principal.must_change_password
        and via == "session"
        and not allow_pending_password
        and path not in PASSWORD_CHANGE_ALLOWED
    ):
        raise ProblemError(
            403,
            ErrorCode.PASSWORD_CHANGE_REQUIRED,
            "Choose a new password before you continue.",
        )
    return Ctx(
        uow=uow,
        principal=principal,
        settings=settings,
        via=via,
        client_name=_client_name(request.headers.get("x-client-name")),
        request_id=getattr(request.state, "request_id", None),
    )


async def get_ctx(request: Request, uow: Annotated[Uow, Depends(get_uow)]) -> Ctx:
    return await build_ctx(request, uow, allow_pending_password=False)


async def get_dry_run_ctx(request: Request, uow: Annotated[Uow, Depends(get_dry_run_uow)]) -> Ctx:
    return await build_ctx(request, uow, allow_pending_password=False)


async def expected_version(
    if_match: Annotated[
        str | None,
        Header(
            alias="If-Match",
            description="Version of the record being changed, as returned in its ETag.",
        ),
    ] = None,
) -> int | None:
    return parse_if_match(if_match)


CtxDep = Annotated[Ctx, Depends(get_ctx)]
DryRunCtxDep = Annotated[Ctx, Depends(get_dry_run_ctx)]
IfMatch = Annotated[int | None, Depends(expected_version)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
UowDep = Annotated[Uow, Depends(get_uow)]


def expect(header: int | None, body: int | None = None) -> Expected:
    return Expected(header=header, body=body)


def perm(marker: str) -> dict[str, object]:
    """``openapi_extra`` for routes that need no permission: ``public`` or ``authenticated``."""
    return {"x-permission": marker}


def guard(permission: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Route options that demand ``permission`` before the body is even validated.

    The permission also lands in the OpenAPI document as ``x-permission``; a test walks every
    route and calls it as a caller without that permission.
    """

    async def check(ctx: Ctx) -> None:
        ctx.principal.require(permission)

    # Annotations are strings in this module; a closure variable cannot be resolved from a
    # string, so the dependency is attached to the parameter as an object instead.
    check.__annotations__["ctx"] = Annotated[Ctx, Depends(get_dry_run_ctx if dry_run else get_ctx)]
    return {"dependencies": [Depends(check)], "openapi_extra": {"x-permission": permission}}


def guard_any(permission: str, *alternatives: str, dry_run: bool = False) -> dict[str, Any]:
    """Like :func:`guard`, but any one of the permissions is enough.

    ``permission`` is the one documented as ``x-permission`` and named in the refusal; the
    alternatives are listed in ``x-permission-alternatives``. The service still decides what the
    weaker permission covers (for example only the caller's own member).
    """
    accepted = (permission, *alternatives)

    async def check(ctx: Ctx) -> None:
        if not any(ctx.principal.has(p) for p in accepted):
            ctx.principal.require(permission)

    check.__annotations__["ctx"] = Annotated[Ctx, Depends(get_dry_run_ctx if dry_run else get_ctx)]
    return {
        "dependencies": [Depends(check)],
        "openapi_extra": {
            "x-permission": permission,
            "x-permission-alternatives": list(alternatives),
        },
    }


def new_request_id() -> str:
    return uuid.uuid4().hex
