"""List, create, edit and delete pages for master data, driven by a description of the resource.

A resource says which API calls to make and which fields its form has; this module does the rest:
the list page and its HTMX fragment, the dialogs, optimistic locking (the version travels in the
form and in ``If-Match``), field errors next to the inputs, and the toasts. A dialog that fails
stays open with its input; a dialog that works closes and the list is drawn again.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from starlette.datastructures import FormData

from planner_client import ApiError
from planner_ui.i18n import N_, gettext_
from planner_ui.session import CtxDep, UiContext
from planner_ui.templating import render, toast, trigger
from planner_ui.texts import field_error_text, problem_text

Kind = Literal[
    "text",
    "email",
    "color",
    "number",
    "checkbox",
    "textarea",
    "select",
    "multiselect",
    "date",
    "efforts",
    "password",
    "roles",
]
PAGE_SIZE = 25

BLOCKER_KINDS = {
    "members": N_("members"),
    "assignments": N_("assignments"),
    "absences": N_("absences"),
    "skills": N_("skills"),
    "tasks": N_("tasks"),
    "users": N_("accounts"),
    "mappings": N_("role mappings"),
}

# Conflicts that belong to a field or to the whole form, not to a newer version of the entry.
FORM_CONFLICTS = frozenset(
    {"DUPLICATE", "FIELD_LOCKED", "LAST_ADMIN", "OWN_ACCOUNT", "BUILTIN_ROLE"}
)


@dataclass(frozen=True)
class Spec:
    """One input of a form."""

    name: str  # attribute of the API model, snake_case
    label: str  # message id
    kind: Kind = "text"
    required: bool = False
    help: str | None = None  # message id
    choices: str | None = None  # key into the resource's ``load_choices``
    minimum: int | None = None
    maximum: int | None = None
    maxlength: int | None = None
    autocomplete: str | None = None


@dataclass
class Choice:
    value: str
    label: str
    scoped: bool = False  # a role that can be limited to teams


@dataclass(frozen=True)
class Secret:
    """A value that is shown once after a change, such as a generated password or a new token."""

    title: str
    intro: str
    label: str
    value: str


@dataclass
class FormField:
    """A spec bound to a value, ready for the template."""

    spec: Spec
    label: str
    value: Any
    error: str | None = None
    readonly: bool = False
    choices: list[Choice] = field(default_factory=list)
    help: str | None = None
    all_choices: Mapping[str, list[Choice]] = field(default_factory=dict)

    def selected(self, value: str) -> bool:
        if isinstance(self.value, list | tuple | set):
            return value in {str(v) for v in self.value}
        return str(self.value) == value

    def picked(self) -> dict[str, set[str]]:
        """For a ``roles`` field: the chosen roles, each with the teams it is limited to."""
        rows = self.value if isinstance(self.value, list) else []
        return {str(r["role_id"]): {str(t) for t in r.get("team_ids", [])} for r in rows}


@dataclass
class ListView:
    """Where a list is and how to reach its other pages, keeping the filters."""

    path: str
    params: dict[str, str]
    page: int
    page_size: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))

    @property
    def first(self) -> int:
        return 0 if self.total == 0 else (self.page - 1) * self.page_size + 1

    @property
    def last(self) -> int:
        return min(self.total, self.page * self.page_size)

    def url(self, page: int | None = None) -> str:
        params = {k: v for k, v in self.params.items() if v not in ("", None)}
        if page and page > 1:
            params["page"] = str(page)
        query = urllib.parse.urlencode(params)
        return f"{self.path}?{query}" if query else self.path


class FieldProblemsError(Exception):
    """The form is not valid; one message per field."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("invalid form")
        self.errors = errors


def problems_from_validation(exc: ValidationError) -> FieldProblemsError:
    errors: dict[str, str] = {}
    for item in exc.errors():
        name = str(item["loc"][0]) if item["loc"] else ""
        errors.setdefault(name, field_error_text(item["type"], item["msg"]))
    return FieldProblemsError(errors)


def problems_from_api(exc: ApiError) -> dict[str, str]:
    """Field messages of a problem, by the model's snake_case attribute name."""
    result: dict[str, str] = {}
    for error in exc.errors:
        name = error.get("field") or ""
        key = "".join("_" + c.lower() if c.isupper() else c for c in name)
        result.setdefault(
            key, field_error_text(str(error.get("code", "")), str(error.get("message", "")))
        )
    return result


