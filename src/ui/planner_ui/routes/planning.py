"""The planning board, its dialogs, the overview and the absence calendar."""

from __future__ import annotations

import math
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from planner_client import ApiError, models
from planner_ui.i18n import gettext_
from planner_ui.resource_helpers import date_or_none, days, id_or_none
from planner_ui.session import CtxDep, UiContext
from planner_ui.templating import render, toast
from planner_ui.texts import problem_text

router = APIRouter()

WEEK_CHOICES = (1, 2, 4, 6, 8, 12, 26)
CONFLICT = {409, 412, 428}


def requires(*permissions: str) -> Any:
    async def check(ctx: CtxDep) -> UiContext:
        if not any(ctx.can(p) for p in permissions):
            raise ApiError(
                403, "PERMISSION_DENIED", f"The permission '{permissions[0]}' is required."
            )
        return ctx

    return Depends(check)


# --- the horizon ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Horizon:
    start: date | None
    weeks: int | None

    def params(self, **extra: str | None) -> dict[str, str]:
        found: dict[str, str] = {}
        if self.start:
            found["from"] = self.start.isoformat()
        if self.weeks:
            found["weeks"] = str(self.weeks)
        found.update({k: v for k, v in extra.items() if v})
        return found


def horizon_of(values: Mapping[str, Any]) -> Horizon:
    raw = str(values.get("weeks") or "")
    weeks = int(raw) if raw.isdigit() and 1 <= int(raw) <= 26 else None
    return Horizon(date_or_none(str(values.get("from") or "")), weeks)


def url(path: str, params: Mapping[str, str]) -> str:
    query = urllib.parse.urlencode(params)
    return f"{path}?{query}" if query else path


@dataclass(frozen=True)
class Links:
    previous: str
    following: str
    today: str


def links(path: str, start: date, count: int, extra: Mapping[str, str]) -> Links:
    def at(first: date) -> str:
        return url(path, {"from": first.isoformat(), "weeks": str(count), **extra})

    return Links(
        at(start - timedelta(weeks=count)),
        at(start + timedelta(weeks=count)),
        url(path, {"weeks": str(count), **extra}),
    )


def week_choices(current: int) -> list[int]:
    return sorted({*WEEK_CHOICES, current})


# --- overview ------------------------------------------------------------------------------


@router.get("/overview", response_class=HTMLResponse, dependencies=[requires("assignment:read")])
async def overview(request: Request, ctx: CtxDep) -> HTMLResponse:
    horizon = horizon_of(request.query_params)
    data = await ctx.api.overview_get(ctx.creds, from_=horizon.start, weeks=horizon.weeks)
    count = len(data.weeks)
    return render(
        request,
        "overview.html",
        ctx,
        data=data,
        count=count,
        week_choices=week_choices(count),
        nav=links("/overview", data.start, count, {}),
        first=data.weeks[0],
        last=data.weeks[-1],
    )


@router.get("/overview/export.csv", dependencies=[requires("assignment:read")])
async def overview_export(request: Request, ctx: CtxDep) -> Response:
    horizon = horizon_of(request.query_params)
    table = "teams" if request.query_params.get("table") == "teams" else "skills"
    text = await ctx.api.overview_export_csv(
        ctx.creds, table=table, from_=horizon.start, weeks=horizon.weeks
    )
    return Response(
        text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="overview-{table}.csv"'},
    )


# --- absence calendar ----------------------------------------------------------------------


@router.get(
    "/absences/calendar", response_class=HTMLResponse, dependencies=[requires("absence:read")]
)
async def absence_calendar(request: Request, ctx: CtxDep) -> HTMLResponse:
    horizon = horizon_of(request.query_params)
    team = id_or_none(request.query_params.get("team"))
    data = await ctx.api.absences_calendar(
        ctx.creds, team_id=team, from_=horizon.start, weeks=horizon.weeks
    )
    teams = await ctx.api.teams_list(ctx.creds, page_size=200)
    count = len(data.weeks)
    return render(
        request,
        "absences/calendar.html",
        ctx,
        data=data,
        count=count,
        week_choices=week_choices(count),
        nav=links("/absences/calendar", data.start, count, {"team": team or ""}),
        team=team or "",
        teams=teams.items,
    )


# --- the board -----------------------------------------------------------------------------


