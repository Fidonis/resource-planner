"""Local sign-in, sessions, CSRF, lockout, API tokens (FR-AUTH-01, -02, -07, NFR-API-05, -08)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from tests.api.support import BOOTSTRAP_PASSWORD, BOOTSTRAP_USER, USER_PASSWORD, Api

from planner_api.auth.passwords import hash_token
from planner_api.models import ApiToken, AppUser

LOGIN = "/api/v1/auth/login"


async def test_public_config_needs_no_credentials(api: Api) -> None:
    anonymous = api.anonymous()
    response = await anonymous.get("/api/v1/auth/config")
    assert response.status_code == 200
    body = response.json()
    assert body["localEnabled"] is True
    assert body["oidcEnabled"] is False
    assert body["oidcLoginUrl"] is None
    assert body["passwordMinLength"] == 12


async def test_protected_endpoint_without_credentials_is_401_problem(api: Api) -> None:
    response = await api.anonymous().get("/api/v1/teams")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["www-authenticate"] == "Bearer"
    body = response.json()
    assert body["code"] == "AUTH_REQUIRED"
    assert body["type"] == "urn:resource-planner:problem:AUTH_REQUIRED"
    assert body["status"] == 401
    assert response.headers["x-request-id"] == body["requestId"]


async def test_bootstrap_admin_must_choose_a_new_password(api: Api) -> None:
    caller = await api.sign_in(BOOTSTRAP_USER, BOOTSTRAP_PASSWORD)
    assert caller.me is not None
    assert caller.me["mustChangePassword"] is True
    assert "settings:admin" in caller.me["permissions"]

    # Everything but the password change itself is closed until the password was changed.
    blocked = await caller.get("/api/v1/teams")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "PASSWORD_CHANGE_REQUIRED"
    assert (await caller.get("/api/v1/auth/me")).status_code == 200

    weak = await caller.post("/api/v1/auth/change-password", {"newPassword": "short"})
    assert weak.status_code == 422
    assert weak.json()["code"] == "PASSWORD_POLICY"

    contains_name = await caller.post(
        "/api/v1/auth/change-password", {"newPassword": "xx-admin-xx-long-enough"}
    )
    assert contains_name.json()["code"] == "PASSWORD_POLICY"

    changed = await caller.post("/api/v1/auth/change-password", {"newPassword": USER_PASSWORD})
    assert changed.status_code == 204
    listed = await caller.get("/api/v1/teams")
    assert listed.status_code == 200, listed.text

    fresh = api.anonymous()
    old = await fresh.post(LOGIN, {"username": BOOTSTRAP_USER, "password": BOOTSTRAP_PASSWORD})
    assert old.status_code == 401
    new = await fresh.post(LOGIN, {"username": BOOTSTRAP_USER, "password": USER_PASSWORD})
    assert new.status_code == 200
    assert new.json()["mustChangePassword"] is False


async def test_changing_the_password_ends_other_sessions_and_asks_for_the_old_one(
    api: Api,
) -> None:
    await api.add_user("carol", "viewer")
    first = await api.sign_in("carol")
    second = await api.sign_in("carol")

    missing = await first.post(
        "/api/v1/auth/change-password", {"newPassword": "Another-Long-Pw-77"}
    )
    assert missing.status_code == 422
    wrong = await first.post(
        "/api/v1/auth/change-password",
        {"currentPassword": "not-the-password", "newPassword": "Another-Long-Pw-77"},
    )
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "INVALID_CREDENTIALS"

    ok = await first.post(
        "/api/v1/auth/change-password",
        {"currentPassword": USER_PASSWORD, "newPassword": "Another-Long-Pw-77"},
    )
    assert ok.status_code == 204
    assert (await first.get("/api/v1/auth/me")).status_code == 200
    assert (await second.get("/api/v1/auth/me")).status_code == 401


async def test_wrong_credentials_do_not_tell_whether_the_user_exists(api: Api) -> None:
    await api.add_user("dave", "viewer")
    anonymous = api.anonymous()
    unknown = await anonymous.post(LOGIN, {"username": "nobody", "password": "x" * 14})
    wrong = await anonymous.post(LOGIN, {"username": "dave", "password": "x" * 14})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["code"] == wrong.json()["code"] == "INVALID_CREDENTIALS"
    assert unknown.json()["detail"] == wrong.json()["detail"]


async def test_account_locks_after_repeated_failures_even_for_the_right_password(
    api: Api,
) -> None:
    await api.add_user("erin", "viewer")
    anonymous = api.anonymous()
    for _ in range(3):
        failed = await anonymous.post(LOGIN, {"username": "erin", "password": "wrong-password"})
        assert failed.status_code == 401
    locked = await anonymous.post(LOGIN, {"username": "erin", "password": USER_PASSWORD})
    assert locked.status_code == 429
    assert locked.json()["code"] == "ACCOUNT_LOCKED"
    assert int(locked.headers["retry-after"]) > 0

    async with api.factory() as session:
        user = (await session.scalars(select(AppUser).where(AppUser.username == "erin"))).one()
        user.locked_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert (
        await anonymous.post(LOGIN, {"username": "erin", "password": USER_PASSWORD})
    ).status_code == 200


async def test_disabled_account_cannot_sign_in(api: Api) -> None:
    await api.add_user("frank", "viewer", active=False)
    response = await api.anonymous().post(LOGIN, {"username": "frank", "password": USER_PASSWORD})
    assert response.status_code == 403
    assert response.json()["code"] == "ACCOUNT_DISABLED"


async def test_sign_in_is_rate_limited_per_address(api: Api) -> None:
    anonymous = api.anonymous()
    statuses = [
        (await anonymous.post(LOGIN, {"username": f"u{i}", "password": "x" * 14})).status_code
        for i in range(22)
    ]
    assert statuses[:20] == [401] * 20
    assert statuses[20:] == [429, 429]


async def test_changes_with_a_session_need_the_csrf_token(api: Api) -> None:
    caller = await api.as_role("master-data-admin")
    body = {"name": "Alpha", "color": "#336699"}
    without = await caller.post("/api/v1/teams", body, headers={"X-CSRF-Token": "wrong"})
    assert without.status_code == 403
    assert without.json()["code"] == "CSRF_FAILED"
    assert (await caller.get("/api/v1/teams")).status_code == 200  # reading needs none
    assert (await caller.post("/api/v1/teams", body)).status_code == 201


async def test_logout_ends_the_session(api: Api) -> None:
    caller = await api.as_role("viewer")
    assert (await caller.get("/api/v1/auth/me")).status_code == 200
    out = await caller.post("/api/v1/auth/logout")
    assert out.status_code == 200
    assert out.json() == {"redirectUrl": None}
    assert (await caller.get("/api/v1/auth/me")).status_code == 401


async def test_me_lists_permissions_and_team_scope(api: Api) -> None:
    admin = await api.admin()
    team = await admin.create("/api/v1/teams", {"name": "Alpha", "color": "#336699"})
    lead = await api.as_role("team-lead", teams=[uuid.UUID(team["id"])])
    assert lead.me is not None
    grants = {g["permission"]: g["teamIds"] for g in lead.me["grants"]}
    assert grants["assignment:write"] == [team["id"]]
    assert grants["member:write"] == [team["id"]]
    assert grants["team:read"] is None  # reading is never limited to teams
    assert "task:write" not in grants


async def test_preferences_change_the_language_of_the_account(api: Api) -> None:
    caller = await api.as_role("viewer")
    response = await caller.put("/api/v1/auth/me/preferences", {"locale": "de"})
    assert response.status_code == 200
    assert response.json()["locale"] == "de"
    assert (await caller.get("/api/v1/auth/me")).json()["locale"] == "de"
    unknown = await caller.put("/api/v1/auth/me/preferences", {"locale": "xx"})
    assert unknown.status_code == 422


async def _token(api: Api, owner: str, permissions: list[str], **changes: object) -> str:
    plain = f"rpt_{uuid.uuid4().hex}"
    async with api.factory() as session:
        user = (await session.scalars(select(AppUser).where(AppUser.username == owner))).one()
        session.add(
            ApiToken(
                user_id=user.id,
                name="test",
                token_hash=hash_token(plain),
                prefix=plain[:8],
                permissions=permissions,
                **changes,
            )
        )
        await session.commit()
    return plain


async def test_api_token_acts_with_the_owners_permissions_narrowed_to_its_own(api: Api) -> None:
    await api.add_user("grace", "admin")
    token = await _token(api, "grace", ["team:read", "audit:read", "no:such:permission"])
    machine = api.anonymous()
    headers = {"Authorization": f"Bearer {token}"}

    me = await machine.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["via"] == "api-token"
    assert me.json()["csrfToken"] is None
    assert me.json()["permissions"] == ["audit:read", "team:read"]

    assert (await machine.get("/api/v1/teams", headers=headers)).status_code == 200
    # The owner may write teams, the token may not.
    denied = await machine.post("/api/v1/teams", {"name": "X", "color": "#336699"}, headers=headers)
    assert denied.status_code == 403
    assert denied.json()["permission"] == "team:write"


async def test_api_token_is_recorded_in_the_audit_log(api: Api) -> None:
    await api.add_user("heidi", "admin")
    token = await _token(api, "heidi", ["team:write", "audit:read"])
    machine = api.anonymous()
    headers = {"Authorization": f"Bearer {token}", "X-Client-Name": "nightly-import"}
    created = await machine.post(
        "/api/v1/teams", {"name": "Via token", "color": "#336699"}, headers=headers
    )
    assert created.status_code == 201
    entries = (await machine.get("/api/v1/audit-log?entityType=team", headers=headers)).json()
    assert entries["items"][0]["via"] == "api-token"
    assert entries["items"][0]["clientName"] == "nightly-import"
    assert entries["items"][0]["actorName"] == "heidi"


async def test_expired_revoked_and_unknown_tokens_are_refused(api: Api) -> None:
    await api.add_user("ivan", "admin")
    expired = await _token(
        api, "ivan", ["team:read"], expires_at=datetime.now(UTC) - timedelta(days=1)
    )
    revoked = await _token(api, "ivan", ["team:read"], revoked_at=datetime.now(UTC))
    machine = api.anonymous()
    for token in (expired, revoked, "rpt_does-not-exist"):
        response = await machine.get("/api/v1/teams", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, token
        assert response.json()["code"] == "AUTH_REQUIRED"


async def test_a_token_of_a_disabled_owner_stops_working(api: Api) -> None:
    await api.add_user("judy", "admin")
    token = await _token(api, "judy", ["team:read"])
    async with api.factory() as session:
        user = (await session.scalars(select(AppUser).where(AppUser.username == "judy"))).one()
        user.active = False
        await session.commit()
    response = await api.anonymous().get(
        "/api/v1/teams", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


async def test_foreign_bearer_tokens_are_refused_while_oidc_is_off(api: Api) -> None:
    response = await api.anonymous().get(
        "/api/v1/teams", headers={"Authorization": "Bearer eyJhbGciOi.fake.token"}
    )
    assert response.status_code == 401
