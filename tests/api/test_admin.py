"""Users, roles, the mapping of external roles and API tokens (FR-ADM-01, FR-ADM-02, NFR-API-08)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from tests.api.support import USER_PASSWORD, Api, Caller

from planner_api.auth.permissions import ALL_PERMISSIONS, BUILTIN_ROLES, TEAM_SCOPED
from planner_api.models import ApiToken, AppSession, AppUser, AuditLog, Member

USERS = "/api/v1/users"
ROLES = "/api/v1/roles"
MAPPINGS = "/api/v1/role-mappings"
TOKENS = "/api/v1/api-tokens"
STRONG = "Another-Strong-Passw0rd"


async def role_id(admin: Caller, key: str) -> str:
    listing = (await admin.get(f"{ROLES}?pageSize=200")).json()["items"]
    return str(next(r["id"] for r in listing if r["key"] == key))


async def make_user(admin: Caller, username: str, *roles: dict[str, Any], **extra: Any) -> Any:
    body = {"username": username, "displayName": username.title(), "roles": list(roles), **extra}
    response = await admin.post(USERS, body)
    assert response.status_code == 201, response.text
    return response.json()


async def ready(api: Api, created: dict[str, Any]) -> Caller:
    """Sign in with the temporary password and choose a new one, as a real user does."""
    caller = await api.sign_in(created["username"], created["temporaryPassword"])
    done = await caller.post("/api/v1/auth/change-password", {"newPassword": STRONG})
    assert done.status_code == 204, done.text
    return caller


def audit_text(entries: list[AuditLog]) -> str:
    return json.dumps([(e.before, e.after, e.note) for e in entries], default=str).lower()


# --- users ------------------------------------------------------------------------------------


async def test_a_user_is_created_with_a_generated_password_that_must_be_changed(api: Api) -> None:
    admin = await api.admin()
    planner = await role_id(admin, "planner")
    created = await make_user(admin, "zoe", {"roleId": planner}, email="zoe@example.test")
    assert created["authProvider"] == "local" and created["mustChangePassword"] is True
    assert created["roles"][0]["roleKey"] == "planner" and created["roles"][0]["teamIds"] == []
    temporary = created["temporaryPassword"]
    assert temporary and len(temporary) >= 12

    signed_in = await api.sign_in("zoe", temporary)
    assert signed_in.me is not None and signed_in.me["mustChangePassword"] is True
    assert (await signed_in.get("/api/v1/teams")).status_code == 403
    assert (await signed_in.get("/api/v1/auth/me")).status_code == 200
    # the password is shown once and not again
    again = (await admin.get(f"{USERS}/{created['id']}")).json()
    assert "temporaryPassword" not in again


async def test_a_supplied_password_is_checked_and_not_returned(api: Api) -> None:
    admin = await api.admin()
    weak = await admin.post(USERS, {"username": "zoe", "displayName": "Zoe", "password": "short"})
    assert weak.status_code == 422 and weak.json()["code"] == "PASSWORD_POLICY"
    contains_name = await admin.post(
        USERS, {"username": "zoe", "displayName": "Zoe", "password": "zoe-and-a-long-tail-1"}
    )
    assert contains_name.json()["code"] == "PASSWORD_POLICY"
    ok = await admin.post(USERS, {"username": "zoe", "displayName": "Zoe", "password": STRONG})
    assert ok.status_code == 201 and ok.json()["temporaryPassword"] is None
    assert (await api.sign_in("zoe", STRONG)).me is not None


async def test_a_user_name_is_unique_ignoring_case_and_well_formed(api: Api) -> None:
    admin = await api.admin()
    await make_user(admin, "zoe")
    duplicate = await admin.post(USERS, {"username": "ZOE", "displayName": "Other"})
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "DUPLICATE"
    assert duplicate.json()["errors"][0]["field"] == "username"
    for bad in ("two words", "", "-lead", "a/b"):
        response = await admin.post(USERS, {"username": bad, "displayName": "X"})
        assert response.status_code == 422, bad


async def test_roles_can_be_limited_to_teams_and_the_limit_reaches_the_permissions(
    api: Api,
) -> None:
    admin = await api.admin()
    alpha = await admin.create("/api/v1/teams", {"name": "Alpha"})
    await admin.create("/api/v1/teams", {"name": "Beta"})
    lead = await role_id(admin, "team-lead")
    created = await make_user(admin, "lena", {"roleId": lead, "teamIds": [alpha["id"]]})
    assert created["roles"][0]["teamNames"] == ["Alpha"]
    lena = await api.sign_in("lena", created["temporaryPassword"])
    assert lena.me is not None
    grants = {g["permission"]: g["teamIds"] for g in lena.me["grants"]}
    for permission in TEAM_SCOPED:
        assert grants[permission] == [alpha["id"]]
    assert grants["team:read"] is None  # a read permission has nothing to limit


async def test_unknown_and_repeated_roles_and_teams_are_refused(api: Api) -> None:
    admin = await api.admin()
    planner = await role_id(admin, "planner")
    cases = [
        [{"roleId": str(uuid.uuid4())}],
        [{"roleId": planner}, {"roleId": planner}],
        [{"roleId": planner, "teamIds": [str(uuid.uuid4())]}],
    ]
    for roles in cases:
        response = await admin.post(USERS, {"username": "zoe", "displayName": "Z", "roles": roles})
        assert response.status_code == 422, roles
        assert response.json()["errors"][0]["field"] == "roles"


async def test_a_user_is_changed_with_its_version(api: Api) -> None:
    admin = await api.admin()
    planner, viewer = await role_id(admin, "planner"), await role_id(admin, "viewer")
    created = await make_user(admin, "zoe", {"roleId": planner})
    body = {
        "displayName": "Zoe Renamed",
        "email": "zoe@example.test",
        "active": True,
        "roles": [{"roleId": viewer}],
        "memberId": None,
    }
    assert (await admin.put(f"{USERS}/{created['id']}", body)).status_code == 428
    stale = await admin.put(f"{USERS}/{created['id']}", {**body, "version": created["version"] + 5})
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_MISMATCH"
    changed = await admin.put(f"{USERS}/{created['id']}", {**body, "version": created["version"]})
    assert changed.status_code == 200, changed.text
    out = changed.json()
    assert out["displayName"] == "Zoe Renamed" and out["version"] > created["version"]
    assert [r["roleKey"] for r in out["roles"]] == ["viewer"]
    zoe = await api.sign_in("zoe", created["temporaryPassword"])
    assert zoe.me is not None and "task:write" not in zoe.me["permissions"]


async def test_changing_only_the_roles_still_moves_the_version(api: Api) -> None:
    admin = await api.admin()
    planner, viewer = await role_id(admin, "planner"), await role_id(admin, "viewer")
    created = await make_user(admin, "zoe", {"roleId": planner})
    body = {"displayName": "Zoe", "active": True, "roles": [{"roleId": viewer}]}
    first = await admin.put(f"{USERS}/{created['id']}", {**body, "version": created["version"]})
    assert first.json()["version"] > created["version"]
    again = await admin.put(f"{USERS}/{created['id']}", {**body, "version": created["version"]})
    assert again.status_code == 409  # the first change made the version stale


async def test_a_disabled_user_loses_the_session_at_once(api: Api) -> None:
    admin = await api.admin()
    created = await make_user(admin, "zoe", {"roleId": await role_id(admin, "viewer")})
    zoe = await api.sign_in("zoe", created["temporaryPassword"])
    assert (await zoe.get("/api/v1/auth/me")).status_code == 200
    body = {"displayName": "Zoe", "active": False, "roles": [], "version": created["version"]}
    assert (await admin.put(f"{USERS}/{created['id']}", body)).status_code == 200
    assert (await zoe.get("/api/v1/auth/me")).status_code == 401
    refused = await api.anonymous().post(
        "/api/v1/auth/login", {"username": "zoe", "password": created["temporaryPassword"]}
    )
    assert refused.status_code == 403 and refused.json()["code"] == "ACCOUNT_DISABLED"


async def test_a_user_is_linked_to_a_member_once(api: Api) -> None:
    admin = await api.admin()
    team = await admin.create("/api/v1/teams", {"name": "Alpha"})
    ada = await admin.create("/api/v1/members", {"teamId": team["id"], "displayName": "Ada"})
    first = await make_user(admin, "ada", memberId=ada["id"])
    assert first["memberId"] == ada["id"] and first["memberName"] == "Ada"
    clash = await admin.post(
        USERS, {"username": "other", "displayName": "Other", "memberId": ada["id"]}
    )
    assert clash.status_code == 409 and clash.json()["errors"][0]["field"] == "memberId"
    missing = await admin.post(
        USERS, {"username": "third", "displayName": "T", "memberId": str(uuid.uuid4())}
    )
    assert missing.status_code == 422
    # unlinking frees the member
    body = {"displayName": "Ada", "active": True, "roles": [], "version": first["version"]}
    unlinked = await admin.put(f"{USERS}/{first['id']}", body)
    assert unlinked.json()["memberId"] is None
    async with api.factory() as session:
        assert (await session.get(Member, uuid.UUID(ada["id"]))).user_id is None  # type: ignore[union-attr]


async def drop_other_users(api: Api, keep: str) -> None:
    """Leave one account behind, so that it is the only local administrator."""
    async with api.factory() as session:
        for user in (await session.scalars(select(AppUser))).all():
            if user.username != keep:
                await session.delete(user)
        await session.commit()


async def test_the_last_administrator_cannot_be_removed_in_any_way(api: Api) -> None:
    admin = await api.admin()
    assert admin.me is not None
    mine = (await admin.get(f"{USERS}?q={admin.me['username']}")).json()["items"][0]
    await drop_other_users(api, keep=admin.me["username"])
    base = {"displayName": mine["displayName"], "version": mine["version"]}
    deactivate = await admin.put(f"{USERS}/{mine['id']}", {**base, "active": False, "roles": []})
    assert deactivate.status_code == 409 and deactivate.json()["code"] == "LAST_ADMIN"
    strip = await admin.put(f"{USERS}/{mine['id']}", {**base, "active": True, "roles": []})
    assert strip.status_code == 409 and strip.json()["code"] == "LAST_ADMIN"
    # nothing was saved
    assert (await admin.get(f"{USERS}/{mine['id']}")).json()["version"] == mine["version"]


async def test_an_administrator_can_go_when_another_remains(api: Api) -> None:
    admin = await api.admin()
    other = await api.admin()
    assert other.me is not None
    victim = (await admin.get(f"{USERS}?q={other.me['username']}")).json()["items"][0]
    deleted = await admin.delete(f"{USERS}/{victim['id']}?version={victim['version']}")
    assert deleted.status_code == 204
    assert (await other.get("/api/v1/auth/me")).status_code == 401


async def test_you_cannot_delete_your_own_account(api: Api) -> None:
    admin = await api.admin()
    await api.admin()  # so the last-administrator rule is not what answers
    assert admin.me is not None
    mine = (await admin.get(f"{USERS}?q={admin.me['username']}")).json()["items"][0]
    refused = await admin.delete(f"{USERS}/{mine['id']}?version={mine['version']}")
    assert refused.status_code == 409 and refused.json()["code"] == "OWN_ACCOUNT"


async def test_a_role_that_makes_the_last_administrator_cannot_lose_the_right(api: Api) -> None:
    admin = await api.admin()
    assert admin.me is not None
    custom = (
        await admin.post(
            ROLES,
            {"key": "people", "name": "People", "permissions": ["user:admin", "role:admin"]},
        )
    ).json()
    mine = (await admin.get(f"{USERS}?q={admin.me['username']}")).json()["items"][0]
    await drop_other_users(api, keep=admin.me["username"])
    moved = await admin.put(
        f"{USERS}/{mine['id']}",
        {
            "displayName": mine["displayName"],
            "active": True,
            "roles": [{"roleId": custom["id"]}],
            "version": mine["version"],
        },
    )
    assert moved.status_code == 200, moved.text
    stripped = await admin.put(
        f"{ROLES}/{custom['id']}",
        {"name": "People", "permissions": ["role:admin"], "version": custom["version"]},
    )
    assert stripped.status_code == 409 and stripped.json()["code"] == "LAST_ADMIN"
    deleted = await admin.delete(f"{ROLES}/{custom['id']}?version={custom['version']}")
    assert deleted.json()["code"] == "DELETE_BLOCKED"


async def test_a_password_reset_ends_sessions_lifts_the_lock_and_forces_a_change(api: Api) -> None:
    admin = await api.admin()
    created = await make_user(admin, "zoe", {"roleId": await role_id(admin, "viewer")})
    zoe = await api.sign_in("zoe", created["temporaryPassword"])
    for _ in range(3):  # the tests allow three failures
        await api.anonymous().post(
            "/api/v1/auth/login", {"username": "zoe", "password": "wrong-wrong-wrong"}
        )
    locked = (await admin.get(f"{USERS}/{created['id']}")).json()
    assert locked["locked"] is True

    reset = await admin.post(f"{USERS}/{created['id']}/reset-password", {})
    assert reset.status_code == 200 and reset.json()["temporaryPassword"]
    assert (await zoe.get("/api/v1/auth/me")).status_code == 401
    fresh = await api.sign_in("zoe", reset.json()["temporaryPassword"])
    assert fresh.me is not None and fresh.me["mustChangePassword"] is True
    after = (await admin.get(f"{USERS}/{created['id']}")).json()
    assert after["locked"] is False and after["mustChangePassword"] is True


async def test_a_password_can_be_chosen_in_the_reset_and_is_checked(api: Api) -> None:
    admin = await api.admin()
    created = await make_user(admin, "zoe")
    weak = await admin.post(f"{USERS}/{created['id']}/reset-password", {"newPassword": "short"})
    assert weak.status_code == 422 and weak.json()["code"] == "PASSWORD_POLICY"
    chosen = await admin.post(f"{USERS}/{created['id']}/reset-password", {"newPassword": STRONG})
    assert chosen.json()["temporaryPassword"] is None
    assert (await api.sign_in("zoe", STRONG)).me is not None


async def test_the_password_of_a_single_sign_on_account_is_managed_elsewhere(api: Api) -> None:
    admin = await api.admin()
    async with api.factory() as session:
        sso = AppUser(
            username="sso", display_name="Sso", auth_provider="oidc:oidc", external_subject="s-1"
        )
        session.add(sso)
        await session.commit()
        sso_id = str(sso.id)
    refused = await admin.post(f"{USERS}/{sso_id}/reset-password", {})
    assert refused.status_code == 409
    shown = (await admin.get(f"{USERS}/{sso_id}")).json()
    body = {
        "displayName": "Renamed",
        "email": None,
        "active": True,
        "roles": [],
        "version": shown["version"],
    }
    locked = await admin.put(f"{USERS}/{sso_id}", body)
    assert locked.status_code == 409 and locked.json()["code"] == "FIELD_LOCKED"
    ok = await admin.put(f"{USERS}/{sso_id}", {**body, "displayName": "Sso", "active": False})
    assert ok.status_code == 200 and ok.json()["active"] is False


async def test_deleting_a_user_takes_sessions_and_tokens_along(api: Api) -> None:
    admin = await api.admin()
    created = await make_user(admin, "zoe", {"roleId": await role_id(admin, "viewer")})
    await api.sign_in("zoe", created["temporaryPassword"])
    token = await admin.post(
        TOKENS, {"userId": created["id"], "name": "t", "permissions": ["team:read"]}
    )
    assert token.status_code == 201
    deleted = await admin.delete(f"{USERS}/{created['id']}?version={created['version']}")
    assert deleted.status_code == 204
    async with api.factory() as session:
        assert (await session.scalars(select(ApiToken))).all() == []
        left = (await session.scalars(select(AppSession))).all()
        assert all(str(row.user_id) != created["id"] for row in left)


async def test_the_list_filters_and_pages(api: Api) -> None:
    admin = await api.admin()
    for name in ("zz-amy", "zz-bob", "zz-cleo"):
        await make_user(admin, name)
    page = (await admin.get(f"{USERS}?q=zz-&sort=-username")).json()
    assert [u["username"] for u in page["items"]] == ["zz-cleo", "zz-bob", "zz-amy"]
    assert page["total"] == 3
    assert (await admin.get(f"{USERS}?local=false")).json()["total"] == 0
    assert (await admin.get(f"{USERS}?pageSize=1&page=2")).json()["total"] >= 4


async def test_passwords_never_reach_the_audit_log(api: Api) -> None:
    admin = await api.admin()
    created = await make_user(admin, "zoe", password=STRONG)
    await admin.post(f"{USERS}/{created['id']}/reset-password", {"newPassword": f"{STRONG}-2"})
    async with api.factory() as session:
        entries = (await session.scalars(select(AuditLog).order_by(AuditLog.id))).all()
    text = audit_text(list(entries))
    assert STRONG.lower() not in text and "argon2" not in text and "password_hash" not in text
    actions = [e.action for e in entries if e.entity_type == "user"]
    assert "create" in actions and "password.reset" in actions


# --- roles ------------------------------------------------------------------------------------


async def test_the_permission_catalogue_lists_every_permission(api: Api) -> None:
    admin = await api.admin()
    rows = (await admin.get(f"{ROLES}/permissions")).json()["items"]
    assert [r["permission"] for r in rows] == list(ALL_PERMISSIONS)
    scoped = {r["permission"] for r in rows if r["teamScoped"]}
    assert scoped == set(TEAM_SCOPED) and all(r["description"] for r in rows)


async def test_the_built_in_roles_are_listed_with_their_counts(api: Api) -> None:
    admin = await api.admin()
    page = (await admin.get(f"{ROLES}?pageSize=200")).json()
    assert {r["key"] for r in page["items"]} == {r.key for r in BUILTIN_ROLES}
    admin_role = next(r for r in page["items"] if r["key"] == "admin")
    assert admin_role["builtin"] is True and admin_role["permissions"] == list(ALL_PERMISSIONS)
    assert admin_role["userCount"] >= 1
    planner = next(r for r in page["items"] if r["key"] == "planner")
    assert planner["mappingCount"] == 1  # rp-planner


async def test_whoever_manages_users_can_read_roles_but_not_change_them(api: Api) -> None:
    people = await api.as_role(permissions=["user:admin"])
    assert (await people.get(ROLES)).status_code == 200
    assert (await people.get(f"{ROLES}/permissions")).status_code == 200
    forbidden = await people.post(ROLES, {"key": "x", "name": "X", "permissions": ["team:read"]})
    assert forbidden.status_code == 403 and forbidden.json()["permission"] == "role:admin"
    nobody = await api.as_role(permissions=["team:read"])
    assert (await nobody.get(ROLES)).status_code == 403


async def test_a_custom_role_is_created_changed_and_deleted(api: Api) -> None:
    admin = await api.admin()
    created = await admin.post(
        ROLES,
        {
            "key": "ingest",
            "name": "Ingest",
            "description": "Machine import",
            "permissions": ["ingest:write", "task:read", "ingest:write"],
        },
    )
    assert created.status_code == 201, created.text
    role = created.json()
    assert role["builtin"] is False and role["permissions"] == ["task:read", "ingest:write"]
    assert role["userCount"] == 0

    changed = await admin.put(
        f"{ROLES}/{role['id']}",
        {"name": "Ingest 2", "permissions": ["ingest:write"], "version": role["version"]},
    )
    assert changed.status_code == 200 and changed.json()["permissions"] == ["ingest:write"]
    assert changed.json()["version"] > role["version"]
    assert (
        await admin.delete(f"{ROLES}/{role['id']}?version={changed.json()['version']}")
    ).status_code == 204
    assert (await admin.get(f"{ROLES}/{role['id']}")).status_code == 404


async def test_a_role_is_validated(api: Api) -> None:
    admin = await api.admin()
    unknown = await admin.post(ROLES, {"key": "x", "name": "X", "permissions": ["team:fly"]})
    assert unknown.status_code == 422 and unknown.json()["errors"][0]["field"] == "permissions"
    empty = await admin.post(ROLES, {"key": "x", "name": "X", "permissions": []})
    assert empty.status_code == 422
    badkey = await admin.post(
        ROLES, {"key": "Not Valid", "name": "X", "permissions": ["team:read"]}
    )
    assert badkey.status_code == 422
    await admin.create(ROLES, {"key": "dup", "name": "D", "permissions": ["team:read"]})
    duplicate = await admin.post(ROLES, {"key": "dup", "name": "D2", "permissions": ["team:read"]})
    assert duplicate.status_code == 409 and duplicate.json()["errors"][0]["field"] == "key"


async def test_built_in_roles_cannot_be_changed_or_deleted(api: Api) -> None:
    admin = await api.admin()
    viewer = next(
        r
        for r in (await admin.get(f"{ROLES}?pageSize=200")).json()["items"]
        if r["key"] == "viewer"
    )
    changed = await admin.put(
        f"{ROLES}/{viewer['id']}",
        {"name": "Viewer", "permissions": ["team:read"], "version": viewer["version"]},
    )
    assert changed.status_code == 409 and changed.json()["code"] == "BUILTIN_ROLE"
    deleted = await admin.delete(f"{ROLES}/{viewer['id']}?version={viewer['version']}")
    assert deleted.status_code == 409 and deleted.json()["code"] == "BUILTIN_ROLE"


async def test_a_role_in_use_cannot_be_deleted(api: Api) -> None:
    admin = await api.admin()
    role = await admin.create(ROLES, {"key": "x", "name": "X", "permissions": ["team:read"]})
    await make_user(admin, "zoe", {"roleId": role["id"]})
    blocked = await admin.delete(f"{ROLES}/{role['id']}?version={role['version']}")
    assert blocked.status_code == 409 and blocked.json()["code"] == "DELETE_BLOCKED"
    assert blocked.json()["blockers"] == [{"kind": "users", "count": 1}]

    other = await admin.create(ROLES, {"key": "y", "name": "Y", "permissions": ["team:read"]})
    await admin.put(MAPPINGS, {"mappings": [{"externalRole": "ext", "roleId": other["id"]}]})
    blocked = await admin.delete(f"{ROLES}/{other['id']}?version={other['version']}")
    assert blocked.json()["blockers"] == [{"kind": "mappings", "count": 1}]


async def test_a_changed_custom_role_applies_to_its_holders_at_once(api: Api) -> None:
    admin = await api.admin()
    role = await admin.create(ROLES, {"key": "x", "name": "X", "permissions": ["team:read"]})
    created = await make_user(admin, "zoe", {"roleId": role["id"]})
    zoe = await ready(api, created)
    assert (await zoe.get("/api/v1/skills")).status_code == 403
    await admin.put(
        f"{ROLES}/{role['id']}",
        {"name": "X", "permissions": ["team:read", "skill:read"], "version": role["version"]},
    )
    assert (await zoe.get("/api/v1/skills")).status_code == 200


# --- the mapping of external roles ------------------------------------------------------------


async def test_the_default_mapping_has_the_three_keycloak_roles(api: Api) -> None:
    admin = await api.admin()
    body = (await admin.get(MAPPINGS)).json()
    assert body["providerId"] == "oidc"
    assert {(m["externalRole"], m["roleKey"]) for m in body["mappings"]} == {
        ("rp-admin", "admin"),
        ("rp-planner", "planner"),
        ("rp-viewer", "viewer"),
    }


async def test_the_mapping_is_replaced_as_a_whole_and_audited(api: Api) -> None:
    admin = await api.admin()
    viewer, planner = await role_id(admin, "viewer"), await role_id(admin, "planner")
    replaced = await admin.put(
        MAPPINGS,
        {
            "mappings": [
                {"externalRole": "staff", "roleId": viewer},
                {"externalRole": "staff", "roleId": planner},
                {"externalRole": "staff", "roleId": planner},  # repeated, kept once
                {"externalRole": "boss", "roleId": planner},
            ]
        },
    )
    assert replaced.status_code == 200, replaced.text
    assert {(m["externalRole"], m["roleKey"]) for m in replaced.json()["mappings"]} == {
        ("staff", "viewer"),
        ("staff", "planner"),
        ("boss", "planner"),
    }
    assert len(replaced.json()["mappings"]) == 3
    assert (await admin.get(MAPPINGS)).json() == replaced.json()
    async with api.factory() as session:
        entry = (
            await session.scalars(select(AuditLog).where(AuditLog.entity_type == "role-mapping"))
        ).one()
    assert "rp-admin -> admin" in entry.before["mappings"]  # type: ignore[index]
    assert "boss -> planner" in entry.after["mappings"]  # type: ignore[index]


async def test_a_mapping_to_an_unknown_role_is_refused(api: Api) -> None:
    admin = await api.admin()
    refused = await admin.put(
        MAPPINGS, {"mappings": [{"externalRole": "x", "roleId": str(uuid.uuid4())}]}
    )
    assert refused.status_code == 422
    assert len((await admin.get(MAPPINGS)).json()["mappings"]) == 3  # unchanged


async def test_the_mapping_may_be_emptied(api: Api) -> None:
    admin = await api.admin()
    assert (await admin.put(MAPPINGS, {"mappings": []})).json()["mappings"] == []


# --- API tokens -------------------------------------------------------------------------------


async def make_token(
    admin: Caller, user: dict[str, Any], *permissions: str, **extra: Any
) -> dict[str, Any]:
    response = await admin.post(
        TOKENS, {"userId": user["id"], "name": "job", "permissions": list(permissions), **extra}
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def bearer(token: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {token['token']}"}


async def test_a_token_acts_as_its_account_within_its_own_permissions(api: Api) -> None:
    admin = await api.admin()
    user = await make_user(admin, "svc", {"roleId": await role_id(admin, "planner")})
    token = await make_token(admin, user, "team:read", "task:read")
    assert token["token"].startswith("rpt_") and token["prefix"] == token["token"][:12]
    assert token["status"] == "active" and token["username"] == "svc"

    client = api.anonymous()
    assert (await client.get("/api/v1/teams", headers=bearer(token))).status_code == 200
    assert (await client.get("/api/v1/tasks", headers=bearer(token))).status_code == 200
    # the account may read skills, the token may not
    assert (await client.get("/api/v1/skills", headers=bearer(token))).status_code == 403
    who = (await client.get("/api/v1/auth/me", headers=bearer(token))).json()
    assert who["via"] == "api-token" and set(who["permissions"]) == {"team:read", "task:read"}


async def test_the_token_is_shown_once_and_stored_hashed(api: Api) -> None:
    admin = await api.admin()
    user = await make_user(admin, "svc", {"roleId": await role_id(admin, "viewer")})
    token = await make_token(admin, user, "team:read")
    listed = (await admin.get(TOKENS)).json()
    assert listed["total"] == 1 and "token" not in listed["items"][0]
    assert token["token"] not in json.dumps(listed)
    async with api.factory() as session:
        stored = (await session.scalars(select(ApiToken))).one()
    assert stored.token_hash != token["token"] and token["token"] not in stored.token_hash
    audit = audit_text(list(await _audit(api)))
    assert token["token"].lower() not in audit


async def _audit(api: Api) -> list[AuditLog]:
    async with api.factory() as session:
        return list((await session.scalars(select(AuditLog))).all())


async def test_a_token_cannot_grant_what_its_account_lacks_even_later(api: Api) -> None:
    admin = await api.admin()
    user = await make_user(admin, "svc", {"roleId": await role_id(admin, "planner")})
    beyond = await admin.post(
        TOKENS, {"userId": user["id"], "name": "x", "permissions": ["user:admin"]}
    )
    assert beyond.status_code == 422 and beyond.json()["errors"][0]["field"] == "permissions"

    token = await make_token(admin, user, "task:write")
    client = api.anonymous()
    posted = await client.post(
        "/api/v1/tasks", {"key": "K-1", "title": "By token"}, headers=bearer(token)
    )
    assert posted.status_code == 201
    # the account loses the planner role; the token loses the right with it
    current = (await admin.get(f"{USERS}/{user['id']}")).json()
    await admin.put(
        f"{USERS}/{user['id']}",
        {"displayName": "Svc", "active": True, "roles": [], "version": current["version"]},
    )
    again = await client.post(
        "/api/v1/tasks", {"key": "K-2", "title": "Nope"}, headers=bearer(token)
    )
    assert again.status_code == 403


async def test_unknown_permissions_accounts_and_past_expiries_are_refused(api: Api) -> None:
    admin = await api.admin()
    user = await make_user(admin, "svc", {"roleId": await role_id(admin, "viewer")})
    base = {"userId": user["id"], "name": "x", "permissions": ["team:read"]}
    for change, field in (
        ({"permissions": ["team:fly"]}, "permissions"),
        ({"permissions": []}, "permissions"),
        ({"userId": str(uuid.uuid4())}, "userId"),
        ({"expiresAt": (datetime.now(UTC) - timedelta(days=1)).isoformat()}, "expiresAt"),
    ):
        response = await admin.post(TOKENS, {**base, **change})
        assert response.status_code == 422, change
        assert any(e["field"] == field for e in response.json()["errors"]), change


async def test_a_disabled_account_cannot_get_a_token_and_its_tokens_stop_working(api: Api) -> None:
    admin = await api.admin()
    user = await make_user(admin, "svc", {"roleId": await role_id(admin, "viewer")})
    token = await make_token(admin, user, "team:read")
    body = {"displayName": "Svc", "active": False, "roles": [], "version": user["version"]}
    await admin.put(f"{USERS}/{user['id']}", body)
    assert (await api.anonymous().get("/api/v1/teams", headers=bearer(token))).status_code == 401
    refused = await admin.post(
        TOKENS, {"userId": user["id"], "name": "x", "permissions": ["team:read"]}
    )
    assert refused.status_code == 422


async def test_a_revoked_token_stops_working_and_stays_listed_as_revoked(api: Api) -> None:
    admin = await api.admin()
    user = await make_user(admin, "svc", {"roleId": await role_id(admin, "viewer")})
    token = await make_token(admin, user, "team:read")
    client = api.anonymous()
    assert (await client.get("/api/v1/teams", headers=bearer(token))).status_code == 200
    assert (await admin.delete(f"{TOKENS}/{token['id']}")).status_code == 204
    assert (await admin.delete(f"{TOKENS}/{token['id']}")).status_code == 204  # idempotent
    refused = await client.get("/api/v1/teams", headers=bearer(token))
    assert refused.status_code == 401 and refused.json()["code"] == "AUTH_REQUIRED"
    assert (await admin.get(TOKENS)).json()["total"] == 0
    shown = (await admin.get(f"{TOKENS}?includeRevoked=true")).json()["items"][0]
    assert shown["status"] == "revoked" and shown["revokedAt"] and shown["lastUsedAt"]
    assert (await admin.delete(f"{TOKENS}/{uuid.uuid4()}")).status_code == 404


async def test_an_expired_token_is_refused(api: Api) -> None:
    admin = await api.admin()
    user = await make_user(admin, "svc", {"roleId": await role_id(admin, "viewer")})
    soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    token = await make_token(admin, user, "team:read", expiresAt=soon)
    async with api.factory() as session:
        row = (await session.scalars(select(ApiToken))).one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
    refused = await api.anonymous().get("/api/v1/teams", headers=bearer(token))
    assert refused.status_code == 401
    assert (await admin.get(TOKENS)).json()["items"][0]["status"] == "expired"


async def test_the_use_of_a_token_is_recorded_and_audited_with_its_client(api: Api) -> None:
    admin = await api.admin()
    user = await make_user(admin, "svc", {"roleId": await role_id(admin, "planner")})
    token = await make_token(admin, user, "task:write")
    assert (await admin.get(TOKENS)).json()["items"][0]["lastUsedAt"] is None
    client = api.anonymous()
    created = await client.post(
        "/api/v1/tasks",
        {"key": "K-9", "title": "Imported"},
        headers={**bearer(token), "X-Client-Name": "nightly-import"},
    )
    assert created.status_code == 201
    assert (await admin.get(TOKENS)).json()["items"][0]["lastUsedAt"] is not None
    async with api.factory() as session:
        entry = (
            await session.scalars(select(AuditLog).where(AuditLog.entity_type == "task"))
        ).one()
    assert (entry.via, entry.client_name, entry.actor_name) == (
        "api-token",
        "nightly-import",
        "svc",
    )


async def test_a_token_is_not_a_login_and_cannot_change_passwords(api: Api) -> None:
    admin = await api.admin()
    user = await make_user(admin, "svc", {"roleId": await role_id(admin, "viewer")})
    token = await make_token(admin, user, "team:read")
    response = await api.anonymous().post(
        "/api/v1/auth/change-password",
        {"currentPassword": USER_PASSWORD, "newPassword": STRONG},
        headers=bearer(token),
    )
    assert response.status_code == 403


async def test_tokens_can_be_listed_per_account(api: Api) -> None:
    admin = await api.admin()
    first = await make_user(admin, "one", {"roleId": await role_id(admin, "viewer")})
    second = await make_user(admin, "two", {"roleId": await role_id(admin, "viewer")})
    await make_token(admin, first, "team:read")
    await make_token(admin, second, "team:read")
    await make_token(admin, second, "skill:read")
    listing = (await admin.get(f"{TOKENS}?userId={second['id']}")).json()
    assert listing["total"] == 2 and {t["username"] for t in listing["items"]} == {"two"}
