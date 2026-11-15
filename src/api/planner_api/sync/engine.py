"""Running a sync (FR-INT-01 to 08): one run per integration at a time, counted and logged."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from planner_api.auth.permissions import ALL_PERMISSIONS
from planner_api.auth.principal import Principal
from planner_api.config import Settings
from planner_api.errors import ErrorCode, ProblemError, not_found
from planner_api.models import IntegrationConfig, SyncRun
from planner_api.providers.base import (
    KIND_ABSENCE_SOURCE,
    KIND_DIRECTORY,
    KIND_TASK_SOURCE,
    Provider,
    ProviderError,
    SourceProblem,
)
from planner_api.providers.registry import ProviderRegistry
from planner_api.services.context import Ctx, Uow, utcnow
from planner_api.sync.apply import Applier, Tally
from planner_api.sync.mapping import (
    MAPPING_MODELS,
    AbsenceMapping,
    DirectoryMapping,
    TaskMapping,
    UnmappedTypeError,
    map_absence,
    map_person,
    map_task,
    wanted,
)
from planner_api.sync.secrets import SecretBox, SecretError, reveal

log = logging.getLogger("planner_api.sync")

SUCCESS, PARTIAL, FAILED, RUNNING, ABORTED = "success", "partial", "failed", "running", "aborted"


def split_config(stored: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """An integration keeps the provider's settings and the mapping side by side."""
    return dict(stored.get("provider") or {}), dict(stored.get("mapping") or {})


def lock_key(integration_id: uuid.UUID) -> int:
    return int.from_bytes(integration_id.bytes[:8], "big", signed=True)


def system_principal(name: str) -> Principal:
    return Principal(
        user_id=None,
        username=f"sync:{name}",
        display_name=f"Sync '{name}'",
        grants={permission: None for permission in ALL_PERMISSIONS},
    )


def problem_errors(prefix: str, exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "field": ".".join([prefix, *(str(p) for p in err["loc"])]),
            "message": err["msg"],
            "code": err["type"],
        }
        for err in exc.errors()
    ]


@dataclass
class Prepared:
    """Everything a run needs, read before the lock is taken."""

    integration_id: uuid.UUID
    name: str
    kind: str
    provider: Provider
    config: BaseModel
    mapping: BaseModel
    last_sync_at: Any


