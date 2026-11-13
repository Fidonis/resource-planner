"""Teams, skills, members and absence types (FR-MD-01..06, DM-01, DM-02, DM-04, NFR-API-03/04)."""

from __future__ import annotations

import uuid
from typing import Any

from tests.api.support import Api, Caller, absence_type_id

TEAMS = "/api/v1/teams"
SKILLS = "/api/v1/skills"
MEMBERS = "/api/v1/members"
TYPES = "/api/v1/absence-types"


async def team(admin: Caller, name: str = "Alpha") -> dict[str, Any]:
    return await admin.create(TEAMS, {"name": name, "color": "#336699"})


async def skill(admin: Caller, key: str = "Backend") -> dict[str, Any]:
    return await admin.create(SKILLS, {"key": key, "name": f"{key} work"})


async def member(
    admin: Caller, team_id: object, name: str = "Ada", **extra: object
) -> dict[str, Any]:
    body = {"teamId": team_id, "displayName": name, **extra}
    return await admin.create(MEMBERS, body)


# --- teams ---------------------------------------------------------------------------------


async def test_team_crud_with_etag_and_member_counts(api: Api) -> None:
    admin = await api.admin()
    created = await admin.post(TEAMS, {"name": "Alpha", "color": "#336699"})
    assert created.status_code == 201
    body = created.json()
    assert created.headers["etag"] == '"1"'
    assert created.headers["location"] == f"/api/v1/teams/{body['id']}"
    assert body["version"] == 1
    assert body["memberCount"] == 0
    assert body["writable"] is True
    assert body["managedFields"] == []

    await member(admin, body["id"])
    listing = (await admin.get(TEAMS)).json()
    assert listing["total"] == 1
    assert listing["items"][0]["memberCount"] == 1

    changed = await admin.put(
        f"{TEAMS}/{body['id']}",
        {"name": "Alpha team", "color": "#112233"},
        headers={"If-Match": '"1"'},
    )
    assert changed.status_code == 200
    assert changed.json()["version"] == 2
    assert changed.json()["updatedAt"] >= body["updatedAt"]
    assert (await admin.get(f"{TEAMS}/{body['id']}")).json()["name"] == "Alpha team"


async def test_team_name_is_unique_and_the_error_names_the_field(api: Api) -> None:
    admin = await api.admin()
    await team(admin, "Alpha")
    response = await admin.post(TEAMS, {"name": "Alpha", "color": "#336699"})
    assert response.status_code == 409
    problem = response.json()
    assert problem["code"] == "DUPLICATE"
    assert problem["errors"] == [
        {"field": "name", "message": "Already in use.", "code": "duplicate"}
    ]
    # The failed insert did not break the next request.
    assert (await admin.post(TEAMS, {"name": "Beta", "color": "#336699"})).status_code == 201


async def test_invalid_input_is_reported_per_field(api: Api) -> None:
    admin = await api.admin()
    response = await admin.post(TEAMS, {"name": "", "color": "blue"})
    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "VALIDATION_ERROR"
    assert {e["field"] for e in problem["errors"]} == {"name", "color"}


async def test_optimistic_locking_if_match_body_version_and_missing_version(api: Api) -> None:
    admin = await api.admin()
    created = await team(admin)
    url = f"{TEAMS}/{created['id']}"
    body = {"name": "Alpha 2", "color": "#336699"}

    changed = await admin.put(url, body, headers={"If-Match": '"1"'})
    assert changed.json()["version"] == 2
    # A request that changes nothing does not create a new version.
    same = await admin.put(url, body, headers={"If-Match": '"2"'})
    assert same.status_code == 200
    assert same.json()["version"] == 2

    stale_header = await admin.put(url, body, headers={"If-Match": '"1"'})
    assert stale_header.status_code == 412
    assert stale_header.json()["code"] == "VERSION_MISMATCH"
    assert stale_header.json()["currentVersion"] == 2

    stale_body = await admin.put(url, {**body, "version": 1})
    assert stale_body.status_code == 409
    assert stale_body.json()["code"] == "VERSION_MISMATCH"

    missing = await admin.put(url, body)
    assert missing.status_code == 428
    assert missing.json()["code"] == "VERSION_REQUIRED"

    assert (await admin.put(url, {**body, "version": 2})).status_code == 200
    bad = await admin.put(url, body, headers={"If-Match": "abc"})
    assert bad.status_code == 400