async def board_response(
    ctx: UiContext,
    values: Mapping[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
    fragment: bool = False,
) -> HTMLResponse:
    horizon = horizon_of(values)
    team = id_or_none(str(values.get("team") or ""))
    skill = id_or_none(str(values.get("skill") or ""))
    plan = await ctx.api.planning_get(
        ctx.creds, from_=horizon.start, weeks=horizon.weeks, team_id=team, skill_id=skill
    )
    teams = await ctx.api.teams_list(ctx.creds, page_size=200)
    skills = await ctx.api.skills_list(ctx.creds, page_size=200)
    count = len(plan.weeks)
    filters = {"team": team or "", "skill": skill or ""}
    carry = {"from": plan.start.isoformat(), "weeks": str(count)}
    carry.update({k: v for k, v in filters.items() if v})
    # In a dialog the board's skill filter must not clash with the skill of the assignment.
    dialog_carry = {("filter_skill" if k == "skill" else k): v for k, v in carry.items()}

    def assign_url(**extra: str) -> str:
        return url("/planning/assign", {**dialog_carry, **extra})

    def edit_url(ident: str) -> str:
        return url(f"/planning/assignments/{ident}/edit", dialog_carry)

    return render(
        ctx.request,
        "planning/_board.html" if fragment else "planning/index.html",
        ctx,
        headers=headers,
        plan=plan,
        count=count,
        week_choices=week_choices(count),
        nav=links("/planning", plan.start, count, {k: v for k, v in filters.items() if v}),
        carry_query=urllib.parse.urlencode({"from": plan.start.isoformat(), "weeks": str(count)}),
        filters=filters,
        skill_keys={str(s.id): s.key for s in plan.skills},
        team_choices=teams.items,
        skill_choices=skills.items,
        assign_url=assign_url,
        edit_url=edit_url,
        can_write=ctx.can("assignment:write"),
    )


@router.get("/planning", response_class=HTMLResponse, dependencies=[requires("assignment:read")])
async def board(request: Request, ctx: CtxDep) -> HTMLResponse:
    fragment = (
        ctx.is_htmx
        and request.headers.get("hx-target") == "board"
        and "hx-history-restore-request" not in request.headers
    )
    return await board_response(ctx, request.query_params, fragment=fragment)


@router.get(
    "/planning/export.csv", dependencies=[requires("assignment:read")], include_in_schema=False
)
async def board_export(request: Request, ctx: CtxDep) -> Response:
    horizon = horizon_of(request.query_params)
    text = await ctx.api.assignments_export_csv(ctx.creds, from_=horizon.start, weeks=horizon.weeks)
    return Response(
        text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="assignments.csv"'},
    )


# --- the dialogs ---------------------------------------------------------------------------


def suggest_days(open_days: float, free_days: float) -> float:
    """Default amount for an assignment: what is open, up to the free half days (at least 1)."""
    half_days = math.floor(max(free_days, 0.0) * 2) / 2
    amount = min(open_days, half_days)
    if amount <= 0:
        amount = min(open_days, 1.0)
    if amount <= 0:
        amount = 0.5
    return round(amount, 2)


def _figure(figures: Any, week_start: date) -> Any | None:
    return next((w for w in figures if w.week_start == week_start), None)


async def _work_for_member(
    ctx: UiContext, tasks: list[Any], member_id: str, preferred_skill: str
) -> tuple[Any, str] | None:
    """The first open work in a skill the member has; the board's skill filter comes first."""
    try:
        member = await ctx.api.members_get(ctx.creds, member_id)
    except ApiError:
        return None
    skills = {str(s) for s in member.skill_ids}
    options = [
        (task, str(effort.skill_id))
        for task in tasks
        for effort in task.efforts
        if effort.open > 0 and str(effort.skill_id) in skills
    ]
    options.sort(key=lambda option: option[1] != preferred_skill)
    return options[0] if options else None


async def assign_dialog(
    ctx: UiContext,
    values: Mapping[str, Any],
    *,
    banner: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    horizon = horizon_of(values)
    pool = await ctx.api.planning_get(
        ctx.creds, from_=horizon.start, weeks=horizon.weeks, with_members=False
    )
    wanted_task = str(values.get("task") or "")
    tasks = list(pool.tasks)
    if wanted_task and wanted_task not in {str(t.id) for t in tasks}:
        extra = await ctx.api.planning_get(
            ctx.creds,
            from_=horizon.start,
            weeks=horizon.weeks,
            with_members=False,
            task_id=[wanted_task],
        )
        tasks = [*extra.tasks, *tasks]
    headers = {"HX-Retarget": "#modal-body", "HX-Reswap": "innerHTML"} if status != 200 else None
    if not tasks:
        return render(
            ctx.request,
            "planning/_assign.html",
            ctx,
            status=status,
            headers=headers,
            empty=True,
            banner=banner,
        )
    task = next((t for t in tasks if str(t.id) == wanted_task), None)
    wanted_skill = str(values.get("skill") or "")
    if task is None and values.get("member"):
        found = await _work_for_member(
            ctx, tasks, str(values["member"]), str(values.get("filter_skill") or "")
        )
        if found is not None:
            task, wanted_skill = found
    task = task or tasks[0]
    effort = next(
        (e for e in task.efforts if str(e.skill_id) == wanted_skill),
        next((e for e in task.efforts if e.open > 0), task.efforts[0] if task.efforts else None),
    )
    members: list[Any] = []
    member_id = ""
    week_start = pool.weeks[0].week_start
    amount = 1.0
    free = None
    if effort is not None:
        availability = await ctx.api.availability_get(
            ctx.creds,
            skill_id=str(effort.skill_id),
            from_=pool.start,
            weeks=len(pool.weeks),
        )
        members = list(availability.members)
        wanted_member = str(values.get("member") or "")
        chosen = next(
            (m for m in members if str(m.member_id) == wanted_member),
            members[0] if members else None,
        )
        if chosen is not None:
            member_id = str(chosen.member_id)
            asked = date_or_none(str(values.get("week") or ""))
            known = {w.week_start for w in pool.weeks}
            if asked is not None and asked in known:
                week_start = asked
            else:
                week_start = next(
                    (w.week_start for w in chosen.weeks if w.free > 0), pool.weeks[0].week_start
                )
            free = _figure(chosen.weeks, week_start)
    member_rows = []
    for m in members:
        figure = _figure(m.weeks, week_start)
        member_rows.append({"member": m, "free": float(figure.free) if figure else 0.0})
    typed = days(str(values.get("days") or "")) if values.get("days") else None
    amount = (
        typed
        if typed is not None and typed > 0
        else suggest_days(float(effort.open) if effort else 1.0, float(free.free) if free else 0.0)
    )
    would_overbook = free is not None and amount > float(free.free)
    return render(
        ctx.request,
        "planning/_assign.html",
        ctx,
        status=status,
        headers=headers,
        empty=False,
        banner=banner,
        tasks=tasks,
        task=task,
        effort=effort,
        members=members,
        member_rows=member_rows,
        member_id=member_id,
        weeks=pool.weeks,
        week_start=week_start,
        free=free,
        amount=amount,
        would_overbook=would_overbook,
        carry=_context(values),
    )


def _context(values: Mapping[str, Any]) -> dict[str, str]:
    """What the board needs to draw itself again after a change."""
    found = {
        key: str(values.get(key) or "") for key in ("from", "weeks", "team") if values.get(key)
    }
    if values.get("filter_skill"):
        found["skill"] = str(values["filter_skill"])
    return found


@router.get(
    "/planning/assign", response_class=HTMLResponse, dependencies=[requires("assignment:write")]
)
async def assign_form(request: Request, ctx: CtxDep) -> HTMLResponse:
    return await assign_dialog(ctx, request.query_params)


def _assignment_toast(result: Any) -> dict[str, str]:
    notes: list[str] = []
    if result.overbooked:
        notes.append(
            gettext_("%(name)s is overbooked in week %(week)s.")
            % {"name": result.member_name, "week": result.week_start.isocalendar().week}
        )
    if result.overplanned:
        notes.append(gettext_("More is planned for this skill than the task needs."))
    if notes:
        return toast("warning", " ".join([gettext_("Saved."), *notes]), modalClose=True)
    return toast("success", gettext_("Saved."), modalClose=True)


@router.post(
    "/planning/assignments",
    response_class=HTMLResponse,
    dependencies=[requires("assignment:write")],
)
async def assignment_create(request: Request, ctx: CtxDep) -> HTMLResponse:
    form = dict((await request.form()).items())
    amount = days(str(form.get("days") or ""))
    week = date_or_none(str(form.get("week") or ""))
    if amount is None or amount <= 0 or week is None:
        return await assign_dialog(
            ctx,
            form,
            banner=gettext_("Enter the person-days as a number above 0, for example 1.5."),
            status=422,
        )
    try:
        result = await ctx.api.assignments_create(
            ctx.creds,
            body=models.AssignmentIn(
                task_id=str(form.get("task")),
                skill_id=str(form.get("skill")),
                member_id=str(form.get("member")),
                week_start=week,
                person_days=amount,
            ),
        )
    except ApiError as exc:
        if exc.status in CONFLICT:
            return await _reloaded(ctx, form)
        if exc.status == 422:
            return await assign_dialog(
                ctx, form, banner=problem_text(exc.code, exc.detail), status=422
            )
        raise
    return await board_response(
        ctx, _context(form), headers=_assignment_toast(result), fragment=True
    )


async def _reloaded(ctx: UiContext, values: Mapping[str, Any]) -> HTMLResponse:
    """The plan changed under the user: show the current state and say so."""
    message = gettext_("The plan was changed by somebody else. The board shows the current state.")
    return await board_response(
        ctx, _context(values), headers=toast("warning", message, modalClose=True), fragment=True
    )


async def _refused(ctx: UiContext, values: Mapping[str, Any], exc: ApiError) -> HTMLResponse:
    """A drop the API refuses: the board is drawn as it was, and the reason is told."""
    return await board_response(
        ctx,
        _context(values),
        headers=toast("error", problem_text(exc.code, exc.detail)),
        fragment=True,
    )


@router.post(
    "/planning/drops",
    response_class=HTMLResponse,
    dependencies=[requires("assignment:write")],
)
async def assignment_drop(request: Request, ctx: CtxDep) -> HTMLResponse:
    """A skill of a task was dropped on a member's week: book what is open, up to the free days."""
    form = dict((await request.form()).items())
    week = date_or_none(str(form.get("week") or ""))
    task_id, skill_id, member_id = (str(form.get(k) or "") for k in ("task", "skill", "member"))
    if week is None or not (task_id and skill_id and member_id):
        return await _reloaded(ctx, form)
    horizon = horizon_of(form)
    try:
        pool = await ctx.api.planning_get(
            ctx.creds,
            from_=horizon.start,
            weeks=horizon.weeks,
            with_members=False,
            task_id=[task_id],
        )
        availability = await ctx.api.availability_get(
            ctx.creds, skill_id=skill_id, from_=pool.start, weeks=len(pool.weeks)
        )
        task = next((t for t in pool.tasks if str(t.id) == task_id), None)
        effort = (
            next((e for e in task.efforts if str(e.skill_id) == skill_id), None) if task else None
        )
        if effort is None:
            return await _reloaded(ctx, form)
        member = next((m for m in availability.members if str(m.member_id) == member_id), None)
        figure = _figure(member.weeks, week) if member else None
        amount = suggest_days(float(effort.open), float(figure.free) if figure else 0.0)
        result = await ctx.api.assignments_create(
            ctx.creds,
            body=models.AssignmentIn(
                task_id=task_id,
                skill_id=skill_id,
                member_id=member_id,
                week_start=week,
                person_days=amount,
            ),
        )
    except ApiError as exc:
        if exc.status in CONFLICT or exc.status == 404:
            return await _reloaded(ctx, form)
        if exc.status == 422:
            return await _refused(ctx, form, exc)
        raise
    return await board_response(
        ctx, _context(form), headers=_assignment_toast(result), fragment=True
    )


@router.put(
    "/planning/assignments/{ident}/move",
    response_class=HTMLResponse,
    dependencies=[requires("assignment:write")],
)
async def assignment_move(ident: str, request: Request, ctx: CtxDep) -> HTMLResponse:
    """A chip was dropped on another member or week: the amount stays, the place changes."""
    form = dict((await request.form()).items())
    week = date_or_none(str(form.get("week") or ""))
    member_id = str(form.get("member") or "")
    version = form.get("version")
    if week is None or not member_id:
        return await _reloaded(ctx, form)
    try:
        assignment = await ctx.api.assignments_get(ctx.creds, ident)
        result = await ctx.api.assignments_update(
            ctx.creds,
            ident,
            body=models.AssignmentUpdate(
                member_id=member_id, week_start=week, person_days=float(assignment.person_days)
            ),
            if_match=int(version) if isinstance(version, str) and version.isdigit() else None,
        )
    except ApiError as exc:
        if exc.status in CONFLICT or exc.status == 404:
            return await _reloaded(ctx, form)
        if exc.status == 422:
            return await _refused(ctx, form, exc)
        raise
    return await board_response(
        ctx, _context(form), headers=_assignment_toast(result), fragment=True
    )


async def edit_dialog(
    ctx: UiContext,
    assignment: Any,
    values: Mapping[str, Any],
    *,
    banner: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    horizon = horizon_of(values)
    pool = await ctx.api.planning_get(
        ctx.creds, from_=horizon.start, weeks=horizon.weeks, with_members=False, with_tasks=False
    )
    availability = await ctx.api.availability_get(
        ctx.creds,
        skill_id=str(assignment.skill_id),
        from_=pool.start,
        weeks=len(pool.weeks),
    )
    members = list(availability.members)
    wanted_member = str(values.get("member") or assignment.member_id)
    week_start = date_or_none(str(values.get("week") or "")) or assignment.week_start
    weeks = [w.week_start for w in pool.weeks]
    if week_start not in weeks:
        weeks = [week_start, *weeks]
    chosen = next((m for m in members if str(m.member_id) == wanted_member), None)
    free = _figure(chosen.weeks, week_start) if chosen else None
    typed = days(str(values.get("days") or "")) if values.get("days") else None
    amount = typed if typed is not None and typed > 0 else float(assignment.person_days)
    # The current member stays selectable even when the skill list no longer offers them.
    names = {str(m.member_id): m.display_name for m in members}
    names.setdefault(str(assignment.member_id), assignment.member_name)
    headers = {"HX-Retarget": "#modal-body", "HX-Reswap": "innerHTML"} if status != 200 else None
    return render(
        ctx.request,
        "planning/_edit.html",
        ctx,
        status=status,
        headers=headers,
        assignment=assignment,
        member_names=names,
        member_id=wanted_member,
        weeks=weeks,
        week_start=week_start,
        free=free,
        amount=amount,
        would_overbook=free is not None
        and wanted_member != str(assignment.member_id)
        and amount > float(free.free),
        banner=banner,
        carry=_context(values),
    )


@router.get(
    "/planning/assignments/{ident}/edit",
    response_class=HTMLResponse,
    dependencies=[requires("assignment:write")],
)
async def assignment_edit(ident: str, request: Request, ctx: CtxDep) -> HTMLResponse:
    assignment = await ctx.api.assignments_get(ctx.creds, ident)
    return await edit_dialog(ctx, assignment, request.query_params)


@router.put(
    "/planning/assignments/{ident}",
    response_class=HTMLResponse,
    dependencies=[requires("assignment:write")],
)
async def assignment_update(ident: str, request: Request, ctx: CtxDep) -> HTMLResponse:
    form = dict((await request.form()).items())
    assignment = await ctx.api.assignments_get(ctx.creds, ident)
    amount = days(str(form.get("days") or ""))
    week = date_or_none(str(form.get("week") or ""))
    if amount is None or amount <= 0 or week is None:
        return await edit_dialog(
            ctx,
            assignment,
            form,
            banner=gettext_("Enter the person-days as a number above 0, for example 1.5."),
            status=422,
        )
    version = form.get("version")
    try:
        result = await ctx.api.assignments_update(
            ctx.creds,
            ident,
            body=models.AssignmentUpdate(
                member_id=str(form.get("member")), week_start=week, person_days=amount
            ),
            if_match=int(version) if isinstance(version, str) and version.isdigit() else None,
        )
    except ApiError as exc:
        if exc.status in CONFLICT:
            return await _reloaded(ctx, form)
        if exc.status == 422:
            return await edit_dialog(
                ctx, assignment, form, banner=problem_text(exc.code, exc.detail), status=422
            )
        raise
    return await board_response(
        ctx, _context(form), headers=_assignment_toast(result), fragment=True
    )


@router.delete(
    "/planning/assignments/{ident}",
    response_class=HTMLResponse,
    dependencies=[requires("assignment:write")],
)
async def assignment_delete(ident: str, request: Request, ctx: CtxDep) -> HTMLResponse:
    # htmx sends the fields of a DELETE in the address.
    values = request.query_params
    version = values.get("version")
    try:
        await ctx.api.assignments_delete(
            ctx.creds,
            ident,
            if_match=int(version) if version and version.isdigit() else None,
        )
    except ApiError as exc:
        if exc.status in CONFLICT:
            return await _reloaded(ctx, values)
        raise
    return await board_response(
        ctx,
        _context(values),
        headers=toast("success", gettext_("Removed."), modalClose=True),
        fragment=True,
    )
