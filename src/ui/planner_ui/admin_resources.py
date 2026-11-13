"""The pages of the administration: accounts, roles and API tokens."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from types import SimpleNamespace
from typing import Any, ClassVar

from planner_client import models
from planner_ui.crud import (
    PAGE_SIZE,
    Choice,
    FieldProblemsError,
    FormField,
    Resource,
    Secret,
    Spec,
)
from planner_ui.i18n import N_, gettext_
from planner_ui.resource_helpers import flag as _flag
from planner_ui.session import UiContext

SOURCES = {"local": True, "sso": False}


async def permission_choices(ctx: UiContext) -> list[Choice]:
    catalogue = await ctx.api.roles_permissions(ctx.creds)
    return [Choice(p.permission, f"{p.permission} — {p.description}") for p in catalogue.items]


class Users(Resource):
    slug = "users"
    read = "user:admin"
    write = "user:admin"
    templates = "users"
    title = N_("Users")
    new_title = N_("New user")
    edit_title = N_("Edit user")
    delete_title = N_("Delete user")
    filter_names = ("source", "active")
    specs: ClassVar[tuple[Spec, ...]] = (
        Spec(
            "username",
            N_("User name"),
            required=True,
            maxlength=160,
            autocomplete="off",
            help=N_("Letters, digits and . _ @ + -. It cannot be changed later."),
        ),
        Spec("display_name", N_("Name"), required=True, maxlength=120),
        Spec("email", N_("E-mail"), kind="email", maxlength=255, autocomplete="off"),
        Spec(
            "password",
            N_("First password"),
            kind="password",
            autocomplete="new-password",
            help=N_(
                "Leave empty to generate one. It is shown once, "
                "and the user chooses a new one at the first sign-in."
            ),
        ),
        Spec(
            "member_id",
            N_("Member"),
            kind="select",
            choices="members",
            help=N_("The member this account stands for; needed to keep the own absences."),
        ),
        Spec(
            "roles",
            N_("Roles"),
            kind="roles",
            choices="roles",
            help=N_(
                "Accounts that sign in with single sign-on get their roles from the "
                "role mapping; roles given here only count if the server merges them."
            ),
        ),
        Spec("active", N_("Active"), kind="checkbox"),
    )

    async def load_choices(self, ctx: UiContext) -> dict[str, list[Choice]]:
        roles = await ctx.api.roles_list(ctx.creds, page_size=200)
        catalogue = await ctx.api.roles_permissions(ctx.creds)
        scoped = {p.permission for p in catalogue.items if p.team_scoped}
        choices: dict[str, list[Choice]] = {
            "roles": [
                Choice(str(r.id), r.name, scoped=bool(scoped & set(r.permissions)))
                for r in roles.items
            ],
            "members": [],
            "teams": [],
        }
        # The editor of accounts need not be allowed to read members and teams.
        if ctx.can("member:read"):
            members = await ctx.api.members_list(ctx.creds, page_size=200)
            choices["members"] = [
                Choice(str(m.id), f"{m.display_name} ({m.team_name})") for m in members.items
            ]
        if ctx.can("team:read"):
            teams = await ctx.api.teams_list(ctx.creds, page_size=200)
            choices["teams"] = [Choice(str(t.id), t.name) for t in teams.items]
        return choices

    async def defaults(self, ctx: UiContext) -> dict[str, Any]:
        return {"active": True, "roles": []}

    def values_of(self, row: Any) -> dict[str, Any]:
        values = super().values_of(row)
        values["member_id"] = str(row.member_id) if row.member_id else ""
        values["roles"] = [
            {"role_id": str(r.role_id), "team_ids": [str(t) for t in r.team_ids]} for r in row.roles
        ]
        return values

    def locked(self, row: Any | None) -> set[str]:
        """An account of the identity provider has its name and e-mail from there."""
        if row is not None and row.auth_provider != "local":
            return {"display_name", "email"}
        return set()

    def build_fields(
        self,
        row: Any | None,
        values: Mapping[str, Any],
        errors: Mapping[str, str],
        choices: Mapping[str, list[Choice]],
    ) -> list[FormField]:
        fields = super().build_fields(row, values, errors, choices)
        if row is None:
            return fields
        return [f for f in fields if f.spec.name not in {"username", "password"}]

    async def fetch_page(
        self, ctx: UiContext, *, q: str | None, page: int, filters: Mapping[str, str]
    ) -> tuple[list[Any], int]:
        found = await ctx.api.users_list(
            ctx.creds,
            q=q,
            local=SOURCES.get(filters.get("source", "")),
            active=_flag(filters.get("active")),
            page=page,
            page_size=PAGE_SIZE,
        )
        return list(found.items), found.total

    async def fetch_one(self, ctx: UiContext, ident: str) -> Any:
        return await ctx.api.users_get(ctx.creds, ident)

    async def create(self, ctx: UiContext, values: dict[str, Any]) -> Any:
        return await ctx.api.users_create(ctx.creds, body=models.UserIn(**values))

    async def update(
        self, ctx: UiContext, ident: str, values: dict[str, Any], version: int | None
    ) -> Any:
        wanted = {k: v for k, v in values.items() if k not in {"username", "password"}}
        return await ctx.api.users_update(
            ctx.creds, ident, body=models.UserUpdate(**wanted), if_match=version
        )

    async def remove(self, ctx: UiContext, ident: str, version: int | None) -> None:
        await ctx.api.users_delete(ctx.creds, ident, if_match=version)

    def secret_of(self, saved: Any, *, created: bool) -> Secret | None:
        password = getattr(saved, "temporary_password", None)
        if not created or not password:
            return None
        return Secret(
            title=gettext_("User created"),
            intro=gettext_(
                "Give this temporary password to the user. It is shown only now, "
                "and the user chooses a new one at the first sign-in."
            ),
            label=gettext_("Temporary password"),
            value=password,
        )


class Roles(Resource):
    slug = "roles"
    read = "role:admin"
    write = "role:admin"
    templates = "roles"
    title = N_("Roles")
    new_title = N_("New role")
    edit_title = N_("Edit role")
    delete_title = N_("Delete role")
    specs: ClassVar[tuple[Spec, ...]] = (
        Spec(
            "key",
            N_("Key"),
            required=True,
            maxlength=64,
            help=N_(
                "Lower-case letters, digits, hyphen and underscore. It cannot be changed later."
            ),
        ),
        Spec("name", N_("Name"), required=True, maxlength=120),
        Spec("description", N_("Description"), kind="textarea", maxlength=300),
        Spec("permissions", N_("Permissions"), kind="multiselect", choices="permissions"),
    )

    async def load_choices(self, ctx: UiContext) -> dict[str, list[Choice]]:
        return {"permissions": await permission_choices(ctx)}

    async def defaults(self, ctx: UiContext) -> dict[str, Any]:
        return {"permissions": []}

    def values_of(self, row: Any) -> dict[str, Any]:
        values = super().values_of(row)
        values["permissions"] = list(row.permissions)
        return values

    def build_fields(
        self,
        row: Any | None,
        values: Mapping[str, Any],
        errors: Mapping[str, str],
        choices: Mapping[str, list[Choice]],
    ) -> list[FormField]:
        fields = super().build_fields(row, values, errors, choices)
        return fields if row is None else [f for f in fields if f.spec.name != "key"]

    async def fetch_page(
        self, ctx: UiContext, *, q: str | None, page: int, filters: Mapping[str, str]
    ) -> tuple[list[Any], int]:
        found = await ctx.api.roles_list(ctx.creds, q=q, page=page, page_size=PAGE_SIZE)
        return list(found.items), found.total

    async def fetch_one(self, ctx: UiContext, ident: str) -> Any:
        return await ctx.api.roles_get(ctx.creds, ident)

    async def create(self, ctx: UiContext, values: dict[str, Any]) -> Any:
        return await ctx.api.roles_create(ctx.creds, body=models.RoleIn(**values))

    async def update(
        self, ctx: UiContext, ident: str, values: dict[str, Any], version: int | None
    ) -> Any:
        wanted = {k: v for k, v in values.items() if k != "key"}
        return await ctx.api.roles_update(
            ctx.creds, ident, body=models.RoleUpdate(**wanted), if_match=version
        )

    async def remove(self, ctx: UiContext, ident: str, version: int | None) -> None:
        await ctx.api.roles_delete(ctx.creds, ident, if_match=version)

    async def list_extras(self, ctx: UiContext) -> dict[str, Any]:
        config = await ctx.api.auth_config(ctx.creds)
        return {"oidc_enabled": config.oidc_enabled}


class ApiTokens(Resource):
    slug = "api-tokens"
    read = "token:admin"
    write = "token:admin"
    templates = "api_tokens"
    title = N_("API tokens")
    new_title = N_("New API token")
    edit_title = N_("API token")
    delete_title = N_("Revoke API token")
    delete_label = N_("Revoke")
    deleted_message = N_("Revoked.")
    editable = False
    filter_names = ("revoked",)
    specs: ClassVar[tuple[Spec, ...]] = (
        Spec("name", N_("Name"), required=True, maxlength=120),
        Spec(
            "user_id",
            N_("Account"),
            kind="select",
            required=True,
            choices="users",
            help=N_("The token acts as this account and can never do more than the account may."),
        ),
        Spec(
            "permissions",
            N_("Permissions"),
            kind="multiselect",
            choices="permissions",
            help=N_("Give a token only what it needs, for example ingest:write for an import."),
        ),
        Spec(
            "expires_at",
            N_("Expires on"),
            kind="date",
            help=N_("Leave empty for a token that does not expire."),
        ),
    )

    async def load_choices(self, ctx: UiContext) -> dict[str, list[Choice]]:
        if ctx.can("user:admin"):
            users = await ctx.api.users_list(ctx.creds, active=True, page_size=200)
            accounts = [Choice(str(u.id), f"{u.display_name} ({u.username})") for u in users.items]
        else:
            accounts = [Choice(str(ctx.me.user_id), f"{ctx.me.display_name} ({ctx.me.username})")]
        return {"users": accounts, "permissions": await permission_choices(ctx)}

    async def defaults(self, ctx: UiContext) -> dict[str, Any]:
        return {"permissions": [], "user_id": str(ctx.me.user_id or "")}

    async def fetch_page(
        self, ctx: UiContext, *, q: str | None, page: int, filters: Mapping[str, str]
    ) -> tuple[list[Any], int]:
        found = await ctx.api.api_tokens_list(
            ctx.creds,
            include_revoked=filters.get("revoked") == "yes",
            page=page,
            page_size=PAGE_SIZE,
        )
        return list(found.items), found.total

    async def fetch_one(self, ctx: UiContext, ident: str) -> Any:
        # A token has no version and no read call; revoking needs only its address.
        return SimpleNamespace(id=ident, version="")

    async def create(self, ctx: UiContext, values: dict[str, Any]) -> Any:
        values = dict(values)
        values["expires_at"] = _end_of_day(values.get("expires_at"))
        return await ctx.api.api_tokens_create(ctx.creds, body=models.ApiTokenIn(**values))

    async def remove(self, ctx: UiContext, ident: str, version: int | None) -> None:
        await ctx.api.api_tokens_revoke(ctx.creds, ident)

    def secret_of(self, saved: Any, *, created: bool) -> Secret | None:
        return Secret(
            title=gettext_("API token created"),
            intro=gettext_("Copy the token now. It is shown only once and cannot be read again."),
            label=gettext_("Token"),
            value=saved.token,
        )


def _end_of_day(text: str | None) -> datetime | None:
    """A date typed in the form is valid until the end of that day (UTC)."""
    if not text:
        return None
    try:
        return datetime.combine(date.fromisoformat(text), time(23, 59, 59), tzinfo=UTC)
    except ValueError as exc:
        raise FieldProblemsError({"expires_at": gettext_("Not a valid date.")}) from exc


ALL: tuple[Resource, ...] = (Users(), Roles(), ApiTokens())
