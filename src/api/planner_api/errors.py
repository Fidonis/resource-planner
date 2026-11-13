"""RFC 9457 problem responses with a stable machine-readable ``code``."""

from __future__ import annotations

from enum import StrEnum
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"


class ErrorCode(StrEnum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    CSRF_FAILED = "CSRF_FAILED"
    PASSWORD_CHANGE_REQUIRED = "PASSWORD_CHANGE_REQUIRED"
    PASSWORD_POLICY = "PASSWORD_POLICY"
    AUTH_MODE_DISABLED = "AUTH_MODE_DISABLED"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    VERSION_REQUIRED = "VERSION_REQUIRED"
    DUPLICATE = "DUPLICATE"
    DELETE_BLOCKED = "DELETE_BLOCKED"
    ASSIGNMENT_SKILL_MISMATCH = "ASSIGNMENT_SKILL_MISMATCH"
    ASSIGNMENT_SKILL_NOT_IN_TASK = "ASSIGNMENT_SKILL_NOT_IN_TASK"
    MEMBER_INACTIVE = "MEMBER_INACTIVE"
    TASK_CLOSED = "TASK_CLOSED"
    EFFORT_BELOW_ASSIGNED = "EFFORT_BELOW_ASSIGNED"
    LAST_ADMIN = "LAST_ADMIN"
    OWN_ACCOUNT = "OWN_ACCOUNT"
    BUILTIN_ROLE = "BUILTIN_ROLE"
    FIELD_LOCKED = "FIELD_LOCKED"
    MANAGED_BY_SOURCE = "MANAGED_BY_SOURCE"
    WEEK_NOT_MONDAY = "WEEK_NOT_MONDAY"
    IMPORT_FAILED = "IMPORT_FAILED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    PROVIDER_UNKNOWN = "PROVIDER_UNKNOWN"
    SYNC_RUNNING = "SYNC_RUNNING"
    DEMO_SEED_DISABLED = "DEMO_SEED_DISABLED"
    DEMO_SEED_NOT_EMPTY = "DEMO_SEED_NOT_EMPTY"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ProblemError(Exception):
    """An error that is reported to the client as a problem document."""

    def __init__(
        self,
        status: int,
        code: ErrorCode | str,
        detail: str,
        *,
        title: str | None = None,
        errors: list[dict[str, Any]] | None = None,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = str(code)
        self.detail = detail
        self.title = title or HTTPStatus(status).phrase
        self.errors = errors or []
        self.extra = extra or {}
        self.headers = headers or {}

    def body(self, instance: str | None = None, request_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": f"urn:resource-planner:problem:{self.code}",
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "code": self.code,
            "errors": self.errors,
        }
        if instance:
            body["instance"] = instance
        if request_id:
            body["requestId"] = request_id
        body.update(self.extra)
        return body


def not_found(entity: str, ident: object = None) -> ProblemError:
    what = f"{entity} {ident}" if ident is not None else entity
    return ProblemError(404, ErrorCode.NOT_FOUND, f"{what} does not exist.")


def forbidden(permission: str) -> ProblemError:
    return ProblemError(
        403,
        ErrorCode.PERMISSION_DENIED,
        f"The permission '{permission}' is required.",
        extra={"permission": permission},
    )


def validation(detail: str, *, field: str | None = None, code: str = "invalid") -> ProblemError:
    errors = [{"field": field, "message": detail, "code": code}] if field else []
    return ProblemError(422, ErrorCode.VALIDATION_ERROR, detail, errors=errors)


def conflict(code: ErrorCode, detail: str, **extra: Any) -> ProblemError:
    return ProblemError(409, code, detail, extra=extra or None)


def blocked(entity: str, blockers: list[dict[str, Any]]) -> ProblemError:
    summary = ", ".join(f"{b['count']} {b['kind']}" for b in blockers)
    return ProblemError(
        409,
        ErrorCode.DELETE_BLOCKED,
        f"The {entity} cannot be deleted because it is still used by: {summary}. "
        "Deactivate it instead, or remove what refers to it first.",
        extra={"blockers": blockers},
    )


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _respond(request: Request, problem: ProblemError) -> JSONResponse:
    return JSONResponse(
        problem.body(instance=request.url.path, request_id=_request_id(request)),
        status_code=problem.status,
        media_type=PROBLEM_JSON,
        headers=problem.headers,
    )


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError) -> JSONResponse:
        return _respond(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for item in exc.errors():
            location = [str(p) for p in item["loc"] if p not in ("body", "query", "path")]
            errors.append(
                {
                    "field": ".".join(location),
                    "message": item["msg"],
                    "code": item["type"],
                }
            )
        problem = ProblemError(
            422, ErrorCode.VALIDATION_ERROR, "The request is not valid.", errors=errors
        )
        return _respond(request, problem)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = ErrorCode.NOT_FOUND if exc.status_code == 404 else f"HTTP_{exc.status_code}"
        detail = "The resource does not exist." if exc.status_code == 404 else str(exc.detail)
        return _respond(request, ProblemError(exc.status_code, code, detail, headers=None))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        import logging

        logging.getLogger("planner_api").exception("unhandled error")
        return _respond(
            request,
            ProblemError(500, ErrorCode.INTERNAL_ERROR, "An unexpected error occurred."),
        )
