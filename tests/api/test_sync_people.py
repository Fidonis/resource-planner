"""Directory and absence syncs: file providers and Keycloak (§9.5, §9.6)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.api.support import FIRST_WEEK, Api, Caller
from tests.api.sync_support import INTEGRATIONS, counts, integration, sync, write
from tests.api.world import ABSENCES, ASSIGNMENTS, MEMBERS, TEAMS, member, skill, task, team, week

from planner_api.providers.keycloak import KeycloakDirectory

PEOPLE = (
    "Name;Email;Active;Groups\r\n"
    "Ada Lovelace;ada@example.test;yes;Backend\r\n"
    "Bob Builder;bob@example.test;yes;Frontend|Backend\r\n"
    "Cy Idle;cy@example.test;no;Backend\r\n"
    "Dee Nogroup;dee@example.test;yes;\r\n"
)
DIRECTORY_MAPPING = {
    "groupTeams": ["Frontend=Team Front", "Backend=Team Back"],
    "createTeams": True,
    "defaultTeam": "Unassigned",
}


async def members_by_name(admin: Caller) -> dict[str, dict[str, Any]]:
    response = await admin.get(MEMBERS, params={"pageSize": 200})
    assert response.status_code == 200, response.text
    return {m["displayName"]: m for m in response.json()["items"]}


async def run_log(admin: Caller, integration_id: str, run: dict[str, Any]) -> list[str]:
    detail = (await admin.get(f"{INTEGRATIONS}/{integration_id}/runs/{run['id']}")).json()
    return [f"{e['level']}: {e['message']}" for e in detail["log"]]


# --- directory ---------------------------------------------------------------------------


async def test_people_become_members_in_the_team_of_their_most_important_group(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await sync_api.admin()
    await team(admin, "Unassigned")
    path = write(imports_dir, "people.csv", PEOPLE)
    made = await integration(admin, "directory", path, mapping=DIRECTORY_MAPPING)

    first = await sync(admin, made["id"])
    assert counts(first) == (4, 0, 0, 0)
    found = await members_by_name(admin)
    assert found["Ada Lovelace"]["teamName"] == "Team Back"
    assert found["Bob Builder"]["teamName"] == "Team Front"  # Frontend is listed first
    assert found["Dee Nogroup"]["teamName"] == "Unassigned"  # the default team
    assert found["Cy Idle"]["active"] is False
    assert found["Ada Lovelace"]["managedFields"] == ["displayName", "email"]

    teams = (await admin.get(TEAMS)).json()["items"]
    created = next(t for t in teams if t["name"] == "Team Back")
    assert created["externalSource"] == made["id"]

    second = await sync(admin, made["id"])
    assert counts(second) == (0, 0, 4, 0)


async def test_a_change_made_here_survives_the_next_sync(sync_api: Api, imports_dir: Path) -> None:
    admin = await sync_api.admin()
    await team(admin, "Unassigned")
    path = write(imports_dir, "people.csv", PEOPLE)
    made = await integration(admin, "directory", path, mapping=DIRECTORY_MAPPING)
    await sync(admin, made["id"])
    ada = (await members_by_name(admin))["Ada Lovelace"]
    elsewhere = await team(admin, "Elsewhere")
    moved = await admin.put(
        f"{MEMBERS}/{ada['id']}",
        {
            "version": ada["version"],
            "teamId": elsewhere["id"],
            "displayName": ada["displayName"],
            "email": ada["email"],
            "allocationPercent": 60,
            "active": True,
        },
    )
    assert moved.status_code == 200, moved.text

    # the source now puts Ada in Frontend, but somebody moved her on purpose
    write(
        imports_dir,
        "people.csv",
        PEOPLE.replace(
            "Ada Lovelace;ada@example.test;yes;Backend",
            "Ada Lovelace;ada@example.test;yes;Frontend",
        ),
    )
    run = await sync(admin, made["id"])
    assert counts(run)[3] == 0
    after = (await members_by_name(admin))["Ada Lovelace"]
    assert after["teamName"] == "Elsewhere" and after["allocationPercent"] == 60


async def test_a_manual_member_with_the_same_address_is_taken_over(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await sync_api.admin()
    own = await team(admin, "Own team")
    await team(admin, "Unassigned")
    manual = await member(admin, own["id"], "A. Lovelace", email="ada@example.test")
    path = write(imports_dir, "people.csv", PEOPLE)
    made = await integration(admin, "directory", path, mapping=DIRECTORY_MAPPING)
    run = await sync(admin, made["id"])
    assert counts(run)[:2] == (3, 1)
    adopted = (await admin.get(f"{MEMBERS}/{manual['id']}")).json()
    assert adopted["displayName"] == "Ada Lovelace"
    assert adopted["externalSource"] == made["id"]
    assert adopted["teamName"] == "Team Back"  # a listed group names the team


async def test_who_is_imported_can_be_narrowed_to_groups(sync_api: Api, imports_dir: Path) -> None:
    admin = await sync_api.admin()
    path = write(imports_dir, "people.csv", PEOPLE)
    made = await integration(
        admin,
        "directory",
        path,
        mapping={
            "includeGroups": ["Frontend"],
            "groupTeams": ["Frontend=Team Front"],
            "createTeams": True,
        },
    )
    run = await sync(admin, made["id"])
    assert counts(run) == (1, 0, 0, 0)
    assert set(await members_by_name(admin)) == {"Bob Builder"}


async def test_a_person_without_any_team_is_reported_not_imported(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await sync_api.admin()
    path = write(imports_dir, "people.csv", PEOPLE)
    made = await integration(
        admin, "directory", path, mapping={"groupTeams": ["Backend=Team Back"], "createTeams": True}
    )
    run = await sync(admin, made["id"])
    assert run["status"] == "partial"
    assert counts(run)[0] == 3 and counts(run)[3] == 1  # Bob is in Backend too
    assert any("no team" in line.lower() for line in await run_log(admin, made["id"], run))


async def test_somebody_who_left_the_directory_is_deactivated_and_their_bookings_reported(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await sync_api.admin()
    await team(admin, "Unassigned")
    backend = await skill(admin, "Backend")
    path = write(imports_dir, "people.csv", PEOPLE)
    made = await integration(
        admin, "directory", path, mapping={**DIRECTORY_MAPPING, "groupSkills": ["Backend=Backend"]}
    )
    await sync(admin, made["id"])
    ada = (await members_by_name(admin))["Ada Lovelace"]
    assert ada["skillIds"] == [backend["id"]]
    work = await task(admin, "T-9", {backend["id"]: "3"})
    booked = await admin.post(
        ASSIGNMENTS,
        {
            "taskId": work["id"],
            "skillId": backend["id"],
            "memberId": ada["id"],
            "weekStart": week(),
            "personDays": 2,
        },
    )
    assert booked.status_code == 201, booked.text

    write(
        imports_dir,
        "people.csv",
        PEOPLE.replace("Ada Lovelace;ada@example.test;yes;Backend\r\n", ""),
    )
    run = await sync(admin, made["id"])
    assert counts(run)[:2] == (0, 1)
    gone = (await members_by_name(admin))["Ada Lovelace"]
    assert gone["active"] is False
    assert any("assignment" in line for line in await run_log(admin, made["id"], run))
    assert (await admin.get(ASSIGNMENTS, params={"memberId": ada["id"]})).json()["total"] == 1


# --- absences ----------------------------------------------------------------------------

ABSENCES_FILE = (
    "ExternalId;Person;From;To;Type;Note\r\n"
    "A-1;ada@example.test;2030-01-08;2030-01-09;URLAUB;Ski\r\n"
    "A-2;Bob Builder;10.01.2030;10.01.2030;KRANK;\r\n"
    "A-3;*;2030-01-14;2030-01-14;FEIERTAG;Company day\r\n"
)
TYPE_MAP = {"URLAUB": "vacation", "KRANK": "sick", "FEIERTAG": "public-holiday"}
ABSENCE_MAPPING = {"typeMap": TYPE_MAP, "lookaheadDays": 3650}


async def absence_rows(admin: Caller) -> list[dict[str, Any]]:
    response = await admin.get(ABSENCES, params={"pageSize": 200})
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def two_members(admin: Caller) -> None:
    t = await team(admin, "Alpha")
    await member(admin, t["id"], "Ada Lovelace", email="ada@example.test")
    await member(admin, t["id"], "Bob Builder", email="bob@example.test")


async def test_absences_are_matched_by_person_and_mapped_type(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await sync_api.admin()
    await two_members(admin)
    path = write(imports_dir, "absences.csv", ABSENCES_FILE)
    made = await integration(admin, "absence_source", path, mapping=ABSENCE_MAPPING)

    first = await sync(admin, made["id"])
    assert counts(first) == (3, 0, 0, 0)
    rows = {r["memberName"] or "*": r for r in await absence_rows(admin)}
    assert (
        rows["Ada Lovelace"]["typeKey"] == "vacation" and rows["Ada Lovelace"]["workingDays"] == 2
    )
    assert rows["Bob Builder"]["typeKey"] == "sick"
    assert rows["*"]["memberId"] is None
    assert counts(await sync(admin, made["id"])) == (0, 0, 3, 0)

    # absences with a source are read-only in the planner
    locked = await admin.put(
        f"{ABSENCES}/{rows['Ada Lovelace']['id']}",
        {
            "version": rows["Ada Lovelace"]["version"],
            "memberId": rows["Ada Lovelace"]["memberId"],
            "absenceTypeId": rows["Ada Lovelace"]["absenceTypeId"],
            "startDate": "2030-01-08",
            "endDate": "2030-01-10",
        },
    )
    assert locked.status_code == 409 and locked.json()["code"] == "MANAGED_BY_SOURCE"


async def test_a_sync_replaces_only_the_absences_of_its_own_integration(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await sync_api.admin()
    await two_members(admin)
    types = (await admin.get("/api/v1/absence-types")).json()["items"]
    training = next(t for t in types if t["key"] == "training")
    ada = (await members_by_name(admin))["Ada Lovelace"]
    manual = await admin.create(
        ABSENCES,
        {
            "memberId": ada["id"],
            "absenceTypeId": training["id"],
            "startDate": "2030-01-08",
            "endDate": "2030-01-08",
        },
    )
    path = write(imports_dir, "absences.csv", ABSENCES_FILE)
    made = await integration(admin, "absence_source", path, mapping=ABSENCE_MAPPING)
    await sync(admin, made["id"])

    shorter = ABSENCES_FILE.replace("2030-01-08;2030-01-09;URLAUB", "2030-01-08;2030-01-08;URLAUB")
    shorter = shorter.replace("A-2;Bob Builder;10.01.2030;10.01.2030;KRANK;\r\n", "")
    write(imports_dir, "absences.csv", shorter)
    run = await sync(admin, made["id"])
    assert counts(run)[:3] == (0, 2, 1)  # one changed, one removed
    after = await absence_rows(admin)
    assert len(after) == 3  # manual + Ada's + the company day
    assert manual["id"] in {r["id"] for r in after}


async def test_unknown_people_and_unmapped_types_are_reported(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await sync_api.admin()
    await two_members(admin)
    text = ABSENCES_FILE + "A-4;nobody@example.test;2030-02-01;2030-02-01;URLAUB;\r\n"
    text += "A-5;ada@example.test;2030-02-05;2030-02-05;MYSTERY;\r\n"
    path = write(imports_dir, "absences.csv", text)
    made = await integration(admin, "absence_source", path, mapping=ABSENCE_MAPPING)
    run = await sync(admin, made["id"])
    assert run["status"] == "partial" and counts(run)[0] == 3 and counts(run)[3] == 2
    log = await run_log(admin, made["id"], run)
    assert any("No member" in line for line in log) and any("MYSTERY" in line for line in log)
    # an unreadable record keeps the run from closing what it could not see
    assert any("nothing was closed" in line for line in log)


# --- Keycloak ----------------------------------------------------------------------------

USERS = [
    {
        "id": "u1",
        "username": "ada",
        "firstName": "Ada",
        "lastName": "Lovelace",
        "email": "ada@example.test",
        "enabled": True,
    },
    {
        "id": "u2",
        "username": "bob",
        "firstName": "Bob",
        "lastName": "Builder",
        "email": "bob@example.test",
        "enabled": True,
    },
    {
        "id": "u3",
        "username": "cy",
        "firstName": "",
        "lastName": "",
        "email": None,
        "enabled": False,
    },
    {"id": "u4", "username": "service-account-planner-sync", "enabled": True},
]
GROUPS = {
    "": [
        {"id": "g1", "name": "Backend", "path": "/Backend", "subGroupCount": 1},
        {"id": "g2", "name": "Frontend", "path": "/Frontend", "subGroupCount": 0},
    ],
    "g1/children": [{"id": "g3", "name": "API", "path": "/Backend/API", "subGroupCount": 0}],
}
MEMBERS_OF = {"g1": [USERS[1]], "g2": [USERS[1]], "g3": [USERS[0]]}


def keycloak_handler(secret: str = "right") -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        path, query = request.url.path, dict(request.url.params)
        if path.endswith("/protocol/openid-connect/token"):
            ok = f"client_secret={secret}" in request.content.decode()
            return httpx.Response(200, json={"access_token": "t"}) if ok else httpx.Response(401)
        assert request.headers["authorization"] == "Bearer t"
        base = "/admin/realms/planner/"
        assert path.startswith(base), path
        rest = path[len(base) :]
        first, size = int(query.get("first", 0)), int(query.get("max", 100))
        if rest == "users/count":
            return httpx.Response(200, json=len(USERS))
        if rest == "users":
            return httpx.Response(200, json=USERS[first : first + size])
        if rest == "groups":
            return httpx.Response(200, json=GROUPS[""][first : first + size])
        if rest.endswith("/children"):
            return httpx.Response(200, json=GROUPS[rest.removeprefix("groups/")])
        if rest.startswith("groups/") and rest.endswith("/members"):
            return httpx.Response(200, json=MEMBERS_OF.get(rest.split("/")[1], []))
        if rest == "roles/rp-planner/users":
            return httpx.Response(200, json=[USERS[0]])
        return httpx.Response(404)

    return handle


@pytest.fixture
def keycloak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        KeycloakDirectory, "transport", httpx.MockTransport(keycloak_handler()), raising=False
    )


async def keycloak_integration(
    admin: Caller, mapping: dict[str, Any], **config: Any
) -> dict[str, Any]:
    return await admin.create(
        INTEGRATIONS,
        {
            "name": "Keycloak",
            "type": "directory",
            "providerKey": "keycloak",
            "enabled": True,
            "config": {
                "baseUrl": "http://keycloak.test:8080",
                "realm": "planner",
                "clientId": "planner-sync",
                "clientSecret": "right",
                **config,
            },
            "mapping": mapping,
        },
    )


@pytest.mark.usefixtures("keycloak")
async def test_keycloak_groups_become_teams_and_technical_accounts_stay_out(
    sync_api: Api,
) -> None:
    admin = await sync_api.admin()
    made = await keycloak_integration(
        admin,
        {
            "groupTeams": ["Frontend=Team Front", "Backend=Team Back"],
            "createTeams": True,
            "defaultTeam": "Unassigned",
        },
    )
    await team(admin, "Unassigned")
    tested = (await admin.post(f"{INTEGRATIONS}/{made['id']}/test")).json()
    assert tested["ok"] is True and "4 users" in tested["message"]

    run = await sync(admin, made["id"])
    assert counts(run) == (3, 0, 0, 0)
    found = await members_by_name(admin)
    assert set(found) == {"Ada Lovelace", "Bob Builder", "cy"}
    assert found["Ada Lovelace"]["teamName"] == "Team Back"  # via the subgroup /Backend/API
    assert found["Bob Builder"]["teamName"] == "Team Front"  # Frontend wins over Backend
    assert found["cy"]["teamName"] == "Unassigned" and found["cy"]["active"] is False
    assert counts(await sync(admin, made["id"])) == (0, 0, 3, 0)


@pytest.mark.usefixtures("keycloak")
async def test_keycloak_roles_can_select_who_is_imported(sync_api: Api) -> None:
    admin = await sync_api.admin()
    made = await keycloak_integration(
        admin,
        {"includeRoles": ["rp-planner"], "defaultTeam": "Everyone", "createTeams": False},
        roles=["rp-planner"],
    )
    await team(admin, "Everyone")
    run = await sync(admin, made["id"])
    assert counts(run) == (1, 0, 0, 0)
    assert set(await members_by_name(admin)) == {"Ada Lovelace"}


async def test_keycloak_problems_are_told_in_plain_words(
    sync_api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = await sync_api.admin()
    monkeypatch.setattr(
        KeycloakDirectory,
        "transport",
        httpx.MockTransport(keycloak_handler(secret="other")),
        raising=False,
    )
    made = await keycloak_integration(admin, {})
    tested = (await admin.post(f"{INTEGRATIONS}/{made['id']}/test")).json()
    assert tested["ok"] is False and "client secret" in tested["message"]
    run = await sync(admin, made["id"])
    assert run["status"] == "failed"
    assert "right" not in json.dumps(run)  # the secret appears nowhere
    assert (await admin.get(f"{INTEGRATIONS}/{made['id']}")).json()["lastSyncStatus"] == "failed"
    _ = FIRST_WEEK
