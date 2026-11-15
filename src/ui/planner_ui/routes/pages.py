"""Pages that are not master data: the entry page, the own account and the health check."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from planner_client import models
from planner_ui import pool_fields
from planner_ui.i18n import gettext_
from planner_ui.session import CtxDep, UiContext
from planner_ui.templating import render, toast

router = APIRouter()

# The first page a user can open, in the order the navigation lists them.
LANDING = (
    ("assignment:read", "/planning"),
    ("member:read", "/members"),
    ("team:read", "/teams"),
    ("skill:read", "/skills"),
    ("absence-type:read", "/absence-types"),
)


@router.get("/")
async def home(ctx: CtxDep) -> Response:
    for permission, path in LANDING:
        if ctx.can(permission):
            return RedirectResponse(path, status_code=303)
    return RedirectResponse("/account", status_code=303)


@router.get("/health/live", include_in_schema=False)
async def live() -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def _pool_fields_context(ctx: UiContext, me: models.Me) -> dict[str, Any]:
    default = (await ctx.api.settings_get(ctx.creds)).pool_task_fields
    own = me.pool_task_fields
    return {
        "following": own is None,
        "checked": pool_fields.ids(default if own is None else own),
        "default_labels": pool_fields.labels(default),
        "choices": pool_fields.choices(),
    }


@router.get("/account", response_class=HTMLResponse)
async def account(ctx: CtxDep) -> HTMLResponse:
    extra = await _pool_fields_context(ctx, ctx.me) if ctx.can("assignment:read") else {}
    return render(ctx.request, "account.html", ctx, permissions=sorted(ctx.permissions), **extra)


@router.put("/account/pool-fields", response_class=HTMLResponse)
async def account_pool_fields(request: Request, ctx: CtxDep) -> HTMLResponse:
    form = await request.form()
    fields = (
        [
            models.PoolTaskField(str(v))
            for v in form.getlist("fields")
            if str(v) in pool_fields.POOL_FIELD_IDS
        ]
        if form.get("mode") == "own"
        else None
    )
    me = await ctx.api.auth_update_pool_task_fields(
        ctx.creds, body=models.PoolTaskFieldsRequest(fields=fields)
    )
    return render(
        request,
        "account/_pool_fields.html",
        ctx,
        headers=toast("success", gettext_("Saved.")),
        **await _pool_fields_context(ctx, me),
    )
