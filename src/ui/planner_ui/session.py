"""Who is asking: the credentials of one request and the API's answer about them.

The interface holds no session of its own. The browser's ``rp_session`` cookie is forwarded to the
API with every call, and the CSRF token the API issues for that session is what the browser must
send back; the interface compares the two before it acts on behalf of the user.
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from starlette.datastructures import FormData

from planner_client import SESSION_COOKIE, ApiError, Credentials, PlannerApi, models
from planner_ui.config import Settings
from planner_ui.i18n import get_catalogs, set_locale

LANGUAGE_COOKIE = "rp_lang"
CLIENT_NAME = "planner-ui"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Routes a user with a pending password change may still reach.
PASSWORD_CHANGE_PATHS = frozenset({"/change-password", "/logout"})


class LoginRequiredError(Exception):
    """No valid session: send the browser to the sign-in page."""


class PasswordChangeRequiredError(Exception):
    """The account has to choose a new password before it can use the application."""


class CsrfFailedError(Exception):
    """A change was requested without the token of the session."""


@dataclass
class UiContext:
    request: Request
    api: PlannerApi
    creds: Credentials
    me: models.Me
    settings: Settings
    locale: str

    @property
    def csrf_token(self) -> str:
        return self.me.csrf_token or ""

    @property
    def is_htmx(self) -> bool:
        return self.request.headers.get("hx-request") == "true"

    @property
    def permissions(self) -> frozenset[str]:
        return frozenset(self.me.permissions)

    def can(self, permission: str, team_id: uuid.UUID | str | None = None) -> bool:
        """Whether the user holds ``permission``; with a team, whether it covers that team."""
        for grant in self.me.grants:
            if grant.permission != permission:
                continue
            if team_id is None or grant.team_ids is None:
                return True
            return str(team_id) in {str(t) for t in grant.team_ids}
        return False


def client_address(request: Request) -> str | None:
    return request.client.host if request.client else None


def build_credentials(request: Request, *, locale: str) -> Credentials:
    return Credentials(
        session_cookie=request.cookies.get(SESSION_COOKIE),
        client_name=CLIENT_NAME,
        request_id=getattr(request.state, "request_id", None),
        forwarded_for=client_address(request),
        language=locale,
    )


def negotiate_locale(request: Request, settings: Settings, preferred: str | None = None) -> str:
    """Account preference, then the language cookie, then the browser, then the installation."""
    catalogs = get_catalogs()
    for candidate in (preferred, request.cookies.get(LANGUAGE_COOKIE)):
        matched = catalogs.match(candidate) if candidate else None
        if matched:
            return matched
    return (
        catalogs.negotiate(request.headers.get("accept-language"))
        or catalogs.match(settings.default_locale)
        or "en"
    )


async def submitted_token(request: Request) -> str | None:
    header = request.headers.get("x-csrf-token")
    if header:
        return header
    content_type = request.headers.get("content-type", "")
    if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
        form: FormData = await request.form()
        value: Any = form.get("csrf_token")
        return value if isinstance(value, str) else None
    return None


async def verify_csrf(request: Request, expected: str | None) -> None:
    if request.method in SAFE_METHODS:
        return
    supplied = await submitted_token(request)
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        raise CsrfFailedError


async def get_context(request: Request) -> UiContext:
    """Dependency of every page that needs a signed-in user."""
    state = request.app.state
    settings: Settings = state.settings
    api: PlannerApi = state.api
    locale = negotiate_locale(request, settings)
    set_locale(locale)
    creds = build_credentials(request, locale=locale)
    if not creds.session_cookie:
        raise LoginRequiredError
    try:
        me = await api.auth_me(creds)
    except ApiError as exc:
        if exc.status == 401:
            raise LoginRequiredError from exc
        raise
    locale = negotiate_locale(request, settings, me.locale)
    set_locale(locale)
    creds.language = locale
    creds.csrf_token = me.csrf_token
    ctx = UiContext(request, api, creds, me, settings, locale)
    if me.must_change_password and request.url.path not in PASSWORD_CHANGE_PATHS:
        raise PasswordChangeRequiredError
    await verify_csrf(request, me.csrf_token)
    return ctx


def new_request_id() -> str:
    return uuid.uuid4().hex


CtxDep = Annotated[UiContext, Depends(get_context)]
