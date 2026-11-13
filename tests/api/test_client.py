"""The generated client against the real application: what the web interface relies on."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport
from tests.api.support import USER_PASSWORD, Api

from planner_client import ApiError, Credentials, PlannerApi, models

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def client(api: Api) -> AsyncIterator[PlannerApi]:
    planner = PlannerApi("http://test", transport=ASGITransport(app=api.app))
    yield planner
    await planner.aclose()


async def sign_in(client: PlannerApi, username: str) -> Credentials:
    creds = Credentials(client_name="planner-ui")
    me = await client.auth_login(
        creds, body=models.LoginRequest(username=username, password=USER_PASSWORD)
    )
    creds.csrf_token = me.csrf_token
    assert creds.session_cookie, "the login response sets the session cookie"
    return creds


def test_the_committed_client_matches_the_snapshot() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_client.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


async def test_typed_calls_round_trip(api: Api, client: PlannerApi) -> None:
    await api.add_user("zoe", "admin")
    creds = await sign_in(client, "zoe")

    team = await client.teams_create(creds, body=models.TeamIn(name="Alpha", color="#336699"))
    assert isinstance(team, models.TeamOut)
    assert team.version == 1

    page = await client.teams_list(creds, q="alp", page_size=10)
    assert isinstance(page, models.PageTeamOut)
    assert [t.name for t in page.items] == ["Alpha"]

    renamed = await client.teams_update(
        creds,
        str(team.id),
        body=models.TeamUpdate(name="Beta", color="#336699"),
        if_match=team.version,
    )
    assert renamed.name == "Beta"
    assert renamed.version == 2

    await client.teams_delete(creds, str(team.id), if_match=renamed.version)
    with pytest.raises(ApiError) as caught:
        await client.teams_get(creds, str(team.id))
    assert caught.value.status == 404
    assert caught.value.code == "NOT_FOUND"


async def test_problems_become_api_errors_with_field_messages(api: Api, client: PlannerApi) -> None:
    await api.add_user("yan", "admin")
    creds = await sign_in(client, "yan")
    await client.teams_create(creds, body=models.TeamIn(name="Alpha", color="#336699"))
    with pytest.raises(ApiError) as duplicate:
        await client.teams_create(creds, body=models.TeamIn(name="Alpha", color="#336699"))
    assert duplicate.value.code == "DUPLICATE"
    assert duplicate.value.field_errors == {"name": "Already in use."}

    with pytest.raises(ApiError) as stale:
        team = (await client.teams_list(creds)).items[0]
        await client.teams_update(
            creds, str(team.id), body=models.TeamUpdate(name="X", color="#336699"), if_match=99
        )
    assert stale.value.status == 412
    assert stale.value.extra["currentVersion"] == 1


async def test_changes_without_the_csrf_token_are_refused(api: Api, client: PlannerApi) -> None:
    await api.add_user("xi", "admin")
    creds = await sign_in(client, "xi")
    creds.csrf_token = None
    with pytest.raises(ApiError) as refused:
        await client.teams_create(creds, body=models.TeamIn(name="Alpha", color="#336699"))
    assert refused.value.code == "CSRF_FAILED"


async def test_two_users_interleaved_never_see_each_others_session(
    api: Api, client: PlannerApi
) -> None:
    """One client object serves every request of the web interface, so it must hold no state."""
    await api.add_user("viv", "viewer")
    await api.add_user("wil", "admin")
    viv, wil = await asyncio.gather(sign_in(client, "viv"), sign_in(client, "wil"))
    assert viv.session_cookie != wil.session_cookie

    async def whoami(creds: Credentials) -> str:
        return (await client.auth_me(creds)).username

    names = await asyncio.gather(*[whoami(viv if i % 2 else wil) for i in range(20)])
    assert names == ["wil", "viv"] * 10

    # The viewer cannot write although the administrator signed in on the same client object.
    with pytest.raises(ApiError) as refused:
        await client.teams_create(viv, body=models.TeamIn(name="Alpha", color="#336699"))
    assert refused.value.code == "PERMISSION_DENIED"
    await client.teams_create(wil, body=models.TeamIn(name="Alpha", color="#336699"))

    # A request without credentials stays anonymous.
    with pytest.raises(ApiError) as anonymous:
        await client.auth_me(Credentials())
    assert anonymous.value.status == 401


async def test_logout_clears_the_cookie_held_in_the_credentials(
    api: Api, client: PlannerApi
) -> None:
    await api.add_user("uma", "viewer")
    creds = await sign_in(client, "uma")
    result = await client.auth_logout(creds)
    assert result.redirect_url is None
    assert creds.session_cookie is None