class Resource:
    """Describes one kind of master data. Subclasses fill in the API calls."""

    slug: ClassVar[str]
    read: ClassVar[str]
    write: ClassVar[str | tuple[str, ...]]  # one permission, or any of several
    templates: ClassVar[str]  # folder of the list template
    title: ClassVar[str]
    new_title: ClassVar[str]
    edit_title: ClassVar[str]
    delete_title: ClassVar[str]
    specs: ClassVar[tuple[Spec, ...]]
    filter_names: ClassVar[tuple[str, ...]] = ()
    editable: ClassVar[bool] = True
    delete_label: ClassVar[str] = N_("Delete")
    deleted_message: ClassVar[str] = N_("Deleted.")

    # --- API --------------------------------------------------------------------------------
    async def fetch_page(
        self, ctx: UiContext, *, q: str | None, page: int, filters: Mapping[str, str]
    ) -> tuple[list[Any], int]:
        raise NotImplementedError

    async def fetch_one(self, ctx: UiContext, ident: str) -> Any:
        raise NotImplementedError

    async def create(self, ctx: UiContext, values: dict[str, Any]) -> Any:
        raise NotImplementedError

    async def update(
        self, ctx: UiContext, ident: str, values: dict[str, Any], version: int | None
    ) -> Any:
        raise NotImplementedError

    async def remove(self, ctx: UiContext, ident: str, version: int | None) -> None:
        raise NotImplementedError

    # --- form -------------------------------------------------------------------------------
    async def load_choices(self, ctx: UiContext) -> dict[str, list[Choice]]:
        return {}

    async def list_extras(self, ctx: UiContext) -> dict[str, Any]:
        """Extra template data of the list page (filter options and the like)."""
        return {}

    async def defaults(self, ctx: UiContext) -> dict[str, Any]:
        return {}

    def values_of(self, row: Any) -> dict[str, Any]:
        return {spec.name: getattr(row, spec.name, None) for spec in self.specs}

    def locked(self, row: Any | None) -> set[str]:
        """Names of the fields the source system owns for this row."""
        if row is None:
            return set()
        return {_snake(name) for name in getattr(row, "managed_fields", [])}

    def after_save(self, ctx: UiContext, saved: Any) -> list[str]:
        """Warnings to show after a successful save."""
        return []

    def secret_of(self, saved: Any, *, created: bool) -> Secret | None:
        """A value to show once instead of closing the dialog."""
        return None

    # --- helpers ----------------------------------------------------------------------------
    def parse(self, form: FormData) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for spec in self.specs:
            raw = form.getlist(spec.name)
            if spec.kind == "checkbox":
                values[spec.name] = spec.name in form
            elif spec.kind == "multiselect":
                values[spec.name] = [str(v) for v in raw if str(v)]
            elif spec.kind == "password":
                # A password is taken as typed: no trimming, empty means "not given".
                values[spec.name] = (str(raw[0]) or None) if raw else None
            elif spec.kind == "roles":
                chosen = [
                    k.removeprefix("role-") for k, _ in form.multi_items() if k.startswith("role-")
                ]
                values[spec.name] = [
                    {
                        "role_id": role_id,
                        "team_ids": [str(t) for t in form.getlist(f"teams-{role_id}") if str(t)],
                    }
                    for role_id in chosen
                ]
            elif spec.kind == "efforts":
                values[spec.name] = {
                    key.removeprefix("effort-"): str(value).strip()
                    for key, value in form.multi_items()
                    if key.startswith("effort-") and str(value).strip()
                }
            else:
                text = str(raw[0]).strip() if raw else ""
                if spec.kind == "number":
                    values[spec.name] = _int_or_text(text)
                elif text == "" and not spec.required:
                    values[spec.name] = None
                else:
                    values[spec.name] = text
        return values

    def build_fields(
        self,
        row: Any | None,
        values: Mapping[str, Any],
        errors: Mapping[str, str],
        choices: Mapping[str, list[Choice]],
    ) -> list[FormField]:
        locked = self.locked(row)
        fields: list[FormField] = []
        for spec in self.specs:
            value = values.get(spec.name)
            fields.append(
                FormField(
                    spec=spec,
                    label=gettext_(spec.label),
                    value="" if value is None else value,
                    error=errors.get(spec.name),
                    readonly=spec.name in locked,
                    choices=list(choices.get(spec.choices or "", [])),
                    help=gettext_(spec.help) if spec.help else None,
                    all_choices=choices,
                )
            )
        return fields


