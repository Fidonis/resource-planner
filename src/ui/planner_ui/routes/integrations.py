"""Integrations: the connections to other systems, their settings, test, sync and run history.

The settings of a provider are drawn from the JSON Schema the API publishes for it (see
``schema_form``), so a provider installed later needs no change here.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError

from planner_client import ApiError, models
from planner_ui import schema_form
from planner_ui.crud import (
    BLOCKER_KINDS,
    PAGE_SIZE,
    ListView,
    guard,
    problems_from_validation,
)
from planner_ui.i18n import N_, gettext_
from planner_ui.session import CtxDep, UiContext
from planner_ui.templating import render, toast, trigger
from planner_ui.texts import field_error_text, problem_text

router = APIRouter()
NEED = guard("integration:admin")
NO_SWAP = {"HX-Reswap": "none"}
TO_DIALOG = {"HX-Retarget": "#modal-body", "HX-Reswap": "innerHTML"}

TYPE_NAMES = {
    "task_source": N_("Tasks"),
    "directory": N_("People"),
    "absence_source": N_("Absences"),
}
STATUS_NAMES = {
    "success": N_("Succeeded"),
    "partial": N_("With errors"),
    "failed": N_("Failed"),
    "aborted": N_("Aborted"),
    "running": N_("Running"),
}
# The schedules offered as a choice; any other cron line is a custom one. Times are UTC, as the
# scheduler of the API evaluates them.
SCHEDULE_PRESETS: tuple[tuple[str, str], ...] = (
    ("", N_("Only when started by hand")),
    ("*/15 * * * *", N_("Every 15 minutes")),
    ("*/30 * * * *", N_("Every 30 minutes")),
    ("0 * * * *", N_("Every hour")),
    ("0 */4 * * *", N_("Every 4 hours")),
    ("0 5 * * *", N_("Every day at 05:00 UTC")),
    ("0 5 * * 1-5", N_("Workdays at 05:00 UTC")),
)
SCHEDULE_NAMES = {value: label for value, label in SCHEDULE_PRESETS if value}
CUSTOM = "custom"
# Blockers of a deletion that name what the integration has written.
OWNED_KINDS = {"teams": N_("teams"), **BLOCKER_KINDS}


def _value(item: Any) -> str:
    return str(getattr(item, "value", item))


def _page(params: Mapping[str, str]) -> int:
    raw = str(params.get("page", "1"))
    return int(raw) if raw.isdigit() and int(raw) >= 1 else 1


def _current_query(request: Request) -> dict[str, str]:
    current = request.headers.get("hx-current-url", "")
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(current).query))


def _is_fragment(request: Request, ctx: UiContext) -> bool:
    return (
        ctx.is_htmx
        and request.headers.get("hx-target") == "list"
        and "hx-history-restore-request" not in request.headers
    )


def _field_errors(exc: ApiError) -> dict[str, str]:
    """The messages of a refused form by the name of the input (``config.baseUrl``)."""
    result: dict[str, str] = {}
    for error in exc.errors:
        name = str(error.get("field") or "")
        result.setdefault(
            name, field_error_text(str(error.get("code", "")), str(error.get("message", "")))
        )
    return result


async def _providers(ctx: UiContext) -> list[models.ProviderInfo]:
    return list((await ctx.api.integrations_providers(ctx.creds)).items)


async def _provider(ctx: UiContext, kind: str, key: str) -> models.ProviderInfo:
    for found in await _providers(ctx):
        if _value(found.type) == kind and found.key == key:
            return found
    raise ApiError(409, "PROVIDER_UNKNOWN", f"No provider '{key}' for '{kind}' is installed.")


# --- the list ---------------------------------------------------------------------------------


async def _list_response(
    ctx: UiContext,
    params: Mapping[str, str],
    *,
    fragment: bool,
    headers: Mapping[str, str] | None = None,
) -> HTMLResponse:
    page = _page(params)
    found = await ctx.api.integrations_list(ctx.creds, page=page, page_size=PAGE_SIZE)
    view = ListView("/integrations", {}, page, PAGE_SIZE, found.total)
    return render(
        ctx.request,
        "integrations/_list.html" if fragment else "integrations/index.html",
        ctx,
        headers=headers,
        rows=list(found.items),
        view=view,
        type_names=TYPE_NAMES,
        status_names=STATUS_NAMES,
        schedule_names=SCHEDULE_NAMES,
    )


@router.get("/integrations", response_class=HTMLResponse, dependencies=[NEED])
async def index(request: Request, ctx: CtxDep) -> HTMLResponse:
    return await _list_response(
        ctx, dict(request.query_params), fragment=_is_fragment(request, ctx)
    )


# --- create and change ------------------------------------------------------------------------


async def _form_response(
    ctx: UiContext,
    info: models.ProviderInfo,
    *,
    row: models.IntegrationOut | None,
    given_name: str,
    enabled: bool,
    schedule: str,
    config_shown: Mapping[str, Any],
    mapping_shown: Mapping[str, Any],
    version: str = "",
    errors: Mapping[str, str] | None = None,
    banner: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    errors = errors or {}
    kind = _value(info.type)
    config_fields = schema_form.build_fields(
        info.config_schema,
        config_shown,
        "config",
        secrets_set=row.secrets_set if row is not None else (),
        errors=errors,
    )
    mapping_fields = schema_form.build_fields(
        info.mapping_schema, mapping_shown, "mapping", errors=errors
    )
    url = f"/integrations/options/{kind}/{info.key}"
    for found in config_fields:
        if found.kind == "options":
            found.options_url = f"{url}/{found.key}" + (f"?integration={row.id}" if row else "")
            found.load_on_open = row is not None
    preset = schedule if schedule in {value for value, _ in SCHEDULE_PRESETS} else CUSTOM
    return render(
        ctx.request,
        "integrations/_form.html",
        ctx,
        status=status,
        headers=TO_DIALOG if status != 200 else None,
        info=info,
        kind=kind,
        type_name=gettext_(TYPE_NAMES[kind]),
        row=row,
        title=gettext_("Edit integration") if row is not None else gettext_("New integration"),
        action=f"/integrations/{row.id}" if row is not None else "/integrations",
        method="put" if row is not None else "post",
        version=version,
        edit_url=f"/integrations/{row.id}/edit" if row is not None else None,
        given_name=given_name,
        enabled=enabled,
        schedule_presets=SCHEDULE_PRESETS,
        schedule_preset=preset,
        schedule_custom=schedule if preset == CUSTOM else "",
        config_fields=config_fields,
        mapping_fields=mapping_fields,
        name_error=errors.get("name"),
        schedule_error=errors.get("schedule"),
        banner=banner,
        submit_label=gettext_("Save") if row is not None else gettext_("Create"),
    )


@router.post(
    "/integrations/options/{kind}/{key}/{field}",
    response_class=HTMLResponse,
    dependencies=[NEED],
)
async def load_choices(
    kind: str,
    key: str,
    field: str,
    request: Request,
    ctx: CtxDep,
    integration: str | None = None,
) -> HTMLResponse:
    """The check boxes of a setting, with the choices the source has now."""
    form = await request.form()
    info = await _provider(ctx, kind, key)
    shown = schema_form.shown_from_form(info.config_schema, form, "config")
    selected = [str(value) for value in shown.get(field) or []]
    # A setting the form cannot read yet is left out; the API says what is missing.
    config, _ = schema_form.parse(info.config_schema, shown, "config")
    items: list[tuple[str, str]] = []
    error: str | None = None
    try:
        found = await ctx.api.integrations_field_options(
            ctx.creds,
            body=models.FieldOptionsIn(
                type=kind,
                provider_key=key,
                integration_id=integration or None,
                config=config,
                field=field,
            ),
        )
        items = [(item.value, item.label) for item in found.items]
    except ValidationError:
        error = gettext_("The choices cannot be loaded for this setting.")
    except ApiError as exc:
        if exc.status not in (409, 422):
            raise
        details = _field_errors(exc)
        if details:
            error = next(iter(details.values()))
        elif exc.code == "PROVIDER_ERROR":
            # What the provider says about its source, as a connection test shows it.
            error = exc.detail
        else:
            error = problem_text(exc.code, exc.detail)
    return render(
        ctx.request,
        "integrations/_options.html",
        ctx,
        input_name=f"config.{field}",
        fid=f"f-config-{field}",
        items=items,
        selected=selected,
        loaded=error is None,
        error=error,
    )


@router.get("/integrations/new", response_class=HTMLResponse, dependencies=[NEED])
async def choose(ctx: CtxDep) -> HTMLResponse:
    groups: dict[str, list[models.ProviderInfo]] = {}
    for found in await _providers(ctx):
        groups.setdefault(_value(found.type), []).append(found)
    ordered = [
        (gettext_(TYPE_NAMES[kind]), kind, groups[kind]) for kind in TYPE_NAMES if kind in groups
    ]
    return render(ctx.request, "integrations/_choose.html", ctx, groups=ordered)


@router.get("/integrations/new/{kind}/{key}", response_class=HTMLResponse, dependencies=[NEED])
async def new_form(kind: str, key: str, ctx: CtxDep) -> HTMLResponse:
    info = await _provider(ctx, kind, key)
    return await _form_response(
        ctx,
        info,
        row=None,
        given_name="",
        enabled=False,
        schedule="",
        config_shown=schema_form.shown_values(info.config_schema, {}),
        mapping_shown=schema_form.shown_values(info.mapping_schema, {}),
    )


@router.get("/integrations/{ident}/edit", response_class=HTMLResponse, dependencies=[NEED])
async def edit_form(ident: str, ctx: CtxDep) -> HTMLResponse:
    row = await ctx.api.integrations_get(ctx.creds, ident)
    info = await _provider(ctx, _value(row.type), row.provider_key)
    return await _form_response(
        ctx,
        info,
        row=row,
        given_name=row.name,
        enabled=row.enabled,
        schedule=row.schedule or "",
        config_shown=schema_form.shown_values(info.config_schema, row.config),
        mapping_shown=schema_form.shown_values(info.mapping_schema, row.mapping),
        version=str(row.version),
    )


async def _saved(ctx: UiContext, request: Request, message: str) -> HTMLResponse:
    headers = toast("success", message, modalClose=True)
    return await _list_response(ctx, _current_query(request), fragment=True, headers=headers)


async def _submit(request: Request, ctx: UiContext, ident: str | None) -> HTMLResponse:
    form = await request.form()
    row = await ctx.api.integrations_get(ctx.creds, ident) if ident else None
    if row is not None:
        info = await _provider(ctx, _value(row.type), row.provider_key)
    else:
        info = await _provider(
            ctx, str(form.get("type") or ""), str(form.get("provider_key") or "")
        )
    name = str(form.get("name") or "").strip()
    schedule = str(form.get("schedule") or "").strip()
    preset = form.get("schedule_preset")
    if preset is not None and str(preset) != CUSTOM:
        schedule = str(preset).strip()
    enabled = "enabled" in form
    config_shown = schema_form.shown_from_form(info.config_schema, form, "config")
    mapping_shown = schema_form.shown_from_form(info.mapping_schema, form, "mapping")
    version = str(form.get("version") or "")

    async def again(
        errors: Mapping[str, str], banner: str | None = None, status: int = 422
    ) -> HTMLResponse:
        return await _form_response(
            ctx,
            info,
            row=row,
            given_name=name,
            enabled=enabled,
            schedule=schedule,
            config_shown=config_shown,
            mapping_shown=mapping_shown,
            version=version,
            errors=errors,
            banner=banner,
            status=status,
        )

    config, config_errors = schema_form.parse(info.config_schema, config_shown, "config")
    mapping, mapping_errors = schema_form.parse(info.mapping_schema, mapping_shown, "mapping")
    if config_errors or mapping_errors:
        return await again({**config_errors, **mapping_errors})
    try:
        if row is not None:
            body = models.IntegrationUpdate(
                name=name,
                enabled=enabled,
                schedule=schedule or None,
                config=config,
                mapping=mapping,
            )
            await ctx.api.integrations_update(
                ctx.creds,
                str(row.id),
                body=body,
                if_match=int(version) if version.isdigit() else None,
            )
        else:
            await ctx.api.integrations_create(
                ctx.creds,
                body=models.IntegrationIn(
                    name=name,
                    type=_value(info.type),
                    provider_key=info.key,
                    enabled=enabled,
                    schedule=schedule or None,
                    config=config,
                    mapping=mapping,
                ),
            )
    except ValidationError as exc:
        return await again(problems_from_validation(exc).errors)
    except ApiError as exc:
        if exc.status == 422 or (exc.status == 409 and exc.code == "DUPLICATE"):
            errors = _field_errors(exc)
            return await again(errors, None if errors else problem_text(exc.code, exc.detail))
        if exc.status in {409, 412, 428}:
            return await again(
                {},
                gettext_(
                    "Somebody else changed this entry in the meantime. "
                    "Load the current values to continue."
                ),
                status=412,
            )
        raise
    return await _saved(ctx, request, gettext_("Saved.") if ident else gettext_("Created."))


@router.post("/integrations", response_class=HTMLResponse, dependencies=[NEED])
async def create(request: Request, ctx: CtxDep) -> HTMLResponse:
    return await _submit(request, ctx, None)


@router.put("/integrations/{ident}", response_class=HTMLResponse, dependencies=[NEED])
async def update(ident: str, request: Request, ctx: CtxDep) -> HTMLResponse:
    return await _submit(request, ctx, ident)


# --- delete -----------------------------------------------------------------------------------


def _delete_dialog(
    ctx: UiContext, row: models.IntegrationOut, *, owned: str | None, status: int = 200
) -> HTMLResponse:
    return render(
        ctx.request,
        "integrations/_delete.html",
        ctx,
        status=status,
        headers=TO_DIALOG if status != 200 else None,
        row=row,
        owned=owned,
    )


@router.get("/integrations/{ident}/delete", response_class=HTMLResponse, dependencies=[NEED])
async def confirm_delete(ident: str, ctx: CtxDep) -> HTMLResponse:
    row = await ctx.api.integrations_get(ctx.creds, ident)
    return _delete_dialog(ctx, row, owned=None)


@router.delete("/integrations/{ident}", response_class=HTMLResponse, dependencies=[NEED])
async def delete(ident: str, request: Request, ctx: CtxDep) -> Response:
    row = await ctx.api.integrations_get(ctx.creds, ident)
    params = request.query_params
    version = params.get("version", "")
    try:
        await ctx.api.integrations_delete(
            ctx.creds,
            ident,
            detach="detach" in params,
            if_match=int(version) if version.isdigit() else None,
        )
    except ApiError as exc:
        if exc.code == "DELETE_BLOCKED":
            blockers = exc.extra.get("blockers") or []
            owned = ", ".join(
                f"{b['count']} {gettext_(OWNED_KINDS.get(b['kind'], b['kind']))}" for b in blockers
            )
            return _delete_dialog(ctx, row, owned=owned, status=412)
        raise
    return await _saved(ctx, request, gettext_("Deleted."))


# --- test and sync ----------------------------------------------------------------------------


@router.post("/integrations/{ident}/test", response_class=HTMLResponse, dependencies=[NEED])
async def test_connection(ident: str, ctx: CtxDep) -> Response:
    result = await ctx.api.integrations_test(ctx.creds, ident)
    kind = "success" if result.ok else "error"
    return Response(headers={**toast(kind, result.message), **NO_SWAP})


def _sync_dialog(
    ctx: UiContext,
    row: models.IntegrationOut,
    *,
    run: models.SyncRunDetail | None = None,
    error: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    headers = dict(TO_DIALOG) if status != 200 or run is not None else {}
    if run is not None:
        headers.update(trigger(refreshList=True))
    return render(
        ctx.request,
        "integrations/_sync.html",
        ctx,
        status=status,
        headers=headers or None,
        row=row,
        run=run,
        error=error,
        status_names=STATUS_NAMES,
    )


@router.get("/integrations/{ident}/sync", response_class=HTMLResponse, dependencies=[NEED])
async def sync_dialog(ident: str, ctx: CtxDep) -> HTMLResponse:
    row = await ctx.api.integrations_get(ctx.creds, ident)
    return _sync_dialog(ctx, row)


@router.post("/integrations/{ident}/sync", response_class=HTMLResponse, dependencies=[NEED])
async def sync_now(ident: str, request: Request, ctx: CtxDep) -> HTMLResponse:
    form = await request.form()
    row = await ctx.api.integrations_get(ctx.creds, ident)
    try:
        run = await ctx.api.integrations_sync(
            ctx.creds,
            ident,
            dry_run=form.get("mode") == "dry",
            full="full" in form,
        )
    except ApiError as exc:
        if exc.status not in {409, 422, 502}:
            raise
        return _sync_dialog(ctx, row, error=problem_text(exc.code, exc.detail), status=409)
    return _sync_dialog(ctx, row, run=run)


# --- runs -------------------------------------------------------------------------------------


@router.get("/integrations/{ident}/runs", response_class=HTMLResponse, dependencies=[NEED])
async def runs(ident: str, request: Request, ctx: CtxDep) -> HTMLResponse:
    row = await ctx.api.integrations_get(ctx.creds, ident)
    page = _page(request.query_params)
    found = await ctx.api.integrations_runs(ctx.creds, ident, page=page, page_size=PAGE_SIZE)
    view = ListView(f"/integrations/{ident}/runs", {}, page, PAGE_SIZE, found.total)
    return render(
        request,
        "integrations/_runs.html" if _is_fragment(request, ctx) else "integrations/runs.html",
        ctx,
        row=row,
        rows=list(found.items),
        view=view,
        status_names=STATUS_NAMES,
    )


@router.get("/integrations/{ident}/runs/{run_id}", response_class=HTMLResponse, dependencies=[NEED])
async def run_detail(ident: str, run_id: str, ctx: CtxDep) -> HTMLResponse:
    row = await ctx.api.integrations_get(ctx.creds, ident)
    run = await ctx.api.integrations_run(ctx.creds, ident, run_id)
    return render(
        ctx.request,
        "integrations/_run.html",
        ctx,
        row=row,
        run=run,
        status_names=STATUS_NAMES,
    )