async def test_two_clients_with_the_same_version_second_one_loses(api: Api) -> None:
    """Acceptance 12 for the master data: the second writer gets 409/412 and must reload."""
    first = await api.admin()
    second = await api.admin()
    created = await team(first)
    url = f"{TEAMS}/{created['id']}"
    seen_by_both = (await first.get(url)).headers["etag"]

    won = await first.put(
        url, {"name": "Renamed", "color": "#336699"}, headers={"If-Match": seen_by_both}
    )
    lost = await second.put(
        url, {"name": "Other", "color": "#336699"}, headers={"If-Match": seen_by_both}
    )
    assert won.status_code == 200
    assert lost.status_code == 412
    assert (await second.get(url)).json()["name"] == "Renamed"


async def test_delete_needs_a_version_and_a_team_with_members_is_blocked(api: Api) -> None:
    admin = await api.admin()
    created = await team(admin)
    url = f"{TEAMS}/{created['id']}"
    ada = await member(admin, created["id"])

    assert (await admin.delete(url)).status_code == 428
    blocked = await admin.delete(url, headers={"If-Match": '"1"'})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "DELETE_BLOCKED"
    assert blocked.json()["blockers"] == [{"kind": "members", "count": 1}]

    assert (await admin.delete(f"{MEMBERS}/{ada['id']}?version=1")).status_code == 204
    assert (await admin.delete(url, headers={"If-Match": '"1"'})).status_code == 204
    gone = await admin.get(url)
    assert gone.status_code == 404
    assert gone.json()["code"] == "NOT_FOUND"


async def test_team_list_filters_sorts_and_pages(api: Api) -> None:
    admin = await api.admin()
    for name in ("Gamma", "alpha", "Beta"):
        await team(admin, name)
    names = [t["name"] for t in (await admin.get(TEAMS)).json()["items"]]
    assert names == ["alpha", "Beta", "Gamma"]  # case-insensitive by default
    descending = (await admin.get(f"{TEAMS}?sort=-name&pageSize=2&page=1")).json()
    assert [t["name"] for t in descending["items"]] == ["Gamma", "Beta"]
    assert descending["total"] == 3
    assert [t["name"] for t in (await admin.get(f"{TEAMS}?q=ALP")).json()["items"]] == ["alpha"]
    assert (await admin.get(f"{TEAMS}?pageSize=500")).status_code == 422


# --- skills --------------------------------------------------------------------------------


async def test_skill_keys_are_unique_ignoring_case(api: Api) -> None:
    admin = await api.admin()
    await skill(admin, "Backend")
    response = await admin.post(SKILLS, {"key": "backend", "name": "Again"})
    assert response.status_code == 409
    assert response.json()["errors"][0]["field"] == "key"
    assert (await admin.post(SKILLS, {"key": "bad/ key!", "name": "x"})).status_code == 422


async def test_skill_in_use_cannot_be_deleted(api: Api) -> None:
    admin = await api.admin()
    alpha = await team(admin)
    backend = await skill(admin)
    ada = await member(admin, alpha["id"], skillIds=[backend["id"]])
    await api.add_assignment(ada["id"], backend["id"])

    blocked = await admin.delete(f"{SKILLS}/{backend['id']}", headers={"If-Match": '"1"'})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "DELETE_BLOCKED"
    kinds = {b["kind"]: b["count"] for b in blocked.json()["blockers"]}
    assert kinds == {"members": 1, "assignments": 1}

    unused = await skill(admin, "Design")
    assert (await admin.delete(f"{SKILLS}/{unused['id']}?version=1")).status_code == 204


# --- members -------------------------------------------------------------------------------