class SyncEngine:
    def __init__(
        self,
        engine: AsyncEngine,
        factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        registry: ProviderRegistry,
    ) -> None:
        self.engine = engine
        self.factory = factory
        self.settings = settings
        self.registry = registry
        self.box = SecretBox(settings.app_encryption_key)

    # --- preparation -----------------------------------------------------------------------

    def provider_for(self, kind: str, key: str) -> Provider:
        provider = self.registry.create(kind, key, self.settings)
        if provider is None:
            raise ProblemError(
                422,
                ErrorCode.PROVIDER_UNKNOWN,
                f"There is no provider '{key}' for {kind.replace('_', ' ')}s.",
                errors=[
                    {"field": "providerKey", "message": "Unknown provider.", "code": "unknown"}
                ],
            )
        return provider

    def models(self, integration: IntegrationConfig) -> tuple[Provider, BaseModel, BaseModel]:
        provider = self.provider_for(integration.type, integration.provider_key)
        raw_provider, raw_mapping = split_config(integration.config)
        try:
            config = provider.config_model.model_validate(
                reveal(provider.config_model, raw_provider, self.box)
            )
        except ValidationError as exc:
            raise ProblemError(
                422,
                ErrorCode.VALIDATION_ERROR,
                "The settings of the integration are not valid.",
                errors=problem_errors("config", exc),
            ) from exc
        except SecretError as exc:
            raise ProblemError(422, ErrorCode.PROVIDER_ERROR, str(exc)) from exc
        try:
            mapping = MAPPING_MODELS[integration.type].model_validate(raw_mapping)
        except ValidationError as exc:
            raise ProblemError(
                422,
                ErrorCode.VALIDATION_ERROR,
                "The mapping of the integration is not valid.",
                errors=problem_errors("mapping", exc),
            ) from exc
        return provider, config, mapping

    async def test(self, integration: IntegrationConfig) -> tuple[bool, str]:
        provider, config, _ = self.models(integration)
        try:
            result = await provider.test_connection(config)
        except ProviderError as exc:
            return False, str(exc)
        return result.ok, result.message

    # --- locking ---------------------------------------------------------------------------

    @asynccontextmanager
    async def _locked(self, integration_id: uuid.UUID) -> AsyncIterator[None]:
        """A session-level advisory lock on a connection of its own (FR-INT-02)."""
        key = lock_key(integration_id)
        async with self.engine.connect() as conn:
            got = (
                await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
            ).scalar_one()
            await conn.commit()
            if not got:
                raise ProblemError(
                    409, ErrorCode.SYNC_RUNNING, "A sync of this integration is running already."
                )
            try:
                yield
            finally:
                await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                await conn.commit()

    async def abort_stale_runs(self) -> int:
        """Runs left in 'running' by a process that died (nobody holds their lock any more)."""
        aborted = 0
        async with self.factory() as session:
            rows = (
                await session.execute(
                    select(SyncRun.id, SyncRun.integration_id).where(SyncRun.status == RUNNING)
                )
            ).all()
        for run_id, integration_id in rows:
            try:
                async with self._locked(integration_id), self.factory() as session:
                    await session.execute(
                        update(SyncRun)
                        .where(SyncRun.id == run_id, SyncRun.status == RUNNING)
                        .values(
                            status=ABORTED,
                            finished_at=utcnow(),
                            log=[
                                {
                                    "level": "error",
                                    "message": "The run was interrupted by a restart.",
                                    "reference": None,
                                }
                            ],
                        )
                    )
                    await session.commit()
                    aborted += 1
            except ProblemError:
                continue  # somebody is really running it
        return aborted

    async def full_due(self, integration_id: uuid.UUID) -> bool:
        """Whether a scheduled run should read the whole source this time.

        Only for providers that can read what changed: their runs see the whole source only when
        asked to, and only such a run closes what has disappeared. A run with errors counts as a
        complete read here, or one unreadable record would make every run a full one.
        """
        async with self.factory() as session:
            integration = await session.get(IntegrationConfig, integration_id)
            if integration is None:
                return False
            cls = self.registry.get(integration.type, integration.provider_key)
            if cls is None or not getattr(cls, "supports_since", False):
                return False
            since = utcnow() - timedelta(hours=self.settings.sync_full_interval_hours)
            recent = (
                await session.execute(
                    select(SyncRun.id)
                    .where(
                        SyncRun.integration_id == integration_id,
                        SyncRun.full.is_(True),
                        SyncRun.dry_run.is_(False),
                        SyncRun.status.in_((SUCCESS, PARTIAL)),
                        SyncRun.started_at >= since,
                    )
                    .limit(1)
                )
            ).first()
        return recent is None

    # --- running ---------------------------------------------------------------------------

    async def run(
        self,
        integration_id: uuid.UUID,
        *,
        dry_run: bool = False,
        full: bool = False,
        triggered_by: str = "system",
    ) -> uuid.UUID:
        """Run one sync and return the id of its :class:`SyncRun`."""
        async with self.factory() as session:
            integration = await session.get(IntegrationConfig, integration_id)
            if integration is None:
                raise not_found("Integration", integration_id)
            prepared = self._prepare(integration)
        # Without an earlier run there is nothing to read changes since: this run is complete.
        full = full or (prepared.provider.supports_since and prepared.last_sync_at is None)
        async with self._locked(integration_id):
            run_id = await self._open_run(prepared, dry_run, full, triggered_by)
            started = utcnow()
            tally = Tally()
            status = SUCCESS
            try:
                tally = await self._execute(prepared, dry_run=dry_run, full=full)
                status = PARTIAL if tally.errors else SUCCESS
            except ProviderError as exc:
                tally.error("source", str(exc))
                status = FAILED
            except ProblemError as exc:
                tally.error("run", exc.detail)
                status = FAILED
            except Exception as exc:
                log.exception("sync of %s failed", prepared.name)
                tally.error("run", f"Unexpected error: {type(exc).__name__}")
                status = FAILED
            await self._close_run(run_id, prepared, tally, status, dry_run, started)
        return run_id

    def _prepare(self, integration: IntegrationConfig) -> Prepared:
        provider, config, mapping = self.models(integration)
        return Prepared(
            integration.id,
            integration.name,
            integration.type,
            provider,
            config,
            mapping,
            integration.last_sync_at,
        )

    async def _open_run(
        self, prepared: Prepared, dry_run: bool, full: bool, triggered_by: str
    ) -> uuid.UUID:
        async with self.factory() as session:
            run = SyncRun(
                integration_id=prepared.integration_id,
                status=RUNNING,
                dry_run=dry_run,
                full=full,
                triggered_by=triggered_by[:160],
            )
            session.add(run)
            await session.commit()
            return run.id

    async def _close_run(
        self,
        run_id: uuid.UUID,
        prepared: Prepared,
        tally: Tally,
        status: str,
        dry_run: bool,
        started: Any,
    ) -> None:
        entries = [
            {"level": e.level, "message": e.message, "reference": e.reference} for e in tally.log
        ]
        if tally.truncated:
            entries.append(
                {"level": "warning", "message": "The log was cut short.", "reference": None}
            )
        async with self.factory() as session:
            await session.execute(
                update(SyncRun)
                .where(SyncRun.id == run_id)
                .values(
                    status=status,
                    finished_at=utcnow(),
                    created_count=tally.created,
                    updated_count=tally.updated,
                    skipped_count=tally.skipped,
                    error_count=tally.errors,
                    log=entries,
                )
            )
            if not dry_run:
                await session.execute(
                    update(IntegrationConfig)
                    .where(IntegrationConfig.id == prepared.integration_id)
                    .values(
                        last_sync_status=status,
                        **({"last_sync_at": started} if status != FAILED else {}),
                    )
                )
            await session.commit()

    async def _execute(self, prepared: Prepared, *, dry_run: bool, full: bool) -> Tally:
        async with self.factory() as session:
            uow = Uow(session, dry_run=dry_run)
            ctx = Ctx(
                uow,
                system_principal(prepared.name),
                self.settings,
                via="system",
                client_name=f"sync:{prepared.name}"[:80],
            )
            applier = Applier(ctx, str(prepared.integration_id), f"sync '{prepared.name}'")
            await applier.load()
            try:
                if prepared.kind == KIND_TASK_SOURCE:
                    await self._tasks(prepared, applier, full=full)
                elif prepared.kind == KIND_DIRECTORY:
                    await self._directory(prepared, applier)
                elif prepared.kind == KIND_ABSENCE_SOURCE:
                    await self._absences(prepared, applier)
                else:
                    raise ProviderError(f"Unknown integration type '{prepared.kind}'.")
                await uow.finish()
            except BaseException:
                await session.rollback()
                raise
        return applier.tally

    # --- the three kinds -------------------------------------------------------------------

    async def _tasks(self, p: Prepared, applier: Applier, *, full: bool) -> None:
        assert isinstance(p.mapping, TaskMapping)
        since = None if full or not p.provider.supports_since else p.last_sync_at
        records = []
        problems = 0
        async for item in p.provider.fetch_tasks(p.config, since):  # type: ignore[attr-defined]
            if isinstance(item, SourceProblem):
                applier.tally.error(item.reference, item.message)
                problems += 1
            else:
                records.append(map_task(item, p.mapping, applier.skill_key))
        seen = await applier.tasks(records)
        if since is None and p.mapping.missing == "CLOSE":
            self._close_guard(applier, seen, problems)
            if seen and not problems:
                await applier.close_missing_tasks(seen)

    async def _directory(self, p: Prepared, applier: Applier) -> None:
        assert isinstance(p.mapping, DirectoryMapping)
        records = []
        problems = 0
        async for item in p.provider.fetch_members(p.config):  # type: ignore[attr-defined]
            if isinstance(item, SourceProblem):
                applier.tally.error(item.reference, item.message)
                problems += 1
            elif wanted(item, p.mapping):
                records.append(map_person(item, p.mapping))
        seen = await applier.members(records)
        if p.mapping.missing == "CLOSE":
            self._close_guard(applier, seen, problems)
            if seen and not problems:
                await applier.close_missing_members(seen)

    async def _absences(self, p: Prepared, applier: Applier) -> None:
        assert isinstance(p.mapping, AbsenceMapping)
        today = utcnow().date()
        start = today - timedelta(days=p.mapping.lookback_days)
        end = today + timedelta(days=p.mapping.lookahead_days)
        records = []
        problems = 0
        async for item in p.provider.fetch_absences(p.config, start, end):  # type: ignore[attr-defined]
            if isinstance(item, SourceProblem):
                applier.tally.error(item.reference, item.message)
                problems += 1
                continue
            try:
                records.append(map_absence(item, p.mapping))
            except UnmappedTypeError as exc:
                applier.tally.error(item.person_ref, str(exc))
                problems += 1
        seen = await applier.absences(records)
        if p.mapping.missing == "CLOSE":
            self._close_guard(applier, seen, problems)
            if seen and not problems:
                await applier.remove_missing_absences(seen, start, end)

    @staticmethod
    def _close_guard(applier: Applier, seen: set[str], problems: int) -> None:
        """Closing what is missing is only safe when the source was read completely."""
        if not seen:
            applier.tally.warn("source", "The source returned nothing: nothing was closed.")
        elif problems:
            applier.tally.warn(
                "source", "Some records could not be read: nothing was closed in this run."
            )
