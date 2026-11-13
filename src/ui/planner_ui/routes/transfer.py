"""CSV export and import of tasks and absences, as downloads and as a dialog."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import UploadFile

from planner_client import ApiError, models
from planner_ui.i18n import N_, gettext_
from planner_ui.session import CtxDep, UiContext
from planner_ui.templating import render
from planner_ui.texts import problem_text

router = APIRouter()

MAX_UPLOAD = 5 * 1024 * 1024  # as the API


@dataclass(frozen=True)
class Kind:
    slug: str
    title: str  # message id
    read: str
    write: tuple[str, ...]
    offers_skills: bool
    download: Callable[[UiContext], Awaitable[str]]
    upload: Callable[[UiContext, str, bool, bool], Awaitable[models.ImportResult]]


async def _tasks_download(ctx: UiContext) -> str:
    return await ctx.api.tasks_export_csv(ctx.creds)


async def _tasks_upload(
    ctx: UiContext, text: str, dry_run: bool, create_skills: bool
) -> models.ImportResult:
    return await ctx.api.tasks_import_csv(
        ctx.creds, csv=text, dry_run=dry_run or None, create_skills=create_skills or None
    )


async def _absences_download(ctx: UiContext) -> str:
    return await ctx.api.absences_export_csv(ctx.creds)


async def _absences_upload(
    ctx: UiContext, text: str, dry_run: bool, create_skills: bool
) -> models.ImportResult:
    return await ctx.api.absences_import_csv(ctx.creds, csv=text, dry_run=dry_run or None)


KINDS = (
    Kind(
        "tasks",
        N_("Import tasks"),
        "task:read",
        ("task:write",),
        True,
        _tasks_download,
        _tasks_upload,
    ),
    Kind(
        "absences",
        N_("Import absences"),
        "absence:read",
        ("absence:write", "absence:write:own"),
        False,
        _absences_download,
        _absences_upload,
    ),
)


def _allowed(ctx: UiContext, permissions: tuple[str, ...]) -> None:
    if not any(ctx.can(p) for p in permissions):
        raise ApiError(403, "PERMISSION_DENIED", f"The permission '{permissions[0]}' is required.")


def _install(kind: Kind) -> None:
    base = f"/{kind.slug}"

    @router.get(f"{base}/export.csv", name=f"{kind.slug}_export")
    async def export(ctx: CtxDep) -> Response:
        _allowed(ctx, (kind.read,))
        text = await kind.download(ctx)
        return Response(
            text.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{kind.slug}.csv"'},
        )

    def dialog(ctx: UiContext) -> HTMLResponse:
        return render(ctx.request, "crud/_import.html", ctx, kind=kind, title=gettext_(kind.title))

    def outcome(
        ctx: UiContext, *, result: models.ImportResult | None = None, error: str | None = None
    ) -> HTMLResponse:
        return render(
            ctx.request, "crud/_import_result.html", ctx, kind=kind, result=result, error=error
        )

    @router.get(f"{base}/import", response_class=HTMLResponse, name=f"{kind.slug}_import_form")
    async def import_form(ctx: CtxDep) -> HTMLResponse:
        _allowed(ctx, kind.write)
        return dialog(ctx)

    @router.post(f"{base}/import", response_class=HTMLResponse, name=f"{kind.slug}_import")
    async def import_file(request: Request, ctx: CtxDep) -> HTMLResponse:
        _allowed(ctx, kind.write)
        form = await request.form()
        upload: Any = form.get("file")
        if not isinstance(upload, UploadFile) or not upload.filename:
            return outcome(ctx, error=gettext_("Choose a CSV file first."))
        raw = await upload.read(MAX_UPLOAD + 1)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return outcome(ctx, error=gettext_("The file is not UTF-8 text. Save it as UTF-8."))
        check = form.get("mode") != "import"
        try:
            result = await kind.upload(ctx, text, check, "create_skills" in form)
        except ApiError as exc:
            if exc.status in {413, 422}:
                return outcome(ctx, error=problem_text(exc.code, exc.detail))
            raise
        return outcome(ctx, result=result)


for _kind in KINDS:
    _install(_kind)
