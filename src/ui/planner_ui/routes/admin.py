"""Administration pages that are not plain lists of records: the reset of a password, the mapping of
external roles, the audit log and the settings."""

from __future__ import annotations

import json
from datetime import UTC, datetime, time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError

from planner_client import ApiError, models
from planner_ui import i18n
from planner_ui.crud import (
    Choice,
    FormField,
    ListView,
    Secret,
    Spec,
    guard,
    problems_from_api,
    problems_from_validation,
    secret_response,
)
from planner_ui.i18n import N_, gettext_
from planner_ui.resource_helpers import date_or_none
from planner_ui.session import CtxDep, UiContext
from planner_ui.templating import render, toast
from planner_ui.texts import field_error_text, problem_text

router = APIRouter()

AUDIT_PAGE_SIZE = 50
VIA = ("session", "api-token", "bearer", "system")
NO_SWAP = {"HX-Reswap": "none"}


def _first_message(exc: ApiError) -> str:
    """The text for the first field problem of an answer, or for its code."""
    for error in exc.errors:
        return field_error_text(str(error.get("code", "")), str(error.get("message", "")))
    return problem_text(exc.code, exc.detail)


# --- reset the password of an account --------------------------------------------------------


@router.get(
    "/users/{ident}/reset-password",
    response_class=HTMLResponse,
    dependencies=[guard("user:admin")],
)
async def reset_password_dialog(ident: str, ctx: CtxDep) -> HTMLResponse:
    user = await ctx.api.users_get(ctx.creds, ident)
    return render(ctx.request, "users/_reset_password.html", ctx, user=user, error=None)


@router.post(
    "/users/{ident}/reset-password",
    response_class=HTMLResponse,
    dependencies=[guard("user:admin")],
)
async def reset_password(ident: str, request: Request, ctx: CtxDep) -> Response:
    user = await ctx.api.users_get(ctx.creds, ident)
    form = await request.form()
    given = str(form.get("new_password") or "") or None
    try:
        result = await ctx.api.users_reset_password(
            ctx.creds, ident, body=models.ResetPasswordIn(new_password=given)
        )
    except ApiError as exc:
        if exc.status not in {409, 422}:
            raise
        return render(
            request,
            "users/_reset_password.html",
            ctx,
            status=422,
            user=user,
            error=_first_message(exc),
        )
    if result.temporary_password:
        return secret_response(
            ctx,
            Secret(
                title=gettext_("Password reset"),
                intro=gettext_(
                    "Give this temporary password to the user. It is shown only now, "
                    "and the user chooses a new one at the next sign-in."
                ),
                label=gettext_("Temporary password"),
                value=result.temporary_password,
            ),
        )
    headers = toast("success", gettext_("Password reset."), modalClose=True, refreshList=True)
    return Response(headers={**headers, **NO_SWAP})


# --- the mapping of external roles ------------------------------------------------------------


