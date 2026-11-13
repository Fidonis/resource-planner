"""The task and absence pages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from planner_client import ApiError, models
from planner_ui.crud import PAGE_SIZE, Choice, FieldProblemsError, FormField, Resource, Spec
from planner_ui.i18n import N_, gettext_
from planner_ui.resource_helpers import date_or_none, days, id_or_none, plain
from planner_ui.session import UiContext
from planner_ui.texts import problem_text


def status_choices() -> list[Choice]:
    return [
        Choice("open", gettext_("Open")),
        Choice("done", gettext_("Done")),
        Choice("cancelled", gettext_("Cancelled")),
    ]


class Tasks(Resource):
    slug = "tasks"
    read = "task:read"
    write = "task:write"
    templates = "tasks"
    title = N_("Tasks")
    new_title = N_("New task")
    edit_title = N_("Edit task")
    delete_title = N_("Delete task")
    filter_names = ("team", "skill", "status")
    specs: ClassVar[tuple[Spec, ...]] = (
        Spec("key", N_("Key"), required=True, maxlength=64),
        Spec("title", N_("Title"), required=True, maxlength=300),
        Spec("owner_team_id", N_("Team"), kind="select", choices="teams"),
        Spec("status", N_("Status"), kind="select", required=True, choices="statuses"),
        Spec(
            "efforts",
            N_("Effort in person-days per skill"),
            kind="efforts",
            choices="skills",
            help=N_("Leave a skill empty when the task does not need it."),
        ),
        Spec("description", N_("Description"), kind="textarea", maxlength=20000),
        Spec(
            "force",
            N_("Cut the assignments down to a lower effort"),
            kind="checkbox",
            help=N_("Otherwise an effort below the planned work is refused."),
        ),
    )

    async def load_choices(self, ctx: UiContext) -> dict[str, list[Choice]]:
        teams = await ctx.api.teams_list(ctx.creds, page_size=200)
        skills = await ctx.api.skills_list(ctx.creds, page_size=200)
        return {
            "teams": [Choice(str(t.id), t.name) for t in teams.items],
            "skills": [Choice(str(s.id), s.name) for s in skills.items],
            "statuses": status_choices(),
        }

    async def list_extras(self, ctx: UiContext) -> dict[str, Any]:
        return {"choices": await self.load_choices(ctx)}

    async def defaults(self, ctx: UiContext) -> dict[str, Any]:
        return {"status": "open", "efforts": {}}

    def values_of(self, row: Any) -> dict[str, Any]:
        values = super().values_of(row)
        values["owner_team_id"] = str(row.owner_team_id) if row.owner_team_id else None
        values["status"] = str(row.status.value)
        values["efforts"] = {str(e.skill_id): plain(e.person_days) for e in row.efforts}
        values["force"] = False
        return values

    def build_fields(
        self,
        row: Any | None,
        values: Mapping[str, Any],
        errors: Mapping[str, str],
        choices: Mapping[str, list[Choice]],
    ) -> list[FormField]:
        fields = super().build_fields(row, values, errors, choices)
        return [f for f in fields if row is not None or f.spec.name != "force"]

    def _efforts(self, values: dict[str, Any]) -> list[models.EffortIn]:
        result: list[models.EffortIn] = []
        for skill_id, text in values.pop("efforts", {}).items():
            amount = days(text)
            if amount is None or amount < 0:
                raise FieldProblemsError(
                    {"efforts": gettext_("Enter the effort as a number of person-days, e.g. 2.5.")}
                )
            if amount > 0:
                result.append(models.EffortIn(skill_id=skill_id, person_days=amount))
        return result

    async def fetch_page(
        self, ctx: UiContext, *, q: str | None, page: int, filters: Mapping[str, str]
    ) -> tuple[list[Any], int]:
        found = await ctx.api.tasks_list(
            ctx.creds,
            q=q,
            team_id=id_or_none(filters.get("team")),
            skill_id=id_or_none(filters.get("skill")),
            status=filters.get("status") or None,
            page=page,
            page_size=PAGE_SIZE,
        )
        return list(found.items), found.total

    async def fetch_one(self, ctx: UiContext, ident: str) -> Any:
        return await ctx.api.tasks_get(ctx.creds, ident)

    async def create(self, ctx: UiContext, values: dict[str, Any]) -> Any:
        values.pop("force", None)
        efforts = self._efforts(values)
        return await ctx.api.tasks_create(
            ctx.creds, body=models.TaskCreate(**values, efforts=efforts)
        )

    async def update(
        self, ctx: UiContext, ident: str, values: dict[str, Any], version: int | None
    ) -> Any:
        force = bool(values.pop("force", False))
        efforts = self._efforts(values)
        try:
            return await ctx.api.tasks_update(
                ctx.creds,
                ident,
                body=models.TaskUpdate(**values, efforts=efforts),
                force=force or None,
                if_match=version,
            )
        except ApiError as exc:
            if exc.code == "EFFORT_BELOW_ASSIGNED":
                detail = "; ".join(str(e.get("message", "")) for e in exc.errors)
                raise FieldProblemsError(
                    {"efforts": f"{problem_text(exc.code, exc.detail)} {detail}".strip()}
                ) from exc
            raise

    async def remove(self, ctx: UiContext, ident: str, version: int | None) -> None:
        await ctx.api.tasks_delete(ctx.creds, ident, if_match=version)


class Absences(Resource):
    slug = "absences"
    read = "absence:read"
    write = ("absence:write", "absence:write:own")
    templates = "absences"
    title = N_("Absences")
    new_title = N_("New absence")
    edit_title = N_("Edit absence")
    delete_title = N_("Delete absence")
    filter_names = ("member", "type", "from", "to")
    specs: ClassVar[tuple[Spec, ...]] = (
        Spec(
            "member_id",
            N_("Member"),
            kind="select",
            choices="members",
            help=N_("Leave empty for a day off for everybody, such as a public holiday."),
        ),
        Spec("absence_type_id", N_("Type"), kind="select", required=True, choices="types"),
        Spec("start_date", N_("From"), kind="date", required=True),
        Spec("start_part", N_("First day"), kind="select", required=True, choices="parts"),
        Spec("end_date", N_("To (inclusive)"), kind="date", required=True),
        Spec("end_part", N_("Last day"), kind="select", required=True, choices="parts"),
        Spec("note", N_("Note"), maxlength=1000),
    )

    async def load_choices(self, ctx: UiContext) -> dict[str, list[Choice]]:
        members = await ctx.api.members_list(ctx.creds, active=True, page_size=200)
        types = await ctx.api.absence_types_list(ctx.creds, active=True, page_size=200)
        return {
            "members": [
                Choice(str(m.id), f"{m.display_name} ({m.team_name})") for m in members.items
            ],
            "types": [Choice(str(t.id), t.name) for t in types.items],
            "parts": [
                Choice("FULL", gettext_("Whole day")),
                Choice("AM", gettext_("Morning only")),
                Choice("PM", gettext_("Afternoon only")),
            ],
        }

    async def list_extras(self, ctx: UiContext) -> dict[str, Any]:
        return {"choices": await self.load_choices(ctx)}

    async def defaults(self, ctx: UiContext) -> dict[str, Any]:
        return {"start_part": "FULL", "end_part": "FULL"}

    def values_of(self, row: Any) -> dict[str, Any]:
        values = super().values_of(row)
        values["member_id"] = str(row.member_id) if row.member_id else None
        values["absence_type_id"] = str(row.absence_type_id)
        values["start_date"] = row.start_date.isoformat()
        values["end_date"] = row.end_date.isoformat()
        values["start_part"] = str(row.start_part.value)
        values["end_part"] = str(row.end_part.value)
        return values

    async def fetch_page(
        self, ctx: UiContext, *, q: str | None, page: int, filters: Mapping[str, str]
    ) -> tuple[list[Any], int]:
        found = await ctx.api.absences_list(
            ctx.creds,
            member_id=id_or_none(filters.get("member")),
            type_id=id_or_none(filters.get("type")),
            from_=date_or_none(filters.get("from")),
            to=date_or_none(filters.get("to")),
            page=page,
            page_size=PAGE_SIZE,
            sort="-startDate",
        )
        return list(found.items), found.total

    async def fetch_one(self, ctx: UiContext, ident: str) -> Any:
        return await ctx.api.absences_get(ctx.creds, ident)

    async def create(self, ctx: UiContext, values: dict[str, Any]) -> Any:
        return await ctx.api.absences_create(ctx.creds, body=models.AbsenceIn(**values))

    async def update(
        self, ctx: UiContext, ident: str, values: dict[str, Any], version: int | None
    ) -> Any:
        return await ctx.api.absences_update(
            ctx.creds, ident, body=models.AbsenceUpdate(**values), if_match=version
        )

    async def remove(self, ctx: UiContext, ident: str, version: int | None) -> None:
        await ctx.api.absences_delete(ctx.creds, ident, if_match=version)

    def after_save(self, ctx: UiContext, saved: Any) -> list[str]:
        weeks = getattr(saved, "overbooked_member_weeks", None) or []
        if not weeks:
            return []
        return [
            gettext_("Saved. %(count)s member-weeks are overbooked now; see the planning board.")
            % {"count": len(weeks)}
        ]