def secret_response(ctx: UiContext, secret: Secret) -> HTMLResponse:
    """Show a value once. The list behind the dialog is drawn again by the ``refreshList`` event."""
    headers = {"HX-Retarget": "#modal-body", "HX-Reswap": "innerHTML", **trigger(refreshList=True)}
    return render(ctx.request, "crud/_secret.html", ctx, headers=headers, secret=secret)


def _snake(name: str) -> str:
    return "".join("_" + c.lower() if c.isupper() else c for c in name)


def _int_or_text(text: str) -> int | str | None:
    if text == "":
        return None
    try:
        return int(text)
    except ValueError:
        return text


def _version(form: Mapping[str, Any]) -> int | None:
    raw = form.get("version")
    return int(raw) if isinstance(raw, str) and raw.isdigit() else None


def _current_query(request: Request) -> dict[str, str]:
    """The list filters of the page the dialog was opened from (htmx sends its address)."""
    current = request.headers.get("hx-current-url", "")
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(current).query))


def guard(*permissions: str) -> Any:
    """A route dependency: the user needs any one of ``permissions``."""

    async def check(ctx: CtxDep) -> UiContext:
        if not any(ctx.can(permission) for permission in permissions):
            raise ApiError(
                403, "PERMISSION_DENIED", f"The permission '{permissions[0]}' is required."
            )
        return ctx

    return Depends(check)


