"""Single sign-on (FR-AUTH-03..06) and bearer tokens (FR-AUTH-05) against a fake provider."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select
from tests.api.oidc_support import (
    API_AUDIENCE,
    CLIENT_ID,
    ISSUER,
    FakeIdp,
    new_key,
)
from tests.api.support import Api, Caller

from planner_api.auth.permissions import BUILTIN_ROLES
from planner_api.config import Settings
from planner_api.models import AppSession, AppUser, AuditLog, ExternalRoleMapping, Role

AUTH = "/api/v1/auth"
PLANNER = next(role for role in BUILTIN_ROLES if role.key == "planner")
VIEWER = next(role for role in BUILTIN_ROLES if role.key == "viewer")


def query_of(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


async def start(caller: Caller, **params: str) -> httpx.Response:
    return await caller.get(f"{AUTH}/oidc/login", params=params, follow_redirects=False)


async def finish(caller: Caller, idp: FakeIdp, started: httpx.Response) -> httpx.Response:
    query = query_of(started.headers["location"])
    code = idp.authorize(query)
    return await caller.get(
        f"{AUTH}/oidc/callback", params={"code": code, "state": query["state"][0]}
    )


async def sign_in(api: Api, idp: FakeIdp, **params: str) -> tuple[Caller, httpx.Response]:
    caller = api.anonymous()
    started = await start(caller, **params)
    assert started.status_code == 302, started.text
    done = await finish(caller, idp, started)
    if "rp_session=" in done.headers.get("set-cookie", ""):
        caller.csrf = (await me(caller))["csrfToken"]
    return caller, done


async def me(caller: Caller) -> dict[str, Any]:
    response = await caller.get(f"{AUTH}/me")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- the authorization code flow --------------------------------------------------------------


async def test_the_login_sends_the_browser_to_the_provider_with_pkce(
    oidc_api: Api, idp: FakeIdp
) -> None:
    caller = oidc_api.anonymous()
    started = await start(caller, next="/planning?weeks=4")
    assert started.status_code == 302
    location = started.headers["location"]
    assert location.startswith(f"{ISSUER}/protocol/openid-connect/auth?")
    query = query_of(location)
    assert query["client_id"] == [CLIENT_ID] and query["code_challenge_method"] == ["S256"]
    assert len(query["state"][0]) >= 24 and len(query["nonce"][0]) >= 24
    assert (
        "rp_oidc" in started.headers["set-cookie"] and "HttpOnly" in started.headers["set-cookie"]
    )
    assert "client_secret" not in location


async def test_a_signed_in_user_holds_the_permissions_of_the_mapped_role(
    oidc_api: Api, idp: FakeIdp
) -> None:
    caller, done = await sign_in(oidc_api, idp, next="/planning?weeks=4")
    assert done.status_code == 303 and done.headers["location"] == "/planning?weeks=4"
    assert "rp_session=" in done.headers["set-cookie"]
    who = await me(caller)
    assert who["username"] == "zoe" and who["displayName"] == "Zoe Example"
    assert who["authProvider"] == "oidc:oidc" and who["via"] == "session"
    assert set(who["permissions"]) == set(PLANNER.permissions)  # acceptance case 9
    assert who["csrfToken"]


async def test_the_account_is_created_once_and_audited(oidc_api: Api, idp: FakeIdp) -> None:
    await sign_in(oidc_api, idp)
    await sign_in(oidc_api, idp)
    async with oidc_api.factory() as session:
        users = (await session.scalars(select(AppUser).where(AppUser.username == "zoe"))).all()
        assert len(users) == 1
        assert users[0].external_subject == "sub-zoe" and users[0].password_hash is None
        assert users[0].external_roles == ["rp-planner"] and users[0].last_login_at
        actions = (await session.scalars(select(AuditLog.action).order_by(AuditLog.id))).all()
    assert actions.count("provision") == 1 and actions.count("login") == 2


async def test_a_changed_mapping_applies_without_a_new_sign_in(oidc_api: Api, idp: FakeIdp) -> None:
    caller, _ = await sign_in(oidc_api, idp)
    assert "task:write" in (await me(caller))["permissions"]
    async with oidc_api.factory() as session:
        viewer = (await session.scalars(select(Role).where(Role.key == "viewer"))).one()
        mapping = (
            await session.scalars(
                select(ExternalRoleMapping).where(ExternalRoleMapping.external_role == "rp-planner")
            )
        ).one()
        mapping.role_id = viewer.id
        await session.commit()
    assert set((await me(caller))["permissions"]) == set(VIEWER.permissions)


async def test_roles_without_a_mapping_give_no_permissions(oidc_api: Api, idp: FakeIdp) -> None:
    idp.roles = ["something-else"]
    caller, done = await sign_in(oidc_api, idp)
    assert done.status_code == 303
    assert (await me(caller))["permissions"] == []
    assert (await caller.get("/api/v1/teams")).status_code == 403


@pytest.mark.parametrize("source", ["id_token", "access_token", "userinfo"])
async def test_the_roles_come_from_the_configured_source(
    oidc_api: Api, idp: FakeIdp, source: str
) -> None:
    oidc_api.settings.oidc_roles_source = source  # type: ignore[assignment]
    try:
        # Only the configured source carries the roles.
        idp.hide_roles_in = {"id_token", "access_token"} - {source}
        caller, _ = await sign_in(oidc_api, idp)
        assert set((await me(caller))["permissions"]) == set(PLANNER.permissions)
    finally:
        oidc_api.settings.oidc_roles_source = "access_token"


async def test_a_member_with_the_verified_address_is_linked(oidc_api: Api, idp: FakeIdp) -> None:
    admin = await oidc_api.admin()
    team = await admin.create("/api/v1/teams", {"name": "Alpha"})
    ada = await admin.create(
        "/api/v1/members",
        {"teamId": team["id"], "displayName": "Zoe M.", "email": "zoe@example.test"},
    )
    caller, _ = await sign_in(oidc_api, idp)
    assert (await me(caller))["memberId"] == ada["id"]


async def test_an_unverified_address_does_not_link_a_member(oidc_api: Api, idp: FakeIdp) -> None:
    admin = await oidc_api.admin()
    team = await admin.create("/api/v1/teams", {"name": "Alpha"})
    await admin.create(
        "/api/v1/members",
        {"teamId": team["id"], "displayName": "Zoe M.", "email": "zoe@example.test"},
    )
    idp.email_verified = False
    caller, _ = await sign_in(oidc_api, idp)
    assert (await me(caller))["memberId"] is None


async def test_the_return_address_must_stay_on_this_site(oidc_api: Api, idp: FakeIdp) -> None:
    for wanted in ("https://evil.example/x", "//evil.example", "/\\evil.example", "javascript:1"):
        _, done = await sign_in(oidc_api, idp, next=wanted)
        assert done.headers["location"] == "/", wanted


async def test_an_oidc_account_cannot_sign_in_with_a_password(oidc_api: Api, idp: FakeIdp) -> None:
    await sign_in(oidc_api, idp)
    response = await oidc_api.anonymous().post(
        f"{AUTH}/login", {"username": "zoe", "password": "Whatever-Passw0rd-1"}
    )
    assert response.status_code == 401 and response.json()["code"] == "INVALID_CREDENTIALS"


async def test_two_users_of_the_provider_with_one_name_get_two_accounts(
    oidc_api: Api, idp: FakeIdp
) -> None:
    await sign_in(oidc_api, idp)
    idp.subject = "sub-other"
    caller, _ = await sign_in(oidc_api, idp)
    other = await me(caller)
    assert other["username"].startswith("zoe~") and other["userId"]


# --- refusals ---------------------------------------------------------------------------------


def refused(done: httpx.Response, reason: str = "oidc_failed") -> None:
    assert done.status_code == 303 and done.headers["location"] == f"/login?error={reason}"
    assert "rp_session=" not in done.headers.get("set-cookie", "")


async def test_a_state_works_once(oidc_api: Api, idp: FakeIdp) -> None:
    caller = oidc_api.anonymous()
    started = await start(caller)
    query = query_of(started.headers["location"])
    params = {"code": idp.authorize(query), "state": query["state"][0]}
    first = await caller.get(f"{AUTH}/oidc/callback", params=params)
    assert first.status_code == 303 and first.headers["location"] == "/"
    again = {"code": idp.authorize(query), "state": query["state"][0]}
    refused(await caller.get(f"{AUTH}/oidc/callback", params=again))


async def test_an_unknown_state_is_refused(oidc_api: Api, idp: FakeIdp) -> None:
    caller = oidc_api.anonymous()
    started = await start(caller)
    code = idp.authorize(query_of(started.headers["location"]))
    refused(await caller.get(f"{AUTH}/oidc/callback", params={"code": code, "state": "nope"}))


async def test_the_browser_that_did_not_start_the_sign_in_cannot_finish_it(
    oidc_api: Api, idp: FakeIdp
) -> None:
    started = await start(oidc_api.anonymous())
    query = query_of(started.headers["location"])
    stranger = oidc_api.anonymous()  # no binding cookie
    done = await stranger.get(
        f"{AUTH}/oidc/callback",
        params={"code": idp.authorize(query), "state": query["state"][0]},
    )
    refused(done)


async def test_an_error_from_the_provider_ends_in_the_login_page(oidc_api: Api) -> None:
    caller = oidc_api.anonymous()
    done = await caller.get(f"{AUTH}/oidc/callback", params={"error": "access_denied"})
    refused(done)


async def test_an_id_token_for_another_nonce_is_refused(oidc_api: Api, idp: FakeIdp) -> None:
    caller = oidc_api.anonymous()
    started = await start(caller)
    query = query_of(started.headers["location"])
    code = idp.authorize({**query, "nonce": ["not-the-nonce-sent"]})
    done = await caller.get(
        f"{AUTH}/oidc/callback", params={"code": code, "state": query["state"][0]}
    )
    refused(done)


async def test_an_access_token_of_somebody_else_is_refused_at_sign_in(
    oidc_api: Api, idp: FakeIdp
) -> None:
    idp.access_overrides = {"sub": "sub-somebody-else"}
    _, done = await sign_in(oidc_api, idp)
    refused(done)


async def test_an_access_token_without_a_subject_is_refused_at_sign_in(
    oidc_api: Api, idp: FakeIdp
) -> None:
    idp.access_overrides = {"sub": None}  # Keycloak leaves it out without the scope "basic"
    _, done = await sign_in(oidc_api, idp)
    refused(done)


async def test_a_disabled_account_cannot_sign_in(oidc_api: Api, idp: FakeIdp) -> None:
    await sign_in(oidc_api, idp)
    async with oidc_api.factory() as session:
        (
            await session.scalars(select(AppUser).where(AppUser.username == "zoe"))
        ).one().active = False
        await session.commit()
    _, done = await sign_in(oidc_api, idp)
    refused(done, "account_disabled")


async def test_a_session_ends_when_the_account_is_disabled(oidc_api: Api, idp: FakeIdp) -> None:
    caller, _ = await sign_in(oidc_api, idp)
    async with oidc_api.factory() as session:
        (
            await session.scalars(select(AppUser).where(AppUser.username == "zoe"))
        ).one().active = False
        await session.commit()
    assert (await caller.get(f"{AUTH}/me")).status_code == 401


async def test_the_provider_being_down_is_reported_as_a_failed_sign_in(
    oidc_api: Api, idp: FakeIdp
) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable")

    oidc_api.app.state.oidc._transport = httpx.MockTransport(down)
    oidc_api.app.state.oidc._discovery = None
    response = await start(oidc_api.anonymous())
    assert (
        response.status_code == 303
        and response.headers["location"] == "/login?error=oidc_unavailable"
    )


async def test_the_other_login_endpoints_are_off_when_single_sign_on_is_off(api: Api) -> None:
    response = await api.anonymous().get(f"{AUTH}/oidc/login", follow_redirects=False)
    assert response.status_code == 403 and response.json()["code"] == "AUTH_MODE_DISABLED"


# --- logout -----------------------------------------------------------------------------------


async def test_the_logout_of_an_oidc_session_names_the_provider_logout_page(
    oidc_api: Api, idp: FakeIdp
) -> None:
    caller, _ = await sign_in(oidc_api, idp)
    ended = await caller.post(f"{AUTH}/logout")
    assert ended.status_code == 200
    target = ended.json()["redirectUrl"]
    assert target.startswith(f"{ISSUER}/protocol/openid-connect/logout?")
    query = query_of(target)
    assert query["post_logout_redirect_uri"] == ["http://localhost:8080/login"]
    assert query["client_id"] == [CLIENT_ID] and query["id_token_hint"][0].count(".") == 2
    assert (await caller.get(f"{AUTH}/me")).status_code == 401


async def test_the_logout_of_a_local_session_has_no_provider_page(oidc_api: Api) -> None:
    admin = await oidc_api.admin()
    ended = await admin.post(f"{AUTH}/logout")
    assert ended.status_code == 200 and ended.json()["redirectUrl"] is None


async def test_every_session_row_of_an_oidc_login_keeps_the_hint(
    oidc_api: Api, idp: FakeIdp
) -> None:
    await sign_in(oidc_api, idp)
    async with oidc_api.factory() as session:
        rows = (await session.scalars(select(AppSession))).all()
    assert [bool(r.id_token_hint) for r in rows] == [True]


# --- bearer tokens ----------------------------------------------------------------------------


async def test_a_bearer_token_acts_for_its_user(oidc_api: Api, idp: FakeIdp) -> None:
    client = oidc_api.anonymous()
    token = idp.access_token(audience=[API_AUDIENCE, "account"])
    response = await client.get(f"{AUTH}/me", headers=bearer(token))
    assert response.status_code == 200, response.text
    who = response.json()
    assert who["via"] == "bearer" and who["username"] == "zoe" and who["csrfToken"] is None
    assert set(who["permissions"]) == set(PLANNER.permissions)
    # changes need no CSRF token: the token is not sent by the browser on its own
    created = await client.post("/api/v1/teams", {"name": "Via token"}, headers=bearer(token))
    assert created.status_code == 403  # a planner may not edit master data
    task = await client.post(
        "/api/v1/tasks", {"key": "B-1", "title": "By bearer"}, headers=bearer(token)
    )
    assert task.status_code == 201, task.text


async def test_the_roles_of_the_token_decide_each_call(oidc_api: Api, idp: FakeIdp) -> None:
    client = oidc_api.anonymous()
    planner = idp.access_token(audience=API_AUDIENCE, roles=["rp-planner"])
    nobody = idp.access_token(audience=API_AUDIENCE, roles=[])
    assert (await client.get("/api/v1/teams", headers=bearer(planner))).status_code == 200
    assert (await client.get("/api/v1/teams", headers=bearer(nobody))).status_code == 403
    assert (await client.get("/api/v1/teams", headers=bearer(planner))).status_code == 200


async def test_the_audit_log_names_the_bearer_client(oidc_api: Api, idp: FakeIdp) -> None:
    client = oidc_api.anonymous()
    token = idp.access_token(audience=API_AUDIENCE)
    response = await client.post(
        "/api/v1/tasks",
        {"key": "B-2", "title": "Audited"},
        headers={**bearer(token), "X-Client-Name": "planner-mcp"},
    )
    assert response.status_code == 201
    async with oidc_api.factory() as session:
        entry = (
            await session.scalars(select(AuditLog).where(AuditLog.entity_type == "task"))
        ).one()
    assert (entry.via, entry.client_name, entry.actor_name) == ("bearer", "planner-mcp", "zoe")


def forged(idp: FakeIdp, **claims: Any) -> str:
    base = idp.base_claims(audience=API_AUDIENCE)
    base.update(preferred_username="zoe", realm_access={"roles": ["rp-admin"]})
    base.update(claims)
    return idp.sign({k: v for k, v in base.items() if v is not None})


@pytest.mark.parametrize(
    ("name", "make"),
    [
        ("wrong audience", lambda idp: forged(idp, aud="another-api")),
        ("no audience", lambda idp: forged(idp, aud=None)),
        ("expired", lambda idp: forged(idp, exp=int(time.time()) - 600)),
        ("no expiry", lambda idp: forged(idp, exp=None)),
        ("wrong issuer", lambda idp: forged(idp, iss="https://evil.example/realms/test")),
        (
            "foreign key",
            lambda idp: idp.sign(idp.base_claims(audience=API_AUDIENCE), key=new_key()),
        ),
        (
            "unknown key id",
            lambda idp: idp.sign(idp.base_claims(audience=API_AUDIENCE), kid="other"),
        ),
        ("no subject", lambda idp: forged(idp, sub=None)),
        ("not a token", lambda idp: "not.a.jwt"),
    ],
)
async def test_a_bad_bearer_token_is_refused(
    oidc_api: Api, idp: FakeIdp, name: str, make: Any
) -> None:
    response = await oidc_api.anonymous().get(f"{AUTH}/me", headers=bearer(make(idp)))
    assert response.status_code == 401, name
    assert response.json()["code"] == "AUTH_REQUIRED"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_a_token_signed_with_a_shared_secret_is_refused(oidc_api: Api, idp: FakeIdp) -> None:
    secret = "x" * 64
    token = idp.sign(idp.base_claims(audience=API_AUDIENCE), key=secret, algorithm="HS256")
    response = await oidc_api.anonymous().get(f"{AUTH}/me", headers=bearer(token))
    assert response.status_code == 401


async def test_an_unsigned_token_is_refused(oidc_api: Api, idp: FakeIdp) -> None:
    import jwt

    token = jwt.encode(idp.base_claims(audience=API_AUDIENCE), "", algorithm="none")
    response = await oidc_api.anonymous().get(f"{AUTH}/me", headers=bearer(token))
    assert response.status_code == 401


async def test_a_disabled_account_is_refused_with_its_bearer_token(
    oidc_api: Api, idp: FakeIdp
) -> None:
    client = oidc_api.anonymous()
    token = idp.access_token(audience=API_AUDIENCE)
    assert (await client.get(f"{AUTH}/me", headers=bearer(token))).status_code == 200
    async with oidc_api.factory() as session:
        (
            await session.scalars(select(AppUser).where(AppUser.username == "zoe"))
        ).one().active = False
        await session.commit()
    response = await client.get(f"{AUTH}/me", headers=bearer(token))
    assert response.status_code == 403 and response.json()["code"] == "ACCOUNT_DISABLED"


async def test_bearer_tokens_are_off_without_accepted_audiences(
    oidc_api: Api, idp: FakeIdp
) -> None:
    oidc_api.settings.oidc_api_audiences = ""
    try:
        token = idp.access_token(audience=API_AUDIENCE)
        response = await oidc_api.anonymous().get(f"{AUTH}/me", headers=bearer(token))
        assert response.status_code == 401
    finally:
        oidc_api.settings.oidc_api_audiences = API_AUDIENCE


async def test_a_bearer_token_is_not_accepted_when_single_sign_on_is_off(
    api: Api, idp: FakeIdp
) -> None:
    token = idp.access_token(audience=API_AUDIENCE)
    response = await api.anonymous().get(f"{AUTH}/me", headers=bearer(token))
    assert response.status_code == 401


async def test_a_new_key_of_the_provider_is_picked_up(oidc_api: Api, idp: FakeIdp) -> None:
    client = oidc_api.anonymous()
    assert (
        await client.get(f"{AUTH}/me", headers=bearer(idp.access_token(audience=API_AUDIENCE)))
    ).status_code == 200
    # the provider rotates its key; the cached key set does not know the new key id yet
    idp.key, idp.kid = new_key(), "test-key-2"
    provider = oidc_api.app.state.oidc
    provider._jwks = (provider._jwks[0] - 120, provider._jwks[1])  # the refresh limit has passed
    token = idp.access_token(audience=API_AUDIENCE)
    assert (await client.get(f"{AUTH}/me", headers=bearer(token))).status_code == 200


def test_single_sign_on_needs_its_settings() -> None:
    with pytest.raises(ValueError, match="OIDC_ISSUER"):
        Settings(auth_oidc_enabled=True)
