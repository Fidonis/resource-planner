"""Application factory."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from planner_api import __version__, bootstrap, migrate
from planner_api.auth.oidc import OidcProvider
from planner_api.auth.ratelimit import RateLimiter
from planner_api.config import Settings, get_settings
from planner_api.db import create_engine, create_session_factory
from planner_api.errors import install_handlers
from planner_api.logging_setup import configure_logging
from planner_api.providers.registry import ProviderRegistry, default_registry
from planner_api.routers import api_router, health_router
from planner_api.sync.engine import SyncEngine
from planner_api.sync.scheduler import SyncScheduler

log = logging.getLogger("planner_api")

DESCRIPTION = """
Planning of person-days per skill, member and week.

Every operation has a stable `operationId`. Errors are `application/problem+json` documents with
a stable `code`. Changing calls need the current `version` of the record in `If-Match` (or in the
body). Planning calls accept `dryRun=true` to see their effect without saving it.

Authenticate with the session cookie (send the CSRF token in `X-CSRF-Token`), with an API token
(`Authorization: Bearer rpt_...`) or, when the server accepts them, with a bearer token of the
identity provider. Name your program in `X-Client-Name`; the audit log records it.
"""

TAGS = [
    {"name": "auth", "description": "Sign in, sign out and the signed-in user."},
    {"name": "settings", "description": "Application settings."},
    {"name": "audit", "description": "Who changed what, and through which client."},
    {"name": "users", "description": "Accounts, their roles and the linked member."},
    {"name": "roles", "description": "Roles, permissions and the mapping of external roles."},
    {"name": "api-tokens", "description": "Credentials for programs."},
    {"name": "teams", "description": "Teams."},
    {"name": "skills", "description": "Skills."},
    {"name": "members", "description": "Members and their skills."},
    {"name": "absence-types", "description": "Kinds of absence."},
    {"name": "tasks", "description": "Tasks and their effort per skill."},
    {"name": "assignments", "description": "Work booked for a member in a week."},
    {"name": "absences", "description": "Absences, their calendar and CSV import."},
    {"name": "planning", "description": "Planning board, overview and availability."},
    {"name": "integrations", "description": "Providers, their settings, syncs and run history."},
    {"name": "ingest", "description": "Machine API for systems that push their data."},
    {"name": "health", "description": "Liveness and readiness."},
]

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_QUIET_PATHS = ("/health/",)

SECURITY_SCHEMES: dict[str, dict[str, Any]] = {
    "sessionCookie": {"type": "apiKey", "in": "cookie", "name": "rp_session"},
    "bearerAuth": {
        "type": "http",
        "scheme": "bearer",
        "description": "An API token (`rpt_...`) or a bearer token of the identity provider.",
    },
}


def _with_security(app: FastAPI) -> dict[str, Any]:
    """OpenAPI document with the security schemes and per-operation security."""
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=TAGS,
    )
    schema.setdefault("components", {})["securitySchemes"] = SECURITY_SCHEMES
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            if operation.get("x-permission") == "public":
                operation["security"] = []
            else:
                operation["security"] = [{"sessionCookie": []}, {"bearerAuth": []}]
    app.openapi_schema = schema
    return schema


def create_app(
    settings: Settings | None = None, registry: ProviderRegistry | None = None
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    sync_engine = SyncEngine(engine, factory, settings, registry or default_registry())
    scheduler = SyncScheduler(sync_engine, factory) if settings.scheduler_enabled else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.migrate_on_start:
            await asyncio.to_thread(migrate.upgrade, settings.database_url)
        await bootstrap.reconcile(factory, settings)
        aborted = await sync_engine.abort_stale_runs()
        if aborted:
            log.warning("%d sync run(s) were interrupted by the last stop", aborted)
        if scheduler is not None:
            await scheduler.start()
        log.info("resource planner API %s is ready", __version__)
        yield
        if scheduler is not None:
            await scheduler.stop()
        await engine.dispose()

    app = FastAPI(
        title="Resource Planner API",
        version=__version__,
        description=DESCRIPTION.strip(),
        lifespan=lifespan,
        openapi_tags=TAGS,
        separate_input_output_schemas=False,
        docs_url="/api/docs" if settings.enable_api_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.sync_engine = sync_engine
    app.state.scheduler = scheduler
    app.state.login_limiter = RateLimiter(limit=20, window_seconds=300)
    app.state.oidc = OidcProvider(settings) if settings.auth_oidc_enabled else None
    app.openapi = lambda: _with_security(app)  # type: ignore[method-assign]

    install_handlers(app)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        supplied = request.headers.get("x-request-id", "")
        request.state.request_id = supplied if _REQUEST_ID.match(supplied) else uuid.uuid4().hex
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if not request.url.path.startswith(_QUIET_PATHS):
            log.info(
                "%s %s -> %s",
                request.method,
                request.url.path,
                response.status_code,
                extra={
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "client": request.client.host if request.client else None,
                },
            )
        return response

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "If-Match", "X-CSRF-Token", "X-Client-Name"],
            expose_headers=["ETag", "Location", "X-Request-ID"],
        )

    app.include_router(health_router)
    app.include_router(api_router)
    return app
