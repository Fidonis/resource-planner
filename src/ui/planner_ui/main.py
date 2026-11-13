"""Application factory of the web interface."""

from __future__ import annotations

import logging
import urllib.parse
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from planner_client import ApiError, ApiUnreachableError, PlannerApi
from planner_ui import admin_resources, resources
from planner_ui.config import Settings, get_settings
from planner_ui.crud import install as install_resource
from planner_ui.i18n import set_locale
from planner_ui.routes import admin, auth, integrations, pages, planning, transfer
from planner_ui.security import SecurityHeadersMiddleware
from planner_ui.session import (
    CsrfFailedError,
    LoginRequiredError,
    PasswordChangeRequiredError,
    negotiate_locale,
    new_request_id,
)
from planner_ui.templating import STATIC_DIR, build_environment, render, toast
from planner_ui.texts import problem_text

logger = logging.getLogger("planner_ui")


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def _current_path(request: Request) -> str:
    """The page to come back to after signing in (only for pages that can be opened by address)."""
    if _is_htmx(request):
        current = urllib.parse.urlsplit(request.headers.get("hx-current-url", ""))
        path = current.path or "/"
        return f"{path}?{current.query}" if current.query else path
    if request.method != "GET":
        return "/"
    return request.url.path + (f"?{request.url.query}" if request.url.query else "")


def _redirect(request: Request, target: str) -> Response:
    if _is_htmx(request):
        return Response(status_code=401, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


def _failure(request: Request, status: int, code: str, detail: str) -> Response:
    """An error shown as a toast to a fragment request and as a page to a full one."""
    message = problem_text(code, detail)
    if _is_htmx(request):
        return Response(
            status_code=status, headers={**toast("error", message), "HX-Reswap": "none"}
        )
    return render(request, "error.html", None, status=status, code=code, message=message)


def install_handlers(app: FastAPI) -> None:
    settings: Settings = app.state.settings

    def localise(request: Request) -> None:
        set_locale(negotiate_locale(request, settings))

    @app.exception_handler(LoginRequiredError)
    async def _login_required(request: Request, exc: LoginRequiredError) -> Response:
        target = "/login?" + urllib.parse.urlencode({"next": _current_path(request)})
        return _redirect(request, target)

    @app.exception_handler(PasswordChangeRequiredError)
    async def _password_change(request: Request, exc: PasswordChangeRequiredError) -> Response:
        return _redirect(request, "/change-password")

    @app.exception_handler(CsrfFailedError)
    async def _csrf(request: Request, exc: CsrfFailedError) -> Response:
        return _failure(request, 403, "CSRF_FAILED", "The page is out of date.")

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> Response:
        if exc.status == 401:
            return _redirect(
                request, "/login?" + urllib.parse.urlencode({"next": _current_path(request)})
            )
        status = exc.status if 400 <= exc.status < 600 else 502
        if status >= 500:
            logger.error("API error %s %s: %s", exc.status, exc.code, exc.detail)
        return _failure(request, status, exc.code, exc.detail)

    @app.exception_handler(ApiUnreachableError)
    async def _unreachable(request: Request, exc: ApiUnreachableError) -> Response:
        logger.error("API unreachable: %s", exc)
        return _failure(request, 503, "UNREACHABLE", "The service cannot be reached.")

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> Response:
        localise(request)
        code = "NOT_FOUND" if exc.status_code == 404 else f"HTTP_{exc.status_code}"
        return _failure(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> Response:
        logger.exception("unhandled error")
        localise(request)
        return _failure(request, 500, "INTERNAL_ERROR", "An unexpected error occurred.")


def create_app(settings: Settings | None = None, *, api: PlannerApi | None = None) -> FastAPI:
    settings = settings or get_settings()
    own_client = api is None
    client = api or PlannerApi(settings.api_base_url, timeout=settings.api_timeout_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        if own_client:
            await client.aclose()

    app = FastAPI(
        title="Resource Planner",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.api = client
    app.state.templates = build_environment()

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = request.headers.get("x-request-id") or new_request_id()
        # A request that raises before a page sets its own language still gets one.
        set_locale(negotiate_locale(request, settings))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    app.add_middleware(SecurityHeadersMiddleware, secure=settings.cookie_secure)
    install_handlers(app)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(auth.router)
    app.include_router(pages.router)
    app.include_router(planning.router)
    app.include_router(transfer.router)
    # Before the lists: "/roles/mappings" must not be read as the address of a role.
    app.include_router(admin.router)
    app.include_router(integrations.router)
    masterdata = APIRouter()
    for resource in (*resources.ALL, *admin_resources.ALL):
        install_resource(masterdata, resource)
    app.include_router(masterdata)
    logging.getLogger("planner_ui").setLevel(settings.log_level.upper())
    return app
