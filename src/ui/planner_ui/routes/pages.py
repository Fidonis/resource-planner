"""Pages that are not master data: the entry page and the health check."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from planner_ui.session import CtxDep
from planner_ui.templating import render

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


@router.get("/account", response_class=HTMLResponse)
async def account(ctx: CtxDep) -> HTMLResponse:
    return render(ctx.request, "account.html", ctx, permissions=sorted(ctx.permissions))
