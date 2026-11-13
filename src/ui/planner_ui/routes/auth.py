"""Sign-in, sign-out, the first-password page and the language of the account."""

from __future__ import annotations

import hmac
import secrets
import urllib.parse
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from planner_client import SESSION_COOKIE, ApiError, Credentials, models
from planner_ui.config import Settings
from planner_ui.i18n import gettext_, set_locale
from planner_ui.session import (
    LANGUAGE_COOKIE,
    CtxDep,
    UiContext,
    build_credentials,
    negotiate_locale,
)
from planner_ui.templating import render
from planner_ui.texts import field_error_text, login_error_text, problem_text

router = APIRouter()

LOGIN_COOKIE = "rp_login"
ONE_YEAR = 365 * 24 * 3600


def safe_next(value: str | None) -> str:
    """Only a path of this site: no scheme, no host, no way out of it."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(ch) < 32 for ch in value):
        return "/"
    return value


def set_session_cookie(response: Response, settings: Settings, creds: Credentials) -> None:
    if creds.session_cookie:
        response.set_cookie(
            SESSION_COOKIE,
            creds.session_cookie,
            httponly=True,
            samesite="lax",
            secure=settings.cookie_secure,
            path="/",
        )
    else:
        response.delete_cookie(SESSION_COOKIE, path="/")


def _login_response(
    request: Request,
    settings: Settings,
    *,
    config: models.AuthConfig,
    next_url: str,
    username: str = "",
    error: str | None = None,
    status: int = 200,
    token: str | None = None,
) -> HTMLResponse:
    token = token or secrets.token_hex(24)
    response = render(
        request,
        "login.html",
        None,
        status=status,
        config=config,
        next_url=next_url,
        username=username,
        error=error,
        login_token=token,
    )
    response.set_cookie(
        LOGIN_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        path="/login",
    )
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/", error: str | None = None) -> Response:
    settings: Settings = request.app.state.settings
    set_locale(negotiate_locale(request, settings))
    api = request.app.state.api
    creds = build_credentials(request, locale=negotiate_locale(request, settings))
    if creds.session_cookie:
        try:
            await api.auth_me(creds)
            return RedirectResponse(safe_next(next), status_code=303)
        except ApiError:
            pass
    config = await api.auth_config(creds)
    return _login_response(
        request, settings, config=config, next_url=safe_next(next), error=login_error_text(error)
    )


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
    login_token: Annotated[str, Form()] = "",
) -> Response:
    settings: Settings = request.app.state.settings
    api = request.app.state.api
    locale = negotiate_locale(request, settings)
    set_locale(locale)
    creds = build_credentials(request, locale=locale)
    creds.session_cookie = None  # a new session, whatever cookie the browser holds
    config = await api.auth_config(creds)
    target = safe_next(next)
    expected = request.cookies.get(LOGIN_COOKIE, "")
    if not expected or not hmac.compare_digest(expected, login_token):
        return _login_response(
            request,
            settings,
            config=config,
            next_url=target,
            username=username,
            error=problem_text("CSRF_FAILED", ""),
            status=403,
        )
    try:
        me = await api.auth_login(
            creds, body=models.LoginRequest(username=username, password=password)
        )
    except ApiError as exc:
        if exc.status not in {400, 401, 403, 422, 423, 429}:
            raise
        return _login_response(
            request,
            settings,
            config=config,
            next_url=target,
            username=username,
            error=problem_text(exc.code, exc.detail),
            status=401 if exc.status == 401 else exc.status,
        )
    response = RedirectResponse(
        "/change-password" if me.must_change_password else target, status_code=303
    )
    set_session_cookie(response, settings, creds)
    response.delete_cookie(LOGIN_COOKIE, path="/login")
    return response


@router.post("/logout")
async def logout(request: Request, ctx: CtxDep) -> Response:
    result = await ctx.api.auth_logout(ctx.creds)
    target = result.redirect_url or "/login"
    response: Response
    if ctx.is_htmx:
        # The identity provider lives on another origin, and a redirect that follows a form post
        # may not leave the page's own (form-action 'self'): the page navigates there itself.
        response = Response(status_code=204, headers={"HX-Redirect": target})
    else:
        response = RedirectResponse(target, status_code=303)
    set_session_cookie(response, ctx.settings, ctx.creds)
    return response


@router.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request, ctx: CtxDep) -> HTMLResponse:
    return await _password_page(ctx)


async def _password_page(
    ctx: UiContext, errors: dict[str, str] | None = None, status: int = 200
) -> HTMLResponse:
    config = await ctx.api.auth_config(ctx.creds)
    return render(
        ctx.request,
        "change_password.html",
        ctx,
        status=status,
        errors=errors or {},
        forced=ctx.me.must_change_password,
        min_length=config.password_min_length,
    )


@router.post("/change-password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    ctx: CtxDep,
    current_password: Annotated[str, Form()] = "",
    new_password: Annotated[str, Form()] = "",
    confirm_password: Annotated[str, Form()] = "",
) -> Response:
    if new_password != confirm_password:
        return await _password_page(
            ctx, {"confirm_password": gettext_("The two passwords are not the same.")}, 422
        )
    try:
        await ctx.api.auth_change_password(
            ctx.creds,
            body=models.ChangePasswordRequest(
                current_password=current_password or None, new_password=new_password
            ),
        )
    except ApiError as exc:
        if exc.status not in {400, 401, 403, 422}:
            raise
        errors = {
            {"currentPassword": "current_password", "newPassword": "new_password"}.get(
                str(e.get("field")), "new_password"
            ): field_error_text(str(e.get("code")), str(e.get("message")))
            for e in exc.errors
        }
        if not errors:
            errors = {"new_password": problem_text(exc.code, exc.detail)}
        return await _password_page(ctx, errors, 422)
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, ctx.settings, ctx.creds)
    return response


@router.post("/account/language")
async def set_language(
    request: Request,
    ctx: CtxDep,
    locale: Annotated[str, Form()] = "auto",
) -> Response:
    chosen = None if locale == "auto" else locale
    await ctx.api.auth_update_preferences(ctx.creds, body=models.PreferencesRequest(locale=chosen))
    return_to = urllib.parse.urlsplit(request.headers.get("referer", "")).path
    response = RedirectResponse(safe_next(return_to), status_code=303)
    if chosen:
        response.set_cookie(
            LANGUAGE_COOKIE,
            chosen,
            max_age=ONE_YEAR,
            samesite="lax",
            secure=ctx.settings.cookie_secure,
            path="/",
        )
    else:
        response.delete_cookie(LANGUAGE_COOKIE, path="/")
    return response
