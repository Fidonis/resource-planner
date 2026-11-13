"""The pages that list and edit records: master data, tasks and absences."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from planner_client import models
from planner_ui import work_resources
from planner_ui.crud import PAGE_SIZE, Choice, Resource, Spec
from planner_ui.i18n import N_, gettext_
from planner_ui.resource_helpers import flag as _flag
from planner_ui.resource_helpers import uuid_or_none as _uuid
from planner_ui.session import UiContext

DEFAULT_TEAM_COLOR = "#1b5e8c"
DEFAULT_TYPE_COLOR = "#2a78d6"


class Teams(Resource):
    slug = "teams"
    read = "team:read"
    write = "team:write"
    templates = "teams"
    title = N_("Teams")
    new_title = N_("New team")
    edit_title = N_("Edit team")
    delete_title = N_("Delete team")
    specs: ClassVar[tuple[Spec, ...]] = (
        Spec("name", N_("Name"), required=True, maxlength=120),
        Spec("color", N_("Colour"), kind="color", required=True),
    )

    async def fetch_page(
        self, ctx: UiContext, *, q: str | None, page: int, filters: Mapping[str, str]
    ) -> tuple[list[Any], int]:
        found = await ctx.api.teams_list(ctx.creds, q=q, page=page, page_size=PAGE_SIZE)
        return list(found.items), found.total

    async def fetch_one(self, ctx: UiContext, ident: str) -> Any:
        return await ctx.api.teams_get(ctx.creds, ident)

    async def defaults(self, ctx: UiContext) -> dict[str, Any]:
        return {"color": DEFAULT_TEAM_COLOR}

    async def create(self, ctx: UiContext, values: dict[str, Any]) -> Any:
        return await ctx.api.teams_create(ctx.creds, body=models.TeamIn(**values))

    async def update(
        self, ctx: UiContext, ident: str, values: dict[str, Any], version: int | None
    ) -> Any:
        return await ctx.api.teams_update(
            ctx.creds, ident, body=models.TeamUpdate(**values), if_match=version
        )

    async def remove(self, ctx: UiContext, ident: str, version: int | None) -> None:
        await ctx.api.teams_delete(ctx.creds, ident, if_match=version)


class Skills(Resource):
    slug = "skills"
    read = "skill:read"
    write = "skill:write"
    templates = "skills"
    title = N_("Skills")
    new_title = N_("New skill")
    edit_title = N_("Edit skill")
    delete_title = N_("Delete skill")
    specs: ClassVar[tuple[Spec, ...]] = (
        Spec(
            "key",
            N_("Key"),
            required=True,
            maxlength=64,
            help=N_("Matches the label in the task source, regardless of capitalisation."),
        ),
        Spec("name", N_("Name"), required=True, maxlength=120),
    )

    async def fetch_page(
        self, ctx: UiContext, *, q: str | None, page: int, filters: Mapping[str, str]
    ) -> tuple[list[Any], int]:
        found = await ctx.api.skills_list(ctx.creds, q=q, page=page, page_size=PAGE_SIZE)
        return list(found.items), found.total

    async def fetch_one(self, ctx: UiContext, ident: str) -> Any:
        return await ctx.api.skills_get(ctx.creds, ident)

    async def create(self, ctx: UiContext, values: dict[str, Any]) -> Any:
        return await ctx.api.skills_create(ctx.creds, body=models.SkillIn(**values))

    async def update(
        self, ctx: UiContext, ident: str, values: dict[str, Any], version: int | None
    ) -> Any:
        return await ctx.api.skills_update(
            ctx.creds, ident, body=models.SkillUpdate(**values), if_match=version
        )

    async def remove(self, ctx: UiContext, ident: str, version: int | None) -> None:
        await ctx.api.skills_delete(ctx.creds, ident, if_match=version)


class Members(Resource):
    slug = "members"
    read = "member:read"
    write = "member:write"
    templates = "members"
    title = N_("Members")
    new_title = N_("New member")
    edit_title = N_("Edit member")
    delete_title = N_("Delete member")
    filter_names = ("team", "skill", "active")
    specs: ClassVar[tuple[Spec, ...]] = (
        Spec("display_name", N_("Name"), required=True, maxlength=120),
        Spec("email", N_("E-mail"), kind="email", maxlength=255, autocomplete="off"),
        Spec("team_id", N_("Team"), kind="select", required=True, choices="teams"),
        Spec(
            "allocation_percent",
            N_("Availability for planned work (%)"),
            kind="number",
            required=True,
            minimum=10,
            maximum=100,
            help=N_(
                "The share of working time that can be planned; the rest is left for other work."
            ),
        ),
        Spec("skill_ids", N_("Skills"), kind="multiselect", choices="skills"),
        Spec("active", N_("Active"), kind="checkbox"),
        Spec("note", N_("Note"), kind="textarea", maxlength=2000),
    )

    async def load_choices(self, ctx: UiContext) -> dict[str, list[Choice]]:
        teams = await ctx.api.teams_list(ctx.creds, page_size=200)
        skills = await ctx.api.skills_list(ctx.creds, page_size=200)
        return {
            "teams": [Choice(str(t.id), t.name) for t in teams.items],
            "skills": [Choice(str(s.id), f"{s.name} ({s.key})") for s in skills.items],
        }

    async def list_extras(self, ctx: UiContext) -> dict[str, Any]:
        skills = await ctx.api.skills_list(ctx.creds, page_size=200)
        return {
            "choices": await self.load_choices(ctx),
            "skill_names": {str(s.id): s.name for s in skills.items},
        }

    async def defaults(self, ctx: UiContext) -> dict[str, Any]:
        settings = await ctx.api.settings_get(ctx.creds)
        return {
            "allocation_percent": settings.default_allocation_percent,
            "active": True,
            "skill_ids": [],
        }

    def values_of(self, row: Any) -> dict[str, Any]:
        values = super().values_of(row)
        values["skill_ids"] = [str(s) for s in row.skill_ids]
        values["team_id"] = str(row.team_id)
        return values

    async def fetch_page(
        self, ctx: UiContext, *, q: str | None, page: int, filters: Mapping[str, str]
    ) -> tuple[list[Any], int]:
        found = await ctx.api.members_list(
            ctx.creds,
            q=q,
            team_id=str(_uuid(filters.get("team")) or "") or None,
            skill_id=str(_uuid(filters.get("skill")) or "") or None,
            active=_flag(filters.get("active")),
            page=page,
            page_size=PAGE_SIZE,
        )
        return list(found.items), found.total

    async def fetch_one(self, ctx: UiContext, ident: str) -> Any:
        return await ctx.api.members_get(ctx.creds, ident)

    async def create(self, ctx: UiContext, values: dict[str, Any]) -> Any:
        return await ctx.api.members_create(ctx.creds, body=models.MemberCreate(**values))

    async def update(
        self, ctx: UiContext, ident: str, values: dict[str, Any], version: int | None
    ) -> Any:
        wanted = values.pop("skill_ids")
        member = await ctx.api.members_update(
            ctx.creds, ident, body=models.MemberUpdate(**values), if_match=version
        )
        if sorted(str(s) for s in member.skill_ids) == sorted(wanted):
            return member
        return await ctx.api.members_set_skills(
            ctx.creds,
            ident,
            body=models.MemberSkillsIn(skill_ids=wanted),
            if_match=member.version,
        )

    async def remove(self, ctx: UiContext, ident: str, version: int | None) -> None:
        await ctx.api.members_delete(ctx.creds, ident, if_match=version)

    def after_save(self, ctx: UiContext, saved: Any) -> list[str]:
        conflicts = getattr(saved, "conflicts", None) or []
        if not conflicts:
            return []
        return [
            gettext_(
                "Saved. %(count)s assignments still use a skill the member no longer has; "
                "they were kept and are marked in the plan."
            )
            % {"count": sum(c.assignment_count for c in conflicts)}
        ]


class AbsenceTypes(Resource):
    slug = "absence-types"
    read = "absence-type:read"
    write = "absence-type:write"
    templates = "absence_types"
    title = N_("Absence types")
    new_title = N_("New absence type")
    edit_title = N_("Edit absence type")
    delete_title = N_("Delete absence type")
    filter_names = ("active",)
    specs: ClassVar[tuple[Spec, ...]] = (
        Spec(
            "key",
            N_("Key"),
            required=True,
            maxlength=64,
            help=N_("Lower-case letters, digits, hyphen and underscore. Used by imports."),
        ),
        Spec("name", N_("Name"), required=True, maxlength=120),
        Spec(
            "letter",
            N_("Letter"),
            required=True,
            maxlength=2,
            help=N_("Shown next to the colour, so the type never relies on colour alone."),
        ),
        Spec("color", N_("Colour"), kind="color", required=True),
        Spec("sort_order", N_("Sort order"), kind="number", minimum=0, maximum=10000),
        Spec(
            "reduces_capacity",
            N_("Reduces capacity"),
            kind="checkbox",
            help=N_("Off for entries that only inform, such as an on-call duty."),
        ),
        Spec("active", N_("Active"), kind="checkbox"),
    )

    async def defaults(self, ctx: UiContext) -> dict[str, Any]:
        return {
            "color": DEFAULT_TYPE_COLOR,
            "letter": "A",
            "sort_order": 0,
            "reduces_capacity": True,
            "active": True,
        }

    async def fetch_page(
        self, ctx: UiContext, *, q: str | None, page: int, filters: Mapping[str, str]
    ) -> tuple[list[Any], int]:
        found = await ctx.api.absence_types_list(
            ctx.creds, active=_flag(filters.get("active")), page=page, page_size=PAGE_SIZE
        )
        return list(found.items), found.total

    async def fetch_one(self, ctx: UiContext, ident: str) -> Any:
        return await ctx.api.absence_types_get(ctx.creds, ident)

    async def create(self, ctx: UiContext, values: dict[str, Any]) -> Any:
        return await ctx.api.absence_types_create(ctx.creds, body=models.AbsenceTypeIn(**values))

    async def update(
        self, ctx: UiContext, ident: str, values: dict[str, Any], version: int | None
    ) -> Any:
        return await ctx.api.absence_types_update(
            ctx.creds, ident, body=models.AbsenceTypeUpdate(**values), if_match=version
        )

    async def remove(self, ctx: UiContext, ident: str, version: int | None) -> None:
        await ctx.api.absence_types_delete(ctx.creds, ident, if_match=version)


ALL: tuple[Resource, ...] = (
    Teams(),
    Skills(),
    Members(),
    AbsenceTypes(),
    work_resources.Tasks(),
    work_resources.Absences(),
)