def install(router: APIRouter, resource: Resource) -> None:
    """Add the routes of ``resource`` to ``router``."""
    base = f"/{resource.slug}"
    writes = (resource.write,) if isinstance(resource.write, str) else resource.write
    reader, writer = guard(resource.read), guard(*writes)

    async def list_view(ctx: UiContext, params: Mapping[str, str]) -> tuple[list[Any], ListView]:
        raw_page = str(params.get("page", "1"))
        page = int(raw_page) if raw_page.isdigit() and int(raw_page) >= 1 else 1
        q = (params.get("q") or "").strip() or None
        filters = {name: params.get(name, "") for name in resource.filter_names}
        rows, total = await resource.fetch_page(ctx, q=q, page=page, filters=filters)
        view = ListView(base, {"q": q or "", **filters}, page, PAGE_SIZE, total)
        return rows, view

    async def list_response(
        ctx: UiContext,
        params: Mapping[str, str],
        *,
        fragment: bool,
        headers: Mapping[str, str] | None = None,
    ) -> HTMLResponse:
        rows, view = await list_view(ctx, params)
        extras = await resource.list_extras(ctx)
        name = (
            f"{resource.templates}/_list.html" if fragment else f"{resource.templates}/index.html"
        )
        return render(ctx.request, name, ctx, headers=headers, rows=rows, view=view, **extras)

    @router.get(base, response_class=HTMLResponse, dependencies=[reader])
    async def index(request: Request, ctx: CtxDep) -> HTMLResponse:
        fragment = (
            ctx.is_htmx
            and request.headers.get("hx-target") == "list"
            and ("hx-history-restore-request" not in request.headers)
        )
        return await list_response(ctx, dict(request.query_params), fragment=fragment)

    async def form_response(
        ctx: UiContext,
        *,
        row: Any | None,
        values: Mapping[str, Any],
        errors: Mapping[str, str] | None = None,
        banner: str | None = None,
        status: int = 200,
    ) -> HTMLResponse:
        choices = await resource.load_choices(ctx)
        ident = str(row.id) if row is not None else None
        headers: dict[str, str] = {}
        if status != 200:
            headers = {"HX-Retarget": "#modal-body", "HX-Reswap": "innerHTML"}
        return render(
            ctx.request,
            "crud/_form.html",
            ctx,
            status=status,
            headers=headers,
            resource=resource,
            fields=resource.build_fields(row, values, errors or {}, choices),
            title=gettext_(resource.edit_title if row is not None else resource.new_title),
            action=f"{base}/{ident}" if ident else base,
            method="put" if ident else "post",
            version=getattr(row, "version", "") if row is not None else "",
            edit_url=f"{base}/{ident}/edit" if ident else None,
            banner=banner,
            submit_label=gettext_("Save") if ident else gettext_("Create"),
        )

    @router.get(f"{base}/new", response_class=HTMLResponse, dependencies=[writer])
    async def new_form(ctx: CtxDep) -> HTMLResponse:
        return await form_response(ctx, row=None, values=await resource.defaults(ctx))

    if resource.editable:

        @router.get(f"{base}/{{ident}}/edit", response_class=HTMLResponse, dependencies=[writer])
        async def edit_form(ident: str, ctx: CtxDep) -> HTMLResponse:
            row = await resource.fetch_one(ctx, ident)
            return await form_response(ctx, row=row, values=resource.values_of(row))

    async def saved(
        ctx: UiContext, request: Request, message: str, warnings: list[str]
    ) -> HTMLResponse:
        headers = toast("success", message, modalClose=True)
        if warnings:
            events = {
                "toast": {"kind": "warning", "message": " ".join(warnings)},
                "modalClose": True,
            }
            headers = trigger(**events)
        return await list_response(ctx, _current_query(request), fragment=True, headers=headers)

    async def submit(request: Request, ctx: UiContext, ident: str | None) -> HTMLResponse:
        form = await request.form()
        values = resource.parse(form)
        version = _version(form)
        row = await resource.fetch_one(ctx, ident) if ident else None
        try:
            try:
                result = (
                    await resource.update(ctx, ident, values, version)
                    if ident
                    else await resource.create(ctx, values)
                )
            except ValidationError as exc:
                raise problems_from_validation(exc) from exc
        except FieldProblemsError as problems:
            return await form_response(
                ctx, row=row, values=values, errors=problems.errors, status=422
            )
        except ApiError as exc:
            if exc.status == 422 or (exc.status == 409 and exc.code in FORM_CONFLICTS):
                errors = problems_from_api(exc)
                banner = None if errors else problem_text(exc.code, exc.detail)
                return await form_response(
                    ctx, row=row, values=values, errors=errors, banner=banner, status=422
                )
            if exc.status in {409, 412, 428}:
                return await form_response(
                    ctx,
                    row=row,
                    values=values,
                    banner=gettext_(
                        "Somebody else changed this entry in the meantime. "
                        "Load the current values to continue."
                    ),
                    status=412,
                )
            raise
        secret = resource.secret_of(result, created=ident is None)
        if secret is not None:
            return secret_response(ctx, secret)
        message = gettext_("Saved.") if ident else gettext_("Created.")
        return await saved(ctx, request, message, resource.after_save(ctx, result))

    @router.post(base, response_class=HTMLResponse, dependencies=[writer])
    async def create(request: Request, ctx: CtxDep) -> HTMLResponse:
        return await submit(request, ctx, None)

    if resource.editable:

        @router.put(f"{base}/{{ident}}", response_class=HTMLResponse, dependencies=[writer])
        async def update(ident: str, request: Request, ctx: CtxDep) -> HTMLResponse:
            return await submit(request, ctx, ident)

    @router.get(f"{base}/{{ident}}/delete", response_class=HTMLResponse, dependencies=[writer])
    async def confirm_delete(ident: str, ctx: CtxDep) -> HTMLResponse:
        row = await resource.fetch_one(ctx, ident)
        return render(
            ctx.request,
            "crud/_confirm_delete.html",
            ctx,
            resource=resource,
            row=row,
            title=gettext_(resource.delete_title),
            action=f"{base}/{ident}",
            error=None,
            label=gettext_(resource.delete_label),
        )

    @router.delete(f"{base}/{{ident}}", response_class=HTMLResponse, dependencies=[writer])
    async def delete(ident: str, request: Request, ctx: CtxDep) -> Response:
        # htmx sends the fields of a DELETE in the address, not in a body.
        row = await resource.fetch_one(ctx, ident)
        try:
            await resource.remove(ctx, ident, _version(request.query_params))
        except ApiError as exc:
            if exc.status not in {409, 412, 428}:
                raise
            blockers = exc.extra.get("blockers") or []
            if exc.code == "DELETE_BLOCKED" and blockers:
                parts = [
                    f"{b['count']} {gettext_(BLOCKER_KINDS.get(b['kind'], b['kind']))}"
                    for b in blockers
                ]
                error = gettext_(
                    "It is still used by: %(items)s. Deactivate it instead, "
                    "or remove what refers to it first."
                ) % {"items": ", ".join(parts)}
            else:
                error = problem_text(exc.code, exc.detail)
            return render(
                request,
                "crud/_confirm_delete.html",
                ctx,
                status=412,
                headers={"HX-Retarget": "#modal-body", "HX-Reswap": "innerHTML"},
                resource=resource,
                row=row,
                title=gettext_(resource.delete_title),
                action=f"{base}/{ident}",
                error=error,
                label=gettext_(resource.delete_label),
            )
        return await saved(ctx, request, gettext_(resource.deleted_message), [])