async def test_member_defaults_skills_and_team_name(api: Api) -> None:
    admin = await api.admin()
    alpha = await team(admin)
    backend = await skill(admin)
    created = await member(admin, alpha["id"], email="Ada@Example.test", skillIds=[backend["id"]])
    assert created["allocationPercent"] == 80  # the server default
    assert created["active"] is True
    assert created["teamName"] == "Alpha"
    assert created["skillIds"] == [backend["id"]]

    settings = await admin.put(
        "/api/v1/settings",
        {"planningHorizonWeeks": 4, "defaultAllocationPercent": 60, "locale": "en"},
    )
    assert settings.status_code == 200
    other = await member(admin, alpha["id"], "Bob")
    assert other["allocationPercent"] == 60

    duplicate = await admin.post(
        MEMBERS, {"teamId": alpha["id"], "displayName": "Ada 2", "email": "ada@example.test"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["errors"][0]["field"] == "email"


async def test_member_input_is_checked(api: Api) -> None:
    admin = await api.admin()
    alpha = await team(admin)
    too_low = await admin.post(
        MEMBERS, {"teamId": alpha["id"], "displayName": "Ada", "allocationPercent": 5}
    )
    assert too_low.status_code == 422
    unknown_team = await admin.post(MEMBERS, {"teamId": str(uuid.uuid4()), "displayName": "Ada"})
    assert unknown_team.status_code == 422
    assert unknown_team.json()["errors"][0]["field"] == "teamId"
    unknown_skill = await admin.post(
        MEMBERS, {"teamId": alpha["id"], "displayName": "Ada", "skillIds": [str(uuid.uuid4())]}
    )
    assert unknown_skill.status_code == 422
    assert unknown_skill.json()["errors"][0]["field"] == "skillIds"
    bad_mail = await admin.post(
        MEMBERS, {"teamId": alpha["id"], "displayName": "Ada", "email": "not-an-address"}
    )
    assert bad_mail.status_code == 422


async def test_moving_a_member_keeps_the_assignments(api: Api) -> None:
    """DM-01: a member has exactly one team and a move does not delete assignments."""
    admin = await api.admin()
    alpha, beta = await team(admin, "Alpha"), await team(admin, "Beta")
    backend = await skill(admin)
    ada = await member(admin, alpha["id"], skillIds=[backend["id"]])
    await api.add_assignment(ada["id"], backend["id"])

    moved = await admin.put(
        f"{MEMBERS}/{ada['id']}",
        {"teamId": beta["id"], "displayName": "Ada", "allocationPercent": 80, "active": True},
        headers={"If-Match": '"1"'},
    )
    assert moved.status_code == 200
    assert moved.json()["teamName"] == "Beta"
    assert await api.count_assignments() == 1


async def test_removing_a_skill_reports_the_assignments_and_keeps_them(api: Api) -> None:
    """DM-02: a member that loses a skill keeps the assignments, reported as a conflict."""
    admin = await api.admin()
    alpha = await team(admin)
    backend, design = await skill(admin, "Backend"), await skill(admin, "Design")
    ada = await member(admin, alpha["id"], skillIds=[backend["id"], design["id"]])
    await api.add_assignment(ada["id"], backend["id"], task_key="T-1", person_days="1.5")
    await api.add_assignment(
        ada["id"], backend["id"], task_key="T-1", week=api.week(1), person_days="1.0"
    )
    await api.add_assignment(ada["id"], design["id"], task_key="T-2")

    result = await admin.put(
        f"{MEMBERS}/{ada['id']}/skills", {"skillIds": [design["id"]]}, headers={"If-Match": '"1"'}
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["member"]["skillIds"] == [design["id"]]
    assert body["member"]["version"] == 2  # a skill change counts as a change of the member
    assert body["conflicts"] == [
        {
            "taskId": body["conflicts"][0]["taskId"],
            "taskKey": "T-1",
            "skillId": backend["id"],
            "skillKey": "Backend",
            "assignmentCount": 2,
            "personDays": 2.5,
        }
    ]
    assert await api.count_assignments() == 3  # nothing was deleted

    stale = await admin.put(
        f"{MEMBERS}/{ada['id']}/skills", {"skillIds": []}, headers={"If-Match": '"1"'}
    )
    assert stale.status_code == 412


async def test_member_with_assignments_cannot_be_deleted_but_can_be_deactivated(api: Api) -> None:
    admin = await api.admin()
    alpha = await team(admin)
    backend = await skill(admin)
    ada = await member(admin, alpha["id"], skillIds=[backend["id"]])
    await api.add_assignment(ada["id"], backend["id"])

    blocked = await admin.delete(f"{MEMBERS}/{ada['id']}", headers={"If-Match": '"1"'})
    assert blocked.status_code == 409
    assert blocked.json()["blockers"] == [{"kind": "assignments", "count": 1}]

    off = await admin.put(
        f"{MEMBERS}/{ada['id']}",
        {"teamId": alpha["id"], "displayName": "Ada", "allocationPercent": 80, "active": False},
        headers={"If-Match": '"1"'},
    )
    assert off.status_code == 200
    assert off.json()["active"] is False
    assert (await admin.get(f"{MEMBERS}?active=false")).json()["total"] == 1
    assert (await admin.get(f"{MEMBERS}?active=true")).json()["total"] == 0


async def test_member_filters(api: Api) -> None:
    admin = await api.admin()
    alpha, beta = await team(admin, "Alpha"), await team(admin, "Beta")
    backend = await skill(admin)
    await member(admin, alpha["id"], "Ada", skillIds=[backend["id"]])
    await member(admin, beta["id"], "Bob")

    def names(page: dict[str, Any]) -> list[str]:
        return [m["displayName"] for m in page["items"]]

    assert names((await admin.get(f"{MEMBERS}?teamId={beta['id']}")).json()) == ["Bob"]
    assert names((await admin.get(f"{MEMBERS}?skillId={backend['id']}")).json()) == ["Ada"]
    assert names((await admin.get(f"{MEMBERS}?q=bo")).json()) == ["Bob"]


async def test_viewer_cannot_change_members_acceptance_8(api: Api) -> None:
    admin = await api.admin()
    alpha = await team(admin)
    viewer = await api.as_role("viewer")
    refused = await viewer.post(MEMBERS, {"teamId": alpha["id"], "displayName": "Ada"})
    assert refused.status_code == 403
    assert refused.json()["code"] == "PERMISSION_DENIED"
    assert refused.json()["permission"] == "member:write"
    listed = await viewer.get(MEMBERS)
    assert listed.status_code == 200  # reading is allowed
    assert all(m["writable"] is False for m in listed.json()["items"])


async def test_team_lead_works_only_inside_the_teams_of_the_role(api: Api) -> None:
    admin = await api.admin()
    alpha, beta = await team(admin, "Alpha"), await team(admin, "Beta")
    in_beta = await member(admin, beta["id"], "Bob")
    lead = await api.as_role("team-lead", teams=[uuid.UUID(alpha["id"])])

    own = await lead.post(MEMBERS, {"teamId": alpha["id"], "displayName": "Ada"})
    assert own.status_code == 201
    assert own.json()["writable"] is True
    foreign = await lead.post(MEMBERS, {"teamId": beta["id"], "displayName": "Eve"})
    assert foreign.status_code == 403

    seen = (await lead.get(MEMBERS)).json()["items"]
    assert {m["displayName"]: m["writable"] for m in seen} == {"Ada": True, "Bob": False}

    # Moving needs the right in the old and in the new team.
    away = await lead.put(
        f"{MEMBERS}/{own.json()['id']}",
        {"teamId": beta["id"], "displayName": "Ada", "allocationPercent": 80, "active": True},
        headers={"If-Match": '"1"'},
    )
    assert away.status_code == 403
    pull = await lead.put(
        f"{MEMBERS}/{in_beta['id']}",
        {"teamId": alpha["id"], "displayName": "Bob", "allocationPercent": 80, "active": True},
        headers={"If-Match": '"1"'},
    )
    assert pull.status_code == 403
    assert (await lead.delete(f"{MEMBERS}/{in_beta['id']}?version=1")).status_code == 403
    # Teams and skills are not part of what a team lead may change.
    assert (await lead.post(TEAMS, {"name": "New", "color": "#336699"})).status_code == 403


# --- absence types -------------------------------------------------------------------------


async def test_default_absence_types_exist_in_a_fixed_order(api: Api) -> None:
    admin = await api.admin()
    items = (await admin.get(TYPES)).json()["items"]
    assert [t["key"] for t in items] == ["vacation", "sick", "training", "public-holiday"]
    assert all(t["reducesCapacity"] for t in items)
    assert {t["letter"] for t in items} == {"V", "S", "T", "H"}


async def test_absence_type_crud_and_a_type_in_use_is_only_deactivated(api: Api) -> None:
    admin = await api.admin()
    created = await admin.post(
        TYPES,
        {
            "key": "on-call",
            "name": "On-call",
            "color": "#5f6b78",
            "letter": "O",
            "reducesCapacity": False,
        },
    )
    assert created.status_code == 201
    on_call = created.json()
    assert on_call["reducesCapacity"] is False
    assert on_call["absenceCount"] == 0

    assert (await admin.post(TYPES, {"key": "On Call!", "name": "x"})).status_code == 422
    assert (await admin.post(TYPES, {"key": "on-call", "name": "again"})).status_code == 409

    ada_team = await team(admin)
    ada = await member(admin, ada_team["id"])
    await api.add_absence(on_call["id"], ada["id"])
    assert (await admin.get(f"{TYPES}/{on_call['id']}")).json()["absenceCount"] == 1

    blocked = await admin.delete(f"{TYPES}/{on_call['id']}?version=1")
    assert blocked.status_code == 409
    assert blocked.json()["blockers"] == [{"kind": "absences", "count": 1}]

    deactivated = await admin.put(
        f"{TYPES}/{on_call['id']}",
        {
            "key": "on-call",
            "name": "On-call",
            "color": "#5f6b78",
            "letter": "O",
            "reducesCapacity": False,
            "active": False,
            "sortOrder": 50,
            "version": 1,
        },
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False
    assert (await admin.get(f"{TYPES}?active=false")).json()["total"] == 1

    holiday = await absence_type_id(api, "training")
    assert (await admin.delete(f"{TYPES}/{holiday}?version=1")).status_code == 204


# --- settings and audit --------------------------------------------------------------------


async def test_settings_can_be_read_by_anyone_signed_in_and_written_by_admins(api: Api) -> None:
    viewer = await api.as_role("viewer")
    current = (await viewer.get("/api/v1/settings")).json()
    assert current == {
        "planningHorizonWeeks": 4,
        "defaultAllocationPercent": 80,
        "locale": "en",
        "authLocalEnabled": True,
        "authOidcEnabled": False,
        "demoSeedEnabled": True,
    }
    new = {"planningHorizonWeeks": 8, "defaultAllocationPercent": 70, "locale": "de"}
    assert (await viewer.put("/api/v1/settings", new)).status_code == 403
    admin = await api.admin()
    assert (await admin.put("/api/v1/settings", new)).status_code == 200
    assert (await viewer.get("/api/v1/settings")).json()["planningHorizonWeeks"] == 8
    assert (
        await admin.put("/api/v1/settings", {**new, "planningHorizonWeeks": 40})
    ).status_code == 422


async def test_every_change_is_audited_with_the_calling_client(api: Api) -> None:
    admin = await api.admin()
    created = await admin.post(
        TEAMS, {"name": "Alpha", "color": "#336699"}, client_name="planner-ui"
    )
    team_id = created.json()["id"]
    await admin.put(
        f"{TEAMS}/{team_id}", {"name": "Alpha 2", "color": "#336699"}, headers={"If-Match": '"1"'}
    )
    await admin.delete(f"{TEAMS}/{team_id}?version=2")

    entries = (await admin.get(f"/api/v1/audit-log?entityType=team&entityId={team_id}")).json()
    assert entries["total"] == 3
    delete, update, create = entries["items"]  # newest first
    assert (create["action"], create["via"], create["clientName"]) == (
        "create",
        "session",
        "planner-ui",
    )
    assert create["before"] is None
    assert create["after"]["name"] == "Alpha"
    assert update["before"]["name"] == "Alpha"
    assert update["after"]["name"] == "Alpha 2"
    assert update["clientName"] == "tests"
    assert delete["action"] == "delete"
    assert admin.me is not None
    assert delete["actorName"] == admin.me["username"]

    viewer = await api.as_role("viewer")
    assert (await viewer.get("/api/v1/audit-log")).status_code == 403


async def test_a_failed_change_leaves_no_audit_entry(api: Api) -> None:
    admin = await api.admin()
    await team(admin, "Alpha")
    before = (await admin.get("/api/v1/audit-log?entityType=team")).json()["total"]
    assert (await admin.post(TEAMS, {"name": "Alpha", "color": "#336699"})).status_code == 409
    after = (await admin.get("/api/v1/audit-log?entityType=team")).json()["total"]
    assert before == after == 1


# --- shape of errors and of the document ---------------------------------------------------


async def test_unknown_routes_and_ids_answer_with_problem_documents(api: Api) -> None:
    admin = await api.admin()
    nothing = await admin.get("/api/v1/nothing-here")
    assert nothing.status_code == 404
    assert nothing.headers["content-type"].startswith("application/problem+json")
    missing = await admin.get(f"{TEAMS}/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"
    malformed = await admin.get(f"{TEAMS}/not-a-uuid")
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "VALIDATION_ERROR"


async def test_health_endpoints(api: Api) -> None:
    anonymous = api.anonymous()
    assert (await anonymous.get("/health/live")).json() == {"status": "ok"}
    assert (await anonymous.get("/health/ready")).json() == {"status": "ok"}
