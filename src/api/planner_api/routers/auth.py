"""Authentication endpoints (§7.1 Auth)."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse

from planner_api.auth.deps import SESSION_COOKIE, CtxDep, SettingsDep, UowDep, perm
from planner_api.auth.oidc import OidcError, OidcProvider
from planner_api.auth.passwords import hash_token
from planner_api.auth.principal import Principal
from planner_api.config import Settings
from planner_api.errors import ErrorCode, ProblemError
from planner_api.repositories import auth as auth_repo
from planner_api.repositories import settings as settings_repo
from planner_api.schemas.auth import (
    AuthConfig,
    ChangePasswordRequest,
    GrantOut,
    LoginRequest,
    LogoutResult,
    Me,
    PreferencesRequest,
)
from planner_api.schemas.common import ErrorResponses
from planner_api.services import accounts
from planner_api.services import oidc as flow

log = logging.getLogger("planner_api.auth")

router = APIRouter(prefix="/auth", tags=["auth"], responses=ErrorResponses)

OIDC_PATH = "/api/v1/auth/oidc"
BINDING_MAX_AGE = flow.LOGIN_STATE_MINUTES * 60


def _limit_login_attempts(request: Request) -> None:
    host = request.client.host if request.client else "unknown"
    request.app.state.login_limiter.check(host)


def me_from_principal(
    principal: Principal, *, auth_provider: str, email: str | None, via: str
) -> Me:
    grants = [
        GrantOut(
            permission=name,
            team_ids=sorted(scope, key=str) if scope is not None else None,
        )
        for name, scope in sorted(principal.grants.items())
    ]
    return Me(
        user_id=principal.user_id,
        username=principal.username,
        display_name=principal.display_name,
        email=email,
        auth_provider=auth_provider,
        locale=principal.locale,
        must_change_password=principal.must_change_password,
        member_id=principal.member_id,
        permissions=sorted(principal.grants),
        grants=grants,
        csrf_token=principal.session_csrf if via == "session" else None,
        via=via,
    )


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


@router.get(
    "/config",
    operation_id="auth_config",
    summary="Sign-in options of this server",
    description="Public. Tells the sign-in page which modes are switched on.",
    response_model=AuthConfig,
    openapi_extra=perm("public"),
)
async def auth_config(settings: SettingsDep, uow: UowDep) -> AuthConfig:
    values = await settings_repo.load_settings(uow.session)
    return AuthConfig(
        local_enabled=settings.auth_local_enabled,
        oidc_enabled=settings.auth_oidc_enabled,
        oidc_login_url="/api/v1/auth/oidc/login" if settings.auth_oidc_enabled else None,
        password_min_length=settings.password_min_length,
        default_locale=values["locale"],
    )


@router.post(
    "/login",
    operation_id="auth_login",
    summary="Sign in with a local user name and password",
    description=(
        "Public. Sets the session cookie and returns the signed-in user. "
        "Repeated failures lock the account for a while (error code ACCOUNT_LOCKED)."
    ),
    response_model=Me,
    openapi_extra=perm("public"),
    dependencies=[Depends(_limit_login_attempts)],
)
async def login(body: LoginRequest, response: Response, settings: SettingsDep, uow: UowDep) -> Me:
    session = uow.session
    user = await accounts.login_local(session, settings, body.username, body.password)
    token, row = await accounts.start_session(session, settings, user)
    principal = await accounts.build_principal(session, settings, user, csrf=row.csrf_token)
    session.add(accounts.login_event(user, via="session"))
    result = me_from_principal(
        principal, auth_provider=user.auth_provider, email=user.email, via="session"
    )
    await uow.finish()
    set_session_cookie(response, settings, token)
    return result


def _provider(request: Request, settings: Settings) -> OidcProvider:
    provider: OidcProvider | None = getattr(request.app.state, "oidc", None)
    if provider is None or not settings.auth_oidc_enabled:
        raise ProblemError(
            403, ErrorCode.AUTH_MODE_DISABLED, "Single sign-on is switched off on this server."
        )
    return provider


def _back_to_login(reason: str) -> RedirectResponse:
    response = RedirectResponse(f"/login?error={reason}", status_code=303)
    response.delete_cookie(flow.BINDING_COOKIE, path=OIDC_PATH)
    return response


@router.get(
    "/oidc/login",
    operation_id="auth_oidc_login",
    summary="Start the sign-in with the identity provider",
    description=(
        "Public. Redirects the browser to the identity provider (authorization code flow with "
        "PKCE). `next` is a path of this site to open afterwards."
    ),
    response_class=RedirectResponse,
    status_code=302,
    responses={302: {"description": "Redirect to the identity provider."}},
    openapi_extra=perm("public"),
    dependencies=[Depends(_limit_login_attempts)],
)
async def oidc_login(
    request: Request,
    settings: SettingsDep,
    uow: UowDep,
    next: Annotated[str | None, Query(description="Path to open after signing in.")] = None,
) -> RedirectResponse:
    provider = _provider(request, settings)
    try:
        url, binding = await flow.begin_login(uow.session, provider, next)
    except OidcError as exc:
        log.warning("Cannot start the OIDC sign-in: %s", exc.reason)
        return _back_to_login("oidc_unavailable")
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        flow.BINDING_COOKIE,
        binding,
        max_age=BINDING_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path=OIDC_PATH,
    )
    return response


@router.get(
    "/oidc/callback",
    operation_id="auth_oidc_callback",
    summary="Where the identity provider sends the browser back",
    description=(
        "Public. Completes the sign-in, sets the session cookie and redirects to the page that "
        "was asked for. A failed sign-in redirects to `/login?error=...`."
    ),
    response_class=RedirectResponse,
    status_code=303,
    responses={303: {"description": "Redirect to the application."}},
    openapi_extra=perm("public"),
)
async def oidc_callback(
    request: Request,
    settings: SettingsDep,
    uow: UowDep,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    provider = _provider(request, settings)
    try:
        if error or not code or not state:
            raise OidcError(f"The provider answered with an error: {error or 'no code'}"[:200])
        user, id_token, target = await flow.complete_login(
            uow.session,
            settings,
            provider,
            code=code,
            state=state,
            binding=request.cookies.get(flow.BINDING_COOKIE),
        )
        # An ID token too long to keep is marked with '' so the logout still knows the session.
        hint = id_token if len(id_token) <= flow.MAX_ID_TOKEN_HINT else ""
        token, _ = await accounts.start_session(uow.session, settings, user, id_token_hint=hint)
        await uow.finish()
    except flow.OidcAccountDisabledError:
        return _back_to_login("account_disabled")
    except OidcError as exc:
        log.warning("OIDC sign-in failed: %s", exc.reason)
        return _back_to_login("oidc_failed")
    response = RedirectResponse(target, status_code=303)
    set_session_cookie(response, settings, token)
    response.delete_cookie(flow.BINDING_COOKIE, path=OIDC_PATH)
    return response


@router.post(
    "/logout",
    operation_id="auth_logout",
    summary="End the session",
    response_model=LogoutResult,
    openapi_extra=perm("authenticated"),
)
async def logout(request: Request, response: Response, ctx: CtxDep) -> LogoutResult:
    cookie = request.cookies.get(SESSION_COOKIE)
    redirect: str | None = None
    if cookie and ctx.via == "session":
        hint = await accounts.end_session(ctx, hash_token(cookie))
        await ctx.uow.finish()
        provider: OidcProvider | None = getattr(request.app.state, "oidc", None)
        if hint is not None and provider is not None:  # a session of the identity provider
            redirect = await provider.end_session_url(hint or None)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return LogoutResult(redirect_url=redirect)


@router.get(
    "/me",
    operation_id="auth_me",
    summary="The signed-in user and their permissions",
    response_model=Me,
    openapi_extra=perm("authenticated"),
)
async def me(ctx: CtxDep) -> Me:
    user = (
        await auth_repo.get_user(ctx.session, ctx.principal.user_id)
        if ctx.principal.user_id
        else None
    )
    return me_from_principal(
        ctx.principal,
        auth_provider=user.auth_provider if user else "system",
        email=user.email if user else None,
        via=ctx.via,
    )


@router.post(
    "/change-password",
    operation_id="auth_change_password",
    summary="Choose a new password",
    description=(
        "Only for local accounts and only with a session. Other sessions of the user end. "
        "Required after the first sign-in of the bootstrap administrator and after a reset."
    ),
    status_code=204,
    openapi_extra=perm("authenticated"),
)
async def change_password(body: ChangePasswordRequest, request: Request, ctx: CtxDep) -> Response:
    if ctx.via != "session" or ctx.principal.user_id is None:
        raise ProblemError(
            403, ErrorCode.PERMISSION_DENIED, "The password can only be changed in a session."
        )
    user = await auth_repo.get_user(ctx.session, ctx.principal.user_id)
    if user is None:
        raise ProblemError(401, ErrorCode.AUTH_REQUIRED, "Sign in again.")
    current = None if user.must_change_password else body.current_password
    if current is None and not user.must_change_password:
        raise ProblemError(
            422,
            ErrorCode.VALIDATION_ERROR,
            "The current password is required.",
            errors=[{"field": "currentPassword", "message": "Required.", "code": "missing"}],
        )
    await accounts.change_password(
        ctx, user, current, body.new_password, keep_session=request.cookies.get(SESSION_COOKIE)
    )
    await ctx.uow.finish()
    return Response(status_code=204)


@router.put(
    "/me/preferences",
    operation_id="auth_update_preferences",
    summary="Change the preferences of the signed-in account",
    response_model=Me,
    openapi_extra=perm("authenticated"),
)
async def update_preferences(body: PreferencesRequest, ctx: CtxDep) -> Me:
    if ctx.principal.user_id is None:
        raise ProblemError(403, ErrorCode.PERMISSION_DENIED, "This caller has no account.")
    user = await auth_repo.get_user(ctx.session, ctx.principal.user_id)
    if user is None:
        raise ProblemError(401, ErrorCode.AUTH_REQUIRED, "Sign in again.")
    user.locale = body.locale
    result = me_from_principal(
        replace(ctx.principal, locale=user.locale),
        auth_provider=user.auth_provider,
        email=user.email,
        via=ctx.via,
    )
    await ctx.uow.finish()
    return result
