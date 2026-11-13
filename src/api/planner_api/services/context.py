"""The unit of work and the request context every service receives."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from planner_api.auth.principal import Principal
from planner_api.config import Settings
from planner_api.errors import ErrorCode, ProblemError
from planner_api.models import AuditLog
from planner_api.services.common import jsonable

UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"
CHECK_VIOLATION = "23514"

# Unique constraints whose violation the client can fix by changing one field.
DUPLICATE_FIELDS = {
    "uq_team_name": "name",
    "uq_skill_key_lower": "key",
    "uq_member_email_lower": "email",
    "uq_absence_type_key": "key",
    "uq_task_source_key": "key",
    "uq_role_key": "key",
    "uq_app_user_username": "username",
    "uq_integration_config_name": "name",
}


def map_db_error(exc: Exception) -> ProblemError:
    """Translate a database error that slipped past the explicit checks."""
    if isinstance(exc, StaleDataError):
        return ProblemError(
            409,
            ErrorCode.VERSION_MISMATCH,
            "The record was changed by someone else. Reload it and try again.",
        )
    if isinstance(exc, IntegrityError):
        orig: Any = exc.orig
        state = getattr(orig, "sqlstate", None)
        constraint = getattr(getattr(orig, "diag", None), "constraint_name", None) or ""
        if state == UNIQUE_VIOLATION:
            field = DUPLICATE_FIELDS.get(constraint)
            return ProblemError(
                409,
                ErrorCode.DUPLICATE,
                f"The value of '{field}' is already in use."
                if field
                else "A record with the same values already exists.",
                errors=[{"field": field, "message": "Already in use.", "code": "duplicate"}]
                if field
                else None,
                extra={"constraint": constraint},
            )
        if state == FOREIGN_KEY_VIOLATION:
            return ProblemError(
                409,
                ErrorCode.DELETE_BLOCKED,
                "The record is referenced by other records.",
                extra={"constraint": constraint},
            )
        if state == CHECK_VIOLATION:
            return ProblemError(
                422,
                ErrorCode.VALIDATION_ERROR,
                "A value is outside the allowed range.",
                extra={"constraint": constraint},
            )
    return ProblemError(500, ErrorCode.INTERNAL_ERROR, "The database rejected the change.")


@dataclass
class Uow:
    """One database transaction. In dry-run mode ``finish`` rolls back instead of committing."""

    session: AsyncSession
    dry_run: bool = False

    async def flush(self) -> None:
        try:
            await self.session.flush()
        except (IntegrityError, StaleDataError, DBAPIError) as exc:
            await self.session.rollback()
            raise map_db_error(exc) from exc

    async def finish(self) -> None:
        await self.flush()
        if self.dry_run:
            await self.session.rollback()
            return
        try:
            await self.session.commit()
        except (IntegrityError, StaleDataError, DBAPIError) as exc:
            await self.session.rollback()
            raise map_db_error(exc) from exc


@dataclass
class Ctx:
    uow: Uow
    principal: Principal
    settings: Settings
    via: str = "session"
    client_name: str | None = None
    request_id: str | None = None

    @property
    def session(self) -> AsyncSession:
        return self.uow.session

    @property
    def dry_run(self) -> bool:
        return self.uow.dry_run

    def audit(
        self,
        action: str,
        entity_type: str,
        entity_id: object,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> None:
        self.session.add(
            AuditLog(
                actor_user_id=self.principal.user_id,
                actor_name=self.principal.username,
                via=self.via,
                client_name=self.client_name,
                action=action,
                entity_type=entity_type,
                entity_id=str(entity_id) if entity_id is not None else None,
                before=jsonable(before) if before is not None else None,
                after=jsonable(after) if after is not None else None,
                note=note,
            )
        )


@dataclass(frozen=True, slots=True)
class Expected:
    """The version a client believes it is changing (NFR-API-04).

    ``If-Match`` wins over a ``version`` in the body. A stale header answers 412, a stale body
    version 409, and a request that names neither is refused with 428.
    """

    header: int | None = None
    body: int | None = None

    def check(self, actual: int) -> None:
        if self.header is not None:
            if self.header != actual:
                raise ProblemError(
                    412,
                    ErrorCode.VERSION_MISMATCH,
                    f"The record is at version {actual}, not {self.header}. Reload it.",
                    extra={"currentVersion": actual},
                )
            return
        if self.body is not None:
            if self.body != actual:
                raise ProblemError(
                    409,
                    ErrorCode.VERSION_MISMATCH,
                    f"The record is at version {actual}, not {self.body}. Reload it.",
                    extra={"currentVersion": actual},
                )
            return
        raise ProblemError(
            428,
            ErrorCode.VERSION_REQUIRED,
            "Send the version you are changing in an If-Match header or in the body.",
        )


_ETAG = re.compile(r'^\s*(?:W/)?"?(\d+)"?\s*$')


def parse_if_match(value: str | None) -> int | None:
    if value is None:
        return None
    match = _ETAG.match(value)
    if not match:
        raise ProblemError(400, ErrorCode.VALIDATION_ERROR, "If-Match must carry a record version.")
    return int(match.group(1))


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> uuid.UUID:
    return uuid.uuid4()
