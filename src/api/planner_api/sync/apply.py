"""Writing normalised records into the planner: one code path for sync runs and the ingest API.

Records are matched by ``(source, external_id)``, so running the same data twice changes nothing
(FR-INT-01). Every record is written in its own savepoint, so one bad record is reported and the
others still go in (FR-INT-06). Nothing here removes an assignment (FR-INT-05).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import selectinload

from planner_api.errors import ProblemError
from planner_api.models import (
    Absence,
    AbsenceType,
    AppUser,
    Assignment,
    Member,
    MemberSkill,
    Skill,
    Task,
    TaskEffort,
    Team,
)
from planner_api.models.base import touch
from planner_api.repositories import settings as settings_repo
from planner_api.schemas.planning import AbsenceIn
from planner_api.services import absences as absences_service
from planner_api.services import tasks as tasks_service
from planner_api.services.common import snapshot
from planner_api.services.context import Ctx, map_db_error, utcnow
from planner_api.sync.mapping import AbsenceRecord, MemberRecord, TaskRecord

MAX_LOG = 500
SKILL_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/+#-]{0,63}$")
STATUSES = ("open", "done", "cancelled")


class RecordError(Exception):
    """This record cannot be written; the run goes on with the next one."""


@dataclass(frozen=True, slots=True)
class LogEntry:
    level: str  # info | warning | error
    message: str
    reference: str | None = None


@dataclass
class Tally:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    log: list[LogEntry] = field(default_factory=list)
    truncated: bool = False

    def note(self, level: str, message: str, reference: str | None = None) -> None:
        if level == "error":
            self.errors += 1
        if len(self.log) >= MAX_LOG:
            self.truncated = True
            return
        self.log.append(LogEntry(level, message, reference))

    def error(self, reference: str, message: str) -> None:
        self.note("error", message, reference)

    def warn(self, reference: str, message: str) -> None:
        self.note("warning", message, reference)


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _text(value: str | None, limit: int, name: str) -> str | None:
    cleaned = (value or "").strip() or None
    if cleaned is not None and len(cleaned) > limit:
        raise RecordError(f"{name} is longer than {limit} characters.")
    return cleaned


class Applier:
    def __init__(self, ctx: Ctx, source: str, note: str) -> None:
        self.ctx = ctx
        self.source = source
        self.note = note
        self.tally = Tally()
        self._skills: dict[str, Skill] = {}
        self._teams: dict[str, Team] = {}
        self._default_allocation = 80

    # --- lookups ---------------------------------------------------------------------------

    async def load(self) -> None:
        """Read skills and teams once; the mapping asks :meth:`skill_key` while it runs."""
        session = self.ctx.session
        self._skills = {s.key.lower(): s for s in (await session.scalars(select(Skill))).all()}
        self._teams = {t.name.lower(): t for t in (await session.scalars(select(Team))).all()}

    def skill_key(self, label: str) -> str | None:
        skill = self._skills.get(label.strip().lower())
        return skill.key if skill else None

    def _team_id(self, name: str | None) -> uuid.UUID | None:
        team = self._teams.get(name.strip().lower()) if name else None
        return team.id if team else None

    async def _create_skill(self, key: str) -> Skill:
        if not SKILL_KEY.match(key):
            raise RecordError(f"'{key}' cannot be a skill key.")
        skill = Skill(key=key, name=key)
        self.ctx.session.add(skill)
        await self.ctx.uow.flush()
        self.ctx.audit(
            "create",
            "skill",
            skill.id,
            after=snapshot(skill, ("key", "name")),
            note=f"{self.note}: created for an effort",
        )
        self._skills[key.lower()] = skill
        return skill

    # --- tasks -----------------------------------------------------------------------------

    async def tasks(self, records: Iterable[TaskRecord]) -> set[str]:
        """Write the tasks. Returns the external ids that were seen, valid or not."""
        seen: set[str] = set()
        for record in records:
            seen.add(record.external_id)
            try:
                async with self.ctx.session.begin_nested():
                    await self._task(record)
            except RecordError as exc:
                self.tally.error(record.key, str(exc))
            except ProblemError as exc:
                self.tally.error(record.key, exc.detail)
            except DBAPIError as exc:
                self.tally.error(record.key, map_db_error(exc).detail)
        return seen

    async def _task(self, rec: TaskRecord) -> None:
        ctx = self.ctx
        key = _text(rec.key, 64, "The key")
        title = _text(rec.title, 300, "The title")
        if not key or not title:
            raise RecordError("The key or the title is empty.")
        if rec.status not in STATUSES:
            raise RecordError(f"The status '{rec.status}' is not open, done or cancelled.")
        url = _text(rec.url, 1000, "The URL")
        for note in rec.notes:
            self.tally.warn(rec.key, note)
        description = (rec.description or "").strip() or None
        updated = _aware(rec.updated_at)
        for name in rec.create_skills:
            if name.lower() not in self._skills:
                await self._create_skill(name)
        wanted: dict[uuid.UUID, Decimal] | None = None
        if rec.efforts is not None:
            wanted = {}
            for skill_key, days in rec.efforts.items():
                skill = self._skills.get(skill_key.lower())
                if skill is None:
                    raise RecordError(f"The skill '{skill_key}' does not exist.")
                if days <= 0 or days > Decimal("9999.99"):
                    raise RecordError(f"The effort for '{skill_key}' must be above 0.")
                wanted[skill.id] = days
        owner = self._team_id(rec.owner_team)

        stmt = (
            select(Task)
            .where(Task.external_source == self.source, Task.external_id == rec.external_id)
            .options(selectinload(Task.efforts))
        )
        task = (await ctx.session.scalars(stmt)).one_or_none()
        if task is None:
            task = Task(
                key=key,
                title=title,
                description=description,
                owner_team_id=owner,
                status=rec.status,
                external_source=self.source,
                external_id=rec.external_id,
                external_url=url,
                external_updated_at=updated,
                locked_fields=list(rec.owned),
                efforts=[TaskEffort(skill_id=s, person_days=d) for s, d in (wanted or {}).items()],
            )
            ctx.session.add(task)
            await ctx.uow.flush()
            ctx.audit(
                "create", "task", task.id, after=tasks_service.audit_after(task), note=self.note
            )
            self.tally.created += 1
            return

        before = tasks_service.audit_after(task)
        owned = set(task.locked_fields or [])
        changed = False
        for attr, value in (
            ("key", key),
            ("description", description),
            ("external_url", url),
            ("external_updated_at", updated),
        ):
            if getattr(task, attr) != value:
                setattr(task, attr, value)
                changed = True
        # What the source owns is overwritten; what an administrator unlocked is left alone.
        for attr, value in (("title", title), ("status", rec.status)):
            if attr in owned and getattr(task, attr) != value:
                setattr(task, attr, value)
                changed = True
        if owner is not None and task.owner_team_id != owner:
            task.owner_team_id = owner
            changed = True
        if wanted is not None and "efforts" in owned and wanted != tasks_service.effort_map(task):
            try:
                await tasks_service.apply_efforts(ctx, task, wanted, force=False)
                changed = True
            except ProblemError as exc:
                # More is booked than the source now asks for: keep the bookings (FR-INT-05).
                self.tally.error(key, f"The effort was not changed: {exc.detail}")
        if not changed:
            self.tally.skipped += 1
            return
        await ctx.uow.flush()
        ctx.audit(
            "update", "task", task.id, before, tasks_service.audit_after(task), note=self.note
        )
        self.tally.updated += 1

    async def close_missing_tasks(self, seen: set[str]) -> None:
        stmt = (
            select(Task)
            .where(Task.external_source == self.source, Task.status == "open")
            .options(selectinload(Task.efforts))
        )
        for task in (await self.ctx.session.scalars(stmt)).all():
            if task.external_id in seen or "status" not in (task.locked_fields or []):
                continue
            before = tasks_service.audit_after(task)
            task.status = "cancelled"
            await self.ctx.uow.flush()
            self.ctx.audit(
                "update",
                "task",
                task.id,
                before,
                tasks_service.audit_after(task),
                note=f"{self.note}: no longer in the source",
            )
            self.tally.updated += 1
            self.tally.warn(task.key, "No longer in the source: the task was cancelled.")

    # --- members ---------------------------------------------------------------------------

    async def members(self, records: Iterable[MemberRecord]) -> set[str]:
        seen: set[str] = set()
        defaults = await settings_repo.load_settings(self.ctx.session)
        self._default_allocation = int(defaults["default_allocation_percent"])
        for record in records:
            seen.add(record.external_id)
            try:
                async with self.ctx.session.begin_nested():
                    await self._member(record)
            except RecordError as exc:
                self.tally.error(record.display_name, str(exc))
            except ProblemError as exc:
                self.tally.error(record.display_name, exc.detail)
            except DBAPIError as exc:
                self.tally.error(record.display_name, map_db_error(exc).detail)
        return seen

    async def _team_for(self, rec: MemberRecord) -> uuid.UUID | None:
        if not rec.team:
            return None
        found = self._team_id(rec.team)
        if found is not None:
            return found
        if not rec.create_team:
            raise RecordError(f"The team '{rec.team}' does not exist.")
        team = Team(
            name=rec.team,
            external_source=self.source,
            external_id=f"group:{rec.team.lower()}"[:255],
        )
        self.ctx.session.add(team)
        await self.ctx.uow.flush()
        self.ctx.audit(
            "create", "team", team.id, after=snapshot(team, ("name", "color")), note=self.note
        )
        self._teams[rec.team.lower()] = team
        return team.id

    async def _member(self, rec: MemberRecord) -> None:
        ctx = self.ctx
        name = _text(rec.display_name, 160, "The name")
        email = _text(rec.email, 255, "The e-mail address")
        if not name:
            raise RecordError("The name is empty.")
        team_id = await self._team_for(rec)
        stmt = (
            select(Member)
            .where(Member.external_source == self.source, Member.external_id == rec.external_id)
            .options(selectinload(Member.skill_links))
        )
        member = (await ctx.session.scalars(stmt)).one_or_none()
        adopted = False
        if member is None and email:
            stmt = (
                select(Member)
                .where(func.lower(Member.email) == email.lower(), Member.external_source.is_(None))
                .options(selectinload(Member.skill_links))
            )
            member = (await ctx.session.scalars(stmt)).first()
            adopted = member is not None
        skills = await self._skills_of(rec)

        if member is None:
            if team_id is None:
                raise RecordError(
                    "There is no team for this person: list a group of theirs in the mapping or "
                    "name a default team."
                )
            values = {
                "display_name": name,
                "email": email,
                "active": rec.active,
                "team_id": str(team_id),
            }
            member = Member(
                team_id=team_id,
                display_name=name,
                email=email,
                allocation_percent=rec.allocation_percent or self._default_allocation,
                active=rec.active,
                external_source=self.source,
                external_id=rec.external_id,
                external_snapshot=values,
                skill_links=[MemberSkill(skill_id=s.id) for s in skills],
            )
            ctx.session.add(member)
            await ctx.uow.flush()
            ctx.audit("create", "member", member.id, after=_member_audit(member), note=self.note)
            self.tally.created += 1
            return

        before = _member_audit(member)
        known = dict(member.external_snapshot or {})
        changed = False
        if adopted:
            member.external_source = self.source
            member.external_id = rec.external_id
            changed = True
        # A person adopted from the manual data keeps their team unless a group names one.
        team_value = str(team_id) if team_id and not (adopted and rec.team_is_default) else None
        for attr, value in (
            ("display_name", name),
            ("email", email),
            ("active", rec.active),
            ("team_id", team_value),
        ):
            if attr == "team_id" and value is None:
                continue
            if self._sync_field(member, attr, value, known, first=adopted):
                changed = True
        if known != (member.external_snapshot or {}):
            member.external_snapshot = known
            changed = True
        added = [s for s in skills if s.id not in member.skill_ids]
        if added:
            member.skill_links.extend(MemberSkill(skill_id=s.id) for s in added)
            touch(member)
            changed = True
        if not changed:
            self.tally.skipped += 1
            return
        await ctx.uow.flush()
        ctx.audit("update", "member", member.id, before, _member_audit(member), note=self.note)
        self.tally.updated += 1
        if before["active"] and not member.active:
            await self._warn_future_assignments(member)

    def _sync_field(
        self, member: Member, attr: str, new: Any, known: dict[str, Any], *, first: bool
    ) -> bool:
        """Write ``new`` unless somebody changed the field here since the last sync."""
        current = getattr(member, attr)
        current_cmp = str(current) if attr == "team_id" else current
        if current_cmp == new:
            known[attr] = new
            return False
        if first or attr not in known or known[attr] == current_cmp:
            setattr(member, attr, uuid.UUID(new) if attr == "team_id" else new)
            known[attr] = new
            return True
        return False  # a local change wins

    async def _skills_of(self, rec: MemberRecord) -> list[Skill]:
        found: list[Skill] = []
        for key in rec.skills:
            skill = self._skills.get(key.lower())
            if skill is None:
                self.tally.warn(rec.display_name, f"The skill '{key}' does not exist.")
            else:
                found.append(skill)
        return found

    async def _warn_future_assignments(self, member: Member) -> None:
        today = utcnow().date()
        monday = today - timedelta(days=today.weekday())
        count = (
            await self.ctx.session.execute(
                select(func.count())
                .select_from(Assignment)
                .where(Assignment.member_id == member.id, Assignment.week_start >= monday)
            )
        ).scalar_one()
        if count:
            self.tally.warn(
                member.display_name,
                f"Deactivated, but {count} assignment(s) from this week on remain: review them.",
            )

    async def close_missing_members(self, seen: set[str]) -> None:
        stmt = (
            select(Member)
            .where(Member.external_source == self.source, Member.active.is_(True))
            .options(selectinload(Member.skill_links))
        )
        for member in (await self.ctx.session.scalars(stmt)).all():
            if member.external_id in seen:
                continue
            before = _member_audit(member)
            known = dict(member.external_snapshot or {})
            if self._sync_field(member, "active", False, known, first=False):
                member.external_snapshot = known
                await self.ctx.uow.flush()
                self.ctx.audit(
                    "update",
                    "member",
                    member.id,
                    before,
                    _member_audit(member),
                    note=f"{self.note}: no longer in the directory",
                )
                self.tally.updated += 1
                await self._warn_future_assignments(member)

    # --- absences --------------------------------------------------------------------------

    async def absences(self, records: Iterable[AbsenceRecord]) -> set[str]:
        session = self.ctx.session
        members = (await session.scalars(select(Member))).all()
        usernames = dict(
            (await session.execute(select(AppUser.id, AppUser.username))).tuples().all()
        )
        lookup: dict[str, dict[uuid.UUID, Member]] = {}
        for m in members:
            for ref in (
                m.external_id,
                m.email,
                m.display_name,
                usernames.get(m.user_id) if m.user_id else None,
            ):
                if ref:
                    lookup.setdefault(ref.strip().lower(), {})[m.id] = m
        types = {t.key.lower(): t for t in (await session.scalars(select(AbsenceType))).all()}
        seen: set[str] = set()
        for record in records:
            seen.add(record.external_id)
            try:
                async with session.begin_nested():
                    await self._absence(record, lookup, types)
            except RecordError as exc:
                self.tally.error(record.person_ref, str(exc))
            except ProblemError as exc:
                self.tally.error(record.person_ref, exc.detail)
            except DBAPIError as exc:
                self.tally.error(record.person_ref, map_db_error(exc).detail)
        return seen

    async def _absence(
        self,
        rec: AbsenceRecord,
        lookup: dict[str, dict[uuid.UUID, Member]],
        types: dict[str, AbsenceType],
    ) -> None:
        ctx = self.ctx
        member_id: uuid.UUID | None = None
        if rec.person_ref.strip() != "*":
            found = lookup.get(rec.person_ref.strip().lower(), {})
            if not found:
                raise RecordError(f"No member '{rec.person_ref}'.")
            if len(found) > 1:
                raise RecordError(f"More than one member matches '{rec.person_ref}'.")
            member_id = next(iter(found))
        absence_type = types.get(rec.type_key.strip().lower())
        if absence_type is None:
            raise RecordError(f"No absence type '{rec.type_key}'.")
        if not absence_type.active:
            raise RecordError(f"The absence type '{absence_type.key}' is inactive.")
        body = AbsenceIn(
            member_id=member_id,
            absence_type_id=absence_type.id,
            start_date=rec.start,
            end_date=rec.end,
            start_part=rec.start_part,
            end_part=rec.end_part,
            note=rec.note,
        )
        absences_service.validate_span(body)

        stmt = select(Absence).where(
            Absence.external_source == self.source, Absence.external_id == rec.external_id
        )
        row = (await ctx.session.scalars(stmt)).one_or_none()
        values = {
            "member_id": member_id,
            "absence_type_id": absence_type.id,
            "start_date": rec.start,
            "end_date": rec.end,
            "start_part": rec.start_part,
            "end_part": rec.end_part,
            "note": (rec.note or "").strip() or None,
        }
        if row is None:
            row = Absence(external_source=self.source, external_id=rec.external_id, **values)
            ctx.session.add(row)
            await ctx.uow.flush()
            ctx.audit(
                "create",
                "absence",
                row.id,
                after=snapshot(row, absences_service.AUDITED),
                note=self.note,
            )
            self.tally.created += 1
            return
        before = snapshot(row, absences_service.AUDITED)
        changed = False
        for attr, value in values.items():
            if getattr(row, attr) != value:
                setattr(row, attr, value)
                changed = True
        if not changed:
            self.tally.skipped += 1
            return
        await ctx.uow.flush()
        ctx.audit(
            "update",
            "absence",
            row.id,
            before,
            snapshot(row, absences_service.AUDITED),
            note=self.note,
        )
        self.tally.updated += 1

    async def remove_missing_absences(self, seen: set[str], start: date, end: date) -> None:
        """Only absences of this source inside the window that was read (§9.6)."""
        stmt = select(Absence).where(
            Absence.external_source == self.source,
            Absence.end_date >= start,
            Absence.start_date <= end,
        )
        for row in (await self.ctx.session.scalars(stmt)).all():
            if row.external_id in seen:
                continue
            before = snapshot(row, absences_service.AUDITED)
            await self.ctx.session.delete(row)
            self.ctx.audit(
                "delete", "absence", row.id, before, note=f"{self.note}: no longer in the source"
            )
            self.tally.updated += 1


def _member_audit(member: Member) -> dict[str, Any]:
    return {
        "display_name": member.display_name,
        "email": member.email,
        "team_id": str(member.team_id),
        "active": member.active,
        "skill_ids": sorted(str(s) for s in member.skill_ids),
    }
