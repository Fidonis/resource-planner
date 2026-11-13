"""Manual bulk import from CSV: tasks (FR-TASK-05) and absences (FR-ABS-06).

Rows with errors are reported with their line number and skipped; the others are applied in one
transaction. ``dryRun`` runs everything and rolls it back.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select

from planner_api import csvio
from planner_api.auth.permissions import ABSENCE_WRITE, ABSENCE_WRITE_OWN, TASK_WRITE
from planner_api.errors import ProblemError, forbidden
from planner_api.models import AbsenceType, Member, Skill, Task, TaskEffort, Team
from planner_api.repositories import work as repo
from planner_api.schemas.planning import AbsenceIn, ImportLineError, ImportResult
from planner_api.services import absences as absences_service
from planner_api.services import tasks as tasks_service
from planner_api.services.common import snapshot
from planner_api.services.context import Ctx

SKILL_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/+#-]{0,63}$")
TASK_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,63}$")
STATUSES = ("open", "done", "cancelled")
PARTS = ("FULL", "AM", "PM")


class RowError(Exception):
    def __init__(self, field_name: str | None, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.field = field_name
        self.message = message
        self.code = code


@dataclass
class Tally:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[ImportLineError] = field(default_factory=list)
    unknown_skills: list[str] = field(default_factory=list)
    created_skills: list[str] = field(default_factory=list)

    def fail(self, line: int, error: RowError) -> None:
        self.errors.append(
            ImportLineError(line=line, field=error.field, message=error.message, code=error.code)
        )

    def result(self, ctx: Ctx) -> ImportResult:
        return ImportResult(
            dry_run=ctx.dry_run,
            created=self.created,
            updated=self.updated,
            skipped=self.skipped,
            errors=self.errors,
            unknown_skills=self.unknown_skills,
            created_skills=self.created_skills,
        )


# --- tasks -------------------------------------------------------------------------------


@dataclass
class TaskRow:
    line: int
    key: str
    title: str
    team: str
    efforts: list[tuple[str, Decimal]] | None  # None: the cell was empty, leave efforts alone
    status: str | None


def _parse_task_row(line: int, cells: list[str]) -> TaskRow:
    key = csvio.cell(cells, 0)
    title = csvio.cell(cells, 1)
    if not TASK_KEY.match(key):
        raise RowError("Key", "The key is empty or has characters that are not allowed.")
    if not title or len(title) > 300:
        raise RowError("Title", "The title is empty or longer than 300 characters.")
    efforts: list[tuple[str, Decimal]] | None = None
    raw = csvio.cell(cells, 3)
    if raw:
        efforts = []
        seen: set[str] = set()
        for part in raw.split("|"):
            name, sep, days = part.partition("=")
            name = name.strip()
            if not sep or not name:
                raise RowError("Efforts", f"'{part.strip()}' is not in the form Skill=PD.")
            try:
                value = csvio.parse_decimal(days)
            except csvio.CellError as exc:
                raise RowError("Efforts", f"{name}: {exc}") from exc
            if value <= 0 or value > Decimal("9999.99"):
                raise RowError("Efforts", f"{name}: the person-days must be above 0.")
            if name.lower() in seen:
                raise RowError("Efforts", f"{name} appears twice.", "duplicate")
            seen.add(name.lower())
            efforts.append((name, value))
    status = csvio.cell(cells, 4).lower() or None
    if status is not None and status not in STATUSES:
        raise RowError("Status", "The status is open, done or cancelled.")
    return TaskRow(line, key, title, csvio.cell(cells, 2), efforts, status)


async def import_tasks(ctx: Ctx, raw: bytes, *, create_skills: bool, force: bool) -> ImportResult:
    ctx.principal.require(TASK_WRITE)
    table = csvio.drop_header(csvio.read_table(csvio.decode(raw)), "key")
    tally = Tally()
    parsed: list[TaskRow] = []
    for line, cells in table:
        try:
            parsed.append(_parse_task_row(line, cells))
        except RowError as exc:
            tally.fail(line, exc)

    teams = {
        name.lower(): team_id
        for team_id, name in (await ctx.session.execute(select(Team.id, Team.name))).all()
    }
    skills: dict[str, Skill] = {}
    for skill in (await ctx.session.scalars(select(Skill))).all():
        skills[skill.key.lower()] = skill
    for skill in skills.copy().values():
        skills.setdefault(skill.name.lower(), skill)

    unknown: dict[str, str] = {}  # lower-cased -> as written first
    for row in parsed:
        for name, _ in row.efforts or []:
            if name.lower() not in skills:
                unknown.setdefault(name.lower(), name)
    if create_skills:
        for lowered, name in sorted(unknown.items()):
            if not SKILL_KEY.match(name):
                continue
            skill = Skill(key=name, name=name)
            ctx.session.add(skill)
            await ctx.uow.flush()
            ctx.audit(
                "create",
                "skill",
                skill.id,
                after=snapshot(skill, ("key", "name")),
                note="created by a task import",
            )
            skills[lowered] = skill
            tally.created_skills.append(name)
    missing = {k: v for k, v in unknown.items() if k not in skills}
    tally.unknown_skills = sorted(missing.values(), key=str.lower)

    for row in parsed:
        try:
            await _apply_task_row(ctx, row, teams, skills, tally, force=force)
        except RowError as exc:
            tally.fail(row.line, exc)
        except ProblemError as exc:
            tally.fail(row.line, RowError(_problem_field(exc), exc.detail, exc.code.lower()))
    await ctx.uow.finish()
    return tally.result(ctx)


def _problem_field(exc: ProblemError) -> str | None:
    return exc.errors[0].get("field") if exc.errors else None


async def _apply_task_row(
    ctx: Ctx,
    row: TaskRow,
    teams: dict[str, uuid.UUID],
    skills: dict[str, Skill],
    tally: Tally,
    *,
    force: bool,
) -> None:
    owner: uuid.UUID | None = None
    if row.team:
        owner = teams.get(row.team.lower())
        if owner is None:
            raise RowError("Team", f"The team '{row.team}' does not exist.", "unknown_team")
    wanted: dict[uuid.UUID, Decimal] | None = None
    if row.efforts is not None:
        wanted = {}
        for name, days in row.efforts:
            skill = skills.get(name.lower())
            if skill is None:
                raise RowError("Efforts", f"The skill '{name}' does not exist.", "unknown_skill")
            wanted[skill.id] = days

    async with ctx.session.begin_nested():
        existing = await repo.find_task(ctx.session, row.key)
        if existing is None:
            task = Task(
                key=row.key,
                title=row.title,
                owner_team_id=owner,
                status=row.status or "open",
                locked_fields=[],
                efforts=[TaskEffort(skill_id=s, person_days=d) for s, d in (wanted or {}).items()],
            )
            ctx.session.add(task)
            await ctx.session.flush()
            ctx.audit(
                "create", "task", task.id, after=tasks_service.audit_after(task), note="csv import"
            )
            tally.created += 1
            return
        before = tasks_service.audit_after(existing)
        changed = False
        if existing.title != row.title:
            existing.title = row.title
            changed = True
        if owner is not None and existing.owner_team_id != owner:
            existing.owner_team_id = owner
            changed = True
        if row.status is not None and existing.status != row.status:
            existing.status = row.status
            changed = True
        note = "csv import"
        if wanted is not None and wanted != tasks_service.effort_map(existing):
            removed = await tasks_service.apply_efforts(ctx, existing, wanted, force=force)
            note += f"; {len(removed)} assignment(s) reduced" if removed else ""
            changed = True
        if not changed:
            tally.skipped += 1
            return
        await ctx.session.flush()
        ctx.audit(
            "update", "task", existing.id, before, tasks_service.audit_after(existing), note=note
        )
        tally.updated += 1


# --- absences ----------------------------------------------------------------------------


async def import_absences(ctx: Ctx, raw: bytes) -> ImportResult:
    if not (ctx.principal.has(ABSENCE_WRITE) or ctx.principal.has(ABSENCE_WRITE_OWN)):
        raise forbidden(ABSENCE_WRITE)
    table = csvio.drop_header(csvio.read_table(csvio.decode(raw)), "name")
    tally = Tally()
    members = await repo.members_by_name(ctx.session)
    types = await repo.absence_types_by_key(ctx.session)
    for line, cells in table:
        try:
            await _apply_absence_row(ctx, cells, members, types, tally)
        except RowError as exc:
            tally.fail(line, exc)
        except ProblemError as exc:
            tally.fail(line, RowError(_problem_field(exc), exc.detail, exc.code.lower()))
    await ctx.uow.finish()
    return tally.result(ctx)


async def _apply_absence_row(
    ctx: Ctx,
    cells: list[str],
    members: dict[str, list[Member]],
    types: dict[str, AbsenceType],
    tally: Tally,
) -> None:
    name = csvio.cell(cells, 0)
    if not name:
        raise RowError("Name", "The name is empty. Use * for all members.")
    member_id: uuid.UUID | None = None
    if name != "*":
        found = members.get(name.lower(), [])
        distinct = {m.id: m for m in found}
        if not distinct:
            raise RowError("Name", f"No member '{name}'.", "unknown_member")
        if len(distinct) > 1:
            raise RowError("Name", f"More than one member is called '{name}'.", "ambiguous_member")
        member_id = next(iter(distinct))
    try:
        start = csvio.parse_date(csvio.cell(cells, 1))
    except csvio.CellError as exc:
        raise RowError("From", str(exc)) from exc
    try:
        end = csvio.parse_date(csvio.cell(cells, 2))
    except csvio.CellError as exc:
        raise RowError("To", str(exc)) from exc
    type_name = csvio.cell(cells, 3)
    absence_type = types.get(type_name.lower())
    if absence_type is None:
        raise RowError("Type", f"No absence type '{type_name}'.", "unknown_type")
    start_part = (csvio.cell(cells, 5) or "FULL").upper()
    end_part = (csvio.cell(cells, 6) or (start_part if start == end else "FULL")).upper()
    if start_part not in PARTS or end_part not in PARTS:
        raise RowError("StartPart", "A part is FULL, AM or PM.")
    body = AbsenceIn(
        member_id=member_id,
        absence_type_id=absence_type.id,
        start_date=start,
        end_date=end,
        start_part=start_part,
        end_part=end_part,
        note=csvio.cell(cells, 4) or None,
    )
    async with ctx.session.begin_nested():
        absences_service.validate_span(body)
        same = await repo.find_identical_absence(
            ctx.session,
            member_id=member_id,
            type_id=absence_type.id,
            start=start,
            end=end,
            start_part=start_part,
            end_part=end_part,
        )
        if same is not None:
            tally.skipped += 1
            return
        row = await absences_service.create_row(ctx, body)
        await ctx.session.flush()
        ctx.audit(
            "create",
            "absence",
            row.id,
            after=snapshot(row, absences_service.AUDITED),
            note="csv import",
        )
        tally.created += 1