async def _mapping_dialog(
    ctx: UiContext,
    rows: list[tuple[str, str]],
    *,
    error: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    roles = await ctx.api.roles_list(ctx.creds, page_size=200)
    config = await ctx.api.role_mappings_get(ctx.creds)
    return render(
        ctx.request,
        "roles/_mappings.html",
        ctx,
        status=status,
        rows=rows,
        roles=[Choice(str(r.id), r.name) for r in roles.items],
        provider_id=config.provider_id,
        error=error,
    )


@router.get("/roles/mappings", response_class=HTMLResponse, dependencies=[guard("role:admin")])
async def mappings_dialog(ctx: CtxDep) -> HTMLResponse:
    current = await ctx.api.role_mappings_get(ctx.creds)
    return await _mapping_dialog(ctx, [(m.external_role, str(m.role_id)) for m in current.mappings])


@router.get("/roles/mappings/row", response_class=HTMLResponse, dependencies=[guard("role:admin")])
async def mapping_row(ctx: CtxDep) -> HTMLResponse:
    roles = await ctx.api.roles_list(ctx.creds, page_size=200)
    return render(
        ctx.request,
        "roles/_mapping_row.html",
        ctx,
        external_role="",
        role_id="",
        roles=[Choice(str(r.id), r.name) for r in roles.items],
    )


@router.put("/roles/mappings", response_class=HTMLResponse, dependencies=[guard("role:admin")])
async def save_mappings(request: Request, ctx: CtxDep) -> Response:
    form = await request.form()
    pairs = [
        (str(external).strip(), str(role_id))
        for external, role_id in zip(
            form.getlist("external_role"), form.getlist("role_id"), strict=False
        )
        if str(external).strip()
    ]
    try:
        try:
            body = models.MappingsIn(
                mappings=[models.MappingIn(external_role=e, role_id=r) for e, r in pairs]
            )
        except ValidationError as exc:
            raise ApiError(422, "VALIDATION_ERROR", str(exc)) from exc
        await ctx.api.role_mappings_replace(ctx.creds, body=body)
    except ApiError as exc:
        if exc.status != 422:
            raise
        return await _mapping_dialog(ctx, pairs, error=_first_message(exc), status=422)
    return Response(headers={**toast("success", gettext_("Saved."), modalClose=True), **NO_SWAP})


# --- the audit log ---------------------------------------------------------------------------


def _pretty(value: dict[str, Any] | None) -> str:
    return "" if not value else json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def _day_start(text: str | None) -> datetime | None:
    day = date_or_none(text)
    return datetime.combine(day, time.min, tzinfo=UTC) if day else None


def _day_end(text: str | None) -> datetime | None:
    day = date_or_none(text)
    return datetime.combine(day, time(23, 59, 59), tzinfo=UTC) if day else None


@router.get("/audit", response_class=HTMLResponse, dependencies=[guard("audit:read")])
async def audit(request: Request, ctx: CtxDep) -> HTMLResponse:
    params = request.query_params
    raw_page = params.get("page", "1")
    page = int(raw_page) if raw_page.isdigit() and int(raw_page) >= 1 else 1
    filters = {
        name: (params.get(name) or "").strip()
        for name in ("entity_type", "action", "via", "client_name", "since", "until")
    }
    found = await ctx.api.audit_list(
        ctx.creds,
        entity_type=filters["entity_type"] or None,
        action=filters["action"] or None,
        via=filters["via"] or None,
        client_name=filters["client_name"] or None,
        since=_day_start(filters["since"]),
        until=_day_end(filters["until"]),
        page=page,
        page_size=AUDIT_PAGE_SIZE,
    )
    rows = [
        {"entry": e, "before": _pretty(e.before), "after": _pretty(e.after)} for e in found.items
    ]
    fragment = (
        ctx.is_htmx
        and request.headers.get("hx-target") == "list"
        and "hx-history-restore-request" not in request.headers
    )
    return render(
        request,
        "audit/_list.html" if fragment else "audit/index.html",
        ctx,
        rows=rows,
        view=ListView("/audit", filters, page, AUDIT_PAGE_SIZE, found.total),
        via_options=VIA,
    )


# --- settings --------------------------------------------------------------------------------

SETTINGS_SPECS: tuple[Spec, ...] = (
    Spec(
        "default_allocation_percent",
        N_("Availability proposed for a new member (%)"),
        kind="number",
        required=True,
        minimum=10,
        maximum=100,
    ),
    Spec(
        "planning_horizon_weeks",
        N_("Weeks shown on the planning board"),
        kind="number",
        required=True,
        minimum=1,
        maximum=12,
    ),
    Spec(
        "locale",
        N_("Language of accounts without a preference"),
        kind="select",
        required=True,
        choices="locales",
    ),
)


def _settings_fields(values: dict[str, Any], errors: dict[str, str]) -> list[FormField]:
    locales = [Choice(c.code, c.name) for c in i18n.get_catalogs().locales]
    return [
        FormField(
            spec=spec,
            label=gettext_(spec.label),
            value="" if values.get(spec.name) is None else values[spec.name],
            error=errors.get(spec.name),
            choices=locales if spec.choices == "locales" else [],
        )
        for spec in SETTINGS_SPECS
    ]


def _settings_response(
    ctx: UiContext,
    current: models.SettingsOut,
    values: dict[str, Any],
    *,
    fragment: bool,
    errors: dict[str, str] | None = None,
    banner: str | None = None,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    return render(
        ctx.request,
        "settings/_form.html" if fragment else "settings/index.html",
        ctx,
        status=status,
        headers=headers,
        fields=_settings_fields(values, errors or {}),
        settings=current,
        banner=banner,
    )


def _settings_values(current: models.SettingsOut) -> dict[str, Any]:
    return {
        "default_allocation_percent": current.default_allocation_percent,
        "planning_horizon_weeks": current.planning_horizon_weeks,
        "locale": str(current.locale.value),
    }


@router.get("/settings", response_class=HTMLResponse, dependencies=[guard("settings:admin")])
async def settings_page(ctx: CtxDep) -> HTMLResponse:
    current = await ctx.api.settings_get(ctx.creds)
    return _settings_response(ctx, current, _settings_values(current), fragment=False)


@router.put("/settings", response_class=HTMLResponse, dependencies=[guard("settings:admin")])
async def settings_save(request: Request, ctx: CtxDep) -> HTMLResponse:
    current = await ctx.api.settings_get(ctx.creds)
    form = await request.form()
    values: dict[str, Any] = {
        spec.name: str(form.get(spec.name) or "").strip() for spec in SETTINGS_SPECS
    }
    try:
        body = models.SettingsIn(**values)
    except ValidationError as exc:
        return _settings_response(
            ctx,
            current,
            values,
            fragment=True,
            errors=problems_from_validation(exc).errors,
            status=422,
        )
    try:
        saved = await ctx.api.settings_update(ctx.creds, body=body)
    except ApiError as exc:
        if exc.status != 422:
            raise
        errors = problems_from_api(exc)
        return _settings_response(
            ctx,
            current,
            values,
            fragment=True,
            errors=errors,
            banner=None if errors else problem_text(exc.code, exc.detail),
            status=422,
        )
    return _settings_response(
        ctx,
        saved,
        _settings_values(saved),
        fragment=True,
        headers=toast("success", gettext_("Saved.")),
    )
