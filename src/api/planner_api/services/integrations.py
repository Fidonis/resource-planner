"""Integrations: their settings, a connection test and manual sync runs (FR-ADM-03, §9)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from planner_api.auth.permissions import INTEGRATION_ADMIN
from planner_api.errors import ErrorCode, ProblemError, blocked, validation
from planner_api.models import IntegrationConfig, SyncRun
from planner_api.repositories import integrations as repo
from planner_api.schemas.common import Page
from planner_api.schemas.integrations import (
    ConnectionResult,
    IntegrationIn,
    IntegrationOut,
    IntegrationUpdate,
    LogEntryOut,
    ProviderInfo,
    ProviderList,
    SyncRunDetail,
    SyncRunOut,
)
from planner_api.services.context import Ctx, Expected
from planner_api.sync.engine import SyncEngine, problem_errors, split_config
from planner_api.sync.mapping import MAPPING_MODELS
from planner_api.sync.scheduler import validate_schedule
from planner_api.sync.secrets import SecretError, public, reveal, seal, secret_fields


def providers(ctx: Ctx, engine: SyncEngine) -> ProviderList:
    ctx.principal.require(INTEGRATION_ADMIN)
    return ProviderList(
        items=[
            ProviderInfo(
                key=cls.key,
                type=cls.kind,
                title=cls.title,
                supports_since=bool(cls.supports_since),
                config_schema=cls.config_model.model_json_schema(),
                mapping_schema=MAPPING_MODELS[cls.kind].model_json_schema(),
            )
            for cls in engine.registry.all()
        ]
    )


def to_out(engine: SyncEngine, row: IntegrationConfig) -> IntegrationOut:
    cls = engine.registry.get(row.type, row.provider_key)
    stored, mapping = split_config(row.config)
    if cls is not None:
        shown, secrets = public(cls.config_model, stored)
    else:  # the provider is not installed: show nothing that might be a secret
        shown, secrets = {}, []
    return IntegrationOut(
        id=row.id,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        name=row.name,
        type=row.type,
        provider_key=row.provider_key,
        provider_title=cls.title if cls is not None else None,
        enabled=row.enabled,
        schedule=row.schedule,
        config=shown,
        secrets_set=secrets,
        mapping=mapping,
        last_sync_at=row.last_sync_at,
        last_sync_status=row.last_sync_status,
    )


def _run_out(run: SyncRun) -> SyncRunOut:
    return SyncRunOut(
        id=run.id,
        integration_id=run.integration_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status,
        dry_run=run.dry_run,
        full=run.full,
        created=run.created_count,
        updated=run.updated_count,
        skipped=run.skipped_count,
        errors=run.error_count,
        triggered_by=run.triggered_by,
    )


def _run_detail(run: SyncRun) -> SyncRunDetail:
    return SyncRunDetail(
        **_run_out(run).model_dump(),
        log=[LogEntryOut.model_validate(entry) for entry in run.log or []],
    )


def _audited(engine: SyncEngine, row: IntegrationConfig) -> dict[str, Any]:
    out = to_out(engine, row)
    return out.model_dump(
        include={"name", "type", "provider_key", "enabled", "schedule", "config", "mapping"}
    )


def _validated(
    engine: SyncEngine,
    kind: str,
    provider_key: str,
    config: dict[str, Any],
    mapping: dict[str, Any],
    schedule: str | None,
    previous: dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None]:
    """The settings as they are stored (secrets encrypted) and the cleaned schedule."""
    provider = engine.provider_for(kind, provider_key)
    model = provider.config_model
    given = dict(config)
    if previous:
        for name in secret_fields(model):
            if not given.get(name) and previous.get(name):
                try:
                    given[name] = reveal(model, {name: previous[name]}, engine.box)[name]
                except SecretError as exc:
                    raise ProblemError(422, ErrorCode.PROVIDER_ERROR, str(exc)) from exc
    try:
        checked = model.model_validate(given)
    except ValidationError as exc:
        raise ProblemError(
            422,
            ErrorCode.VALIDATION_ERROR,
            "The settings of the provider are not valid.",
            errors=problem_errors("config", exc),
        ) from exc
    try:
        mapped = MAPPING_MODELS[kind].model_validate(mapping)
    except ValidationError as exc:
        raise ProblemError(
            422,
            ErrorCode.VALIDATION_ERROR,
            "The mapping is not valid.",
            errors=problem_errors("mapping", exc),
        ) from exc
    cleaned = (schedule or "").strip() or None
    if cleaned:
        try:
            validate_schedule(cleaned)
        except ValueError as exc:
            raise validation(f"The schedule {exc}.", field="schedule", code="invalid_cron") from exc
    stored = {
        "provider": seal(
            model, checked.model_dump(mode="json", by_alias=True), previous, engine.box
        ),
        "mapping": mapped.model_dump(mode="json", by_alias=True),
    }
    return stored, cleaned


async def list_integrations(
    ctx: Ctx, engine: SyncEngine, *, type_: str | None, page: int, page_size: int, sort: str | None
) -> Page[IntegrationOut]:
    ctx.principal.require(INTEGRATION_ADMIN)
    rows, total = await repo.list_integrations(
        ctx.session, type_=type_, page=page, page_size=page_size, sort=sort
    )
    return Page[IntegrationOut](items=[to_out(engine, r) for r in rows], total=total)


async def get_integration(
    ctx: Ctx, engine: SyncEngine, integration_id: uuid.UUID
) -> IntegrationOut:
    ctx.principal.require(INTEGRATION_ADMIN)
    return to_out(engine, await repo.get_integration(ctx.session, integration_id))


async def create_integration(ctx: Ctx, engine: SyncEngine, body: IntegrationIn) -> IntegrationOut:
    ctx.principal.require(INTEGRATION_ADMIN)
    stored, schedule = _validated(
        engine, body.type, body.provider_key, body.config, body.mapping, body.schedule, None
    )
    row = IntegrationConfig(
        type=body.type,
        provider_key=body.provider_key,
        name=body.name,
        enabled=body.enabled,
        config=stored,
        schedule=schedule,
    )
    ctx.session.add(row)
    await ctx.uow.flush()
    ctx.audit("create", "integration", row.id, after=_audited(engine, row))
    out = to_out(engine, row)
    await ctx.uow.finish()
    return out


async def update_integration(
    ctx: Ctx,
    engine: SyncEngine,
    integration_id: uuid.UUID,
    body: IntegrationUpdate,
    expected: Expected,
) -> IntegrationOut:
    ctx.principal.require(INTEGRATION_ADMIN)
    row = await repo.get_integration(ctx.session, integration_id)
    expected.check(row.version)
    before = _audited(engine, row)
    previous, _ = split_config(row.config)
    stored, schedule = _validated(
        engine, row.type, row.provider_key, body.config, body.mapping, body.schedule, previous
    )
    row.name = body.name
    row.enabled = body.enabled
    row.schedule = schedule
    row.config = stored
    await ctx.uow.flush()
    ctx.audit("update", "integration", row.id, before, _audited(engine, row))
    out = to_out(engine, row)
    await ctx.uow.finish()
    return out


async def delete_integration(
    ctx: Ctx, integration_id: uuid.UUID, expected: Expected, engine: SyncEngine, *, detach: bool
) -> None:
    ctx.principal.require(INTEGRATION_ADMIN)
    row = await repo.get_integration(ctx.session, integration_id)
    expected.check(row.version)
    owned = await repo.owned_counts(ctx.session, str(row.id))
    if owned and not detach:
        raise blocked(
            "integration",
            [{"kind": kind, "count": count} for kind, count in owned.items()],
        )
    before = _audited(engine, row)
    if owned:
        await repo.detach(ctx.session, str(row.id))
    await ctx.session.delete(row)
    note = (
        "detached: " + ", ".join(f"{count} {kind}" for kind, count in owned.items())
        if owned
        else None
    )
    ctx.audit("delete", "integration", row.id, before, note=note)
    await ctx.uow.finish()


async def test_integration(
    ctx: Ctx, engine: SyncEngine, integration_id: uuid.UUID
) -> ConnectionResult:
    ctx.principal.require(INTEGRATION_ADMIN)
    row = await repo.get_integration(ctx.session, integration_id)
    ok, message = await engine.test(row)
    return ConnectionResult(ok=ok, message=message)


async def run_sync(
    ctx: Ctx,
    engine: SyncEngine,
    integration_id: uuid.UUID,
    *,
    dry_run: bool,
    full: bool,
) -> SyncRunDetail:
    ctx.principal.require(INTEGRATION_ADMIN)
    await repo.get_integration(ctx.session, integration_id)
    run_id = await engine.run(
        integration_id, dry_run=dry_run, full=full, triggered_by=f"user:{ctx.principal.username}"
    )
    session: AsyncSession = ctx.session
    await session.rollback()  # see the rows the run committed on its own connections
    return _run_detail(await repo.get_run(session, integration_id, run_id))


async def list_runs(
    ctx: Ctx, integration_id: uuid.UUID, *, page: int, page_size: int
) -> Page[SyncRunOut]:
    ctx.principal.require(INTEGRATION_ADMIN)
    await repo.get_integration(ctx.session, integration_id)
    rows, total = await repo.list_runs(ctx.session, integration_id, page=page, page_size=page_size)
    return Page[SyncRunOut](items=[_run_out(r) for r in rows], total=total)


async def get_run(ctx: Ctx, integration_id: uuid.UUID, run_id: uuid.UUID) -> SyncRunDetail:
    ctx.principal.require(INTEGRATION_ADMIN)
    return _run_detail(await repo.get_run(ctx.session, integration_id, run_id))
