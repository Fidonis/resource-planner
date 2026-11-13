"""Tasks and efforts (FR-TASK-01 to 06, DM-03, DM-04, acceptance case 11)."""

from __future__ import annotations

import uuid

import httpx
from tests.api.support import Api
from tests.api.world import (
    AUDIT,
    TASKS,
    assign,
    booked,
    member,
    skill,
    task,
    team,
    week,
)

from planner_api.models import Task


async def test_a_task_carries_its_effort_per_skill(api: Api) -> None:
    admin = await api.admin()
    backend, frontend = await skill(admin, "Backend"), await skill(admin, "Frontend")
    owner = await team(admin, "Beta")
    created = await admin.post(
        TASKS,
        {
            "key": "SHOP-101",
            "title": "Checkout",
            "ownerTeamId": owner["id"],
            "efforts": [
                {"skillId": backend["id"], "personDays": 3},
                {"skillId": frontend["id"], "personDays": 1.5},
            ],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert created.headers["etag"] == '"1"'
    assert body["ownerTeamName"] == "Beta"
    assert body["status"] == "open"
    assert body["totalEffort"] == 4.5
    assert body["totalOpen"] == 4.5
    assert body["plannedPercent"] == 0
    assert [(e["skillKey"], e["personDays"], e["open"]) for e in body["efforts"]] == [
        ("Backend", 3, 3),
        ("Frontend", 1.5, 1.5),
    ]
    assert body["managedFields"] == []
    assert body["writable"] is True


async def test_the_key_is_unique_among_manual_tasks_and_names_the_field(api: Api) -> None:
    admin = await api.admin()
    await task(admin, "T-1")
    response = await admin.post(TASKS, {"key": "T-1", "title": "Again"})
    assert response.status_code == 409
    assert response.json()["code"] == "DUPLICATE"
    assert response.json()["errors"][0]["field"] == "key"


async def test_efforts_are_checked(api: Api) -> None:
    admin = await api.admin()
    backend = await skill(admin)
    twice = await admin.post(
        TASKS,
        {
            "key": "T-1",
            "title": "x",
            "efforts": [
                {"skillId": backend["id"], "personDays": 1},
                {"skillId": backend["id"], "personDays": 2},
            ],
        },
    )
    assert twice.status_code == 422
    assert twice.json()["errors"][0] == {
        "field": "efforts",
        "message": "A skill can appear only once.",
        "code": "duplicate",
    }
    unknown = await admin.post(
        TASKS,
        {
            "key": "T-2",
            "title": "x",
            "efforts": [{"skillId": str(uuid.uuid4()), "personDays": 1}],
        },
    )
    assert unknown.status_code == 422
    assert unknown.json()["errors"][0]["code"] == "not_found"
    for days in (0, -1, 12345678):
        bad = await admin.post(
            TASKS,
            {
                "key": "T-3",
                "title": "x",
                "efforts": [{"skillId": backend["id"], "personDays": days}],
            },
        )
        assert bad.status_code == 422, days
    missing_team = await admin.post(
        TASKS, {"key": "T-4", "title": "x", "ownerTeamId": str(uuid.uuid4())}
    )
    assert missing_team.status_code == 422
    assert missing_team.json()["errors"][0]["field"] == "ownerTeamId"


async def test_changing_a_task_with_and_without_efforts(api: Api) -> None:
    admin = await api.admin()
    backend, qa = await skill(admin, "Backend"), await skill(admin, "QA")
    created = await task(admin, "T-1", {backend["id"]: "3"})
    renamed = await admin.put(
        f"{TASKS}/{created['id']}",
        {"key": "T-1", "title": "New title", "status": "open"},
        headers={"If-Match": '"1"'},
    )
    assert renamed.status_code == 200
    assert renamed.json()["version"] == 2
    assert [e["skillKey"] for e in renamed.json()["efforts"]] == ["Backend"], "efforts kept"

    replaced = await admin.put(
        f"{TASKS}/{created['id']}",
        {
            "key": "T-1",
            "title": "New title",
            "efforts": [{"skillId": qa["id"], "personDays": 2}],
        },
        headers={"If-Match": '"2"'},
    )
    assert replaced.status_code == 200
    assert [e["skillKey"] for e in replaced.json()["efforts"]] == ["QA"]
    assert replaced.json()["version"] == 3

    only_efforts = await admin.put(
        f"{TASKS}/{created['id']}/efforts",
        {"efforts": [{"skillId": qa["id"], "personDays": 4}], "version": 3},
    )
    assert only_efforts.status_code == 200
    assert only_efforts.json()["totalEffort"] == 4
    assert only_efforts.json()["version"] == 4, "an effort change versions the task"


async def test_a_stale_version_is_refused(api: Api) -> None:
    admin = await api.admin()
    created = await task(admin)
    body = {"key": "T-1", "title": "Changed"}
    ok = await admin.put(f"{TASKS}/{created['id']}", body, headers={"If-Match": '"1"'})
    assert ok.status_code == 200
    stale_header = await admin.put(f"{TASKS}/{created['id']}", body, headers={"If-Match": '"1"'})
    assert stale_header.status_code == 412
    assert stale_header.json()["code"] == "VERSION_MISMATCH"
    stale_body = await admin.put(f"{TASKS}/{created['id']}", {**body, "version": 1})
    assert stale_body.status_code == 409
    missing = await admin.put(f"{TASKS}/{created['id']}", body)
    assert missing.status_code == 428


async def test_dm03_an_effort_below_the_assigned_sum_is_refused(api: Api) -> None:
    admin = await api.admin()
    backend = await skill(admin)
    team_a = await team(admin)
    ada = await member(admin, team_a["id"], "Ada", (backend["id"],))
    created = await task(admin, "T-1", {backend["id"]: "5"})
    await booked(admin, created["id"], backend["id"], ada["id"], week(), 2)
    await booked(admin, created["id"], backend["id"], ada["id"], week(1), 2)

    refused = await admin.put(
        f"{TASKS}/{created['id']}/efforts",
        {"efforts": [{"skillId": backend["id"], "personDays": 3}], "version": 1},
    )
    assert refused.status_code == 409
    problem = refused.json()
    assert problem["code"] == "EFFORT_BELOW_ASSIGNED"
    assert problem["conflicts"] == [
        {"skillId": backend["id"], "skillKey": "Backend", "assigned": 4, "requested": 3}
    ]
    removing = await admin.put(f"{TASKS}/{created['id']}/efforts", {"efforts": [], "version": 1})
    assert removing.status_code == 409
    assert (await admin.get(f"{TASKS}/{created['id']}")).json()["totalAssigned"] == 4


async def test_dm03_force_removes_the_excess_latest_weeks_first_and_audits_it(api: Api) -> None:
    admin = await api.admin()
    backend = await skill(admin)
    ada = await member(admin, (await team(admin))["id"], "Ada", (backend["id"],))
    created = await task(admin, "T-1", {backend["id"]: "5"})
    for offset, days in ((0, 2), (1, 2), (2, 1)):
        await booked(admin, created["id"], backend["id"], ada["id"], week(offset), days)

    forced = await admin.put(
        f"{TASKS}/{created['id']}/efforts",
        {"efforts": [{"skillId": backend["id"], "personDays": 3}], "version": 1},
        params={"force": "true"},
    )
    assert forced.status_code == 200, forced.text
    body = forced.json()
    assert body["totalAssigned"] == 3
    assert body["efforts"][0]["open"] == 0

    rows = (await admin.get("/api/v1/assignments", params={"sort": "weekStart"})).json()["items"]
    assert [(r["weekStart"], r["personDays"]) for r in rows] == [(week(0), 2), (week(1), 1)]

    entries = (
        await admin.get(AUDIT, params={"entityType": "task", "action": "update-efforts"})
    ).json()["items"]
    assert len(entries) == 1
    assert "reduced or removed" in entries[0]["note"]
    removed = entries[0]["after"]["removed_assignments"]
    assert sorted(r["removed"] for r in removed) == [1, 1]


async def test_a_task_with_assignments_cannot_be_deleted(api: Api) -> None:
    admin = await api.admin()
    backend = await skill(admin)
    ada = await member(admin, (await team(admin))["id"], "Ada", (backend["id"],))
    used = await task(admin, "T-1", {backend["id"]: "2"})
    free = await task(admin, "T-2")
    await booked(admin, used["id"], backend["id"], ada["id"], week(), 1)

    refused = await admin.delete(f"{TASKS}/{used['id']}", params={"version": 1})
    assert refused.status_code == 409
    assert refused.json()["code"] == "DELETE_BLOCKED"
    assert refused.json()["blockers"] == [{"kind": "assignments", "count": 1}]
    assert (await admin.delete(f"{TASKS}/{free['id']}", params={"version": 1})).status_code == 204
    assert (await admin.get(f"{TASKS}/{free['id']}")).status_code == 404


async def test_closing_a_task_keeps_its_assignments(api: Api) -> None:
    admin = await api.admin()
    backend = await skill(admin)
    ada = await member(admin, (await team(admin))["id"], "Ada", (backend["id"],))
    created = await task(admin, "T-1", {backend["id"]: "2"})
    await booked(admin, created["id"], backend["id"], ada["id"], week(), 2)
    closed = await admin.put(
        f"{TASKS}/{created['id']}",
        {"key": "T-1", "title": "x", "status": "done"},
        headers={"If-Match": '"1"'},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "done"
    assert closed.json()["totalAssigned"] == 2
    assert (await admin.get("/api/v1/assignments")).json()["total"] == 1


async def test_the_list_filters_and_pages(api: Api) -> None:
    admin = await api.admin()
    backend, qa = await skill(admin, "Backend"), await skill(admin, "QA")
    alpha, beta = await team(admin, "Alpha"), await team(admin, "Beta")
    await task(admin, "A-1", {backend["id"]: "1"}, ownerTeamId=alpha["id"], title="Login page")
    await task(admin, "A-2", {qa["id"]: "1"}, ownerTeamId=beta["id"], title="Logout")
    await task(admin, "B-1", {backend["id"]: "1"}, ownerTeamId=beta["id"], status="done")

    def keys(response: httpx.Response) -> list[str]:
        return [t["key"] for t in response.json()["items"]]

    assert keys(await admin.get(TASKS)) == ["A-1", "A-2", "B-1"]
    assert keys(await admin.get(TASKS, params={"q": "log"})) == ["A-1", "A-2"]
    assert keys(await admin.get(TASKS, params={"teamId": beta["id"]})) == ["A-2", "B-1"]
    assert keys(await admin.get(TASKS, params={"status": "done"})) == ["B-1"]
    assert keys(await admin.get(TASKS, params={"skillId": qa["id"]})) == ["A-2"]
    assert keys(await admin.get(TASKS, params={"source": "manual"})) == ["A-1", "A-2", "B-1"]
    assert keys(await admin.get(TASKS, params={"source": "external"})) == []
    page = await admin.get(TASKS, params={"pageSize": 2, "page": 2, "sort": "-key"})
    assert keys(page) == ["A-1"]
    assert page.json()["total"] == 3
    assert (await admin.get(TASKS, params={"status": "bogus"})).status_code == 422


async def test_acceptance_11_the_fields_an_integration_owns_are_locked(api: Api) -> None:
    admin = await api.admin()
    qa = await skill(admin, "QA")
    async with api.factory() as session:
        imported = Task(
            key="JIRA-1",
            title="From Jira",
            external_source="jira",
            external_id="10001",
            external_url="https://jira.example.test/browse/JIRA-1",
            locked_fields=["title", "status", "efforts"],
        )
        session.add(imported)
        await session.commit()
        task_id = str(imported.id)

    shown = (await admin.get(f"{TASKS}/{task_id}")).json()
    assert shown["managedFields"] == ["key", "title", "status", "efforts"]
    assert shown["externalUrl"].endswith("JIRA-1")
    assert (await admin.get(TASKS, params={"source": "jira"})).json()["total"] == 1

    base = {"key": "JIRA-1", "title": "From Jira", "status": "open", "version": 1}
    title = await admin.put(f"{TASKS}/{task_id}", {**base, "title": "Mine"})
    assert title.status_code == 409
    assert title.json()["code"] == "FIELD_LOCKED"
    assert title.json()["errors"][0]["field"] == "title"
    efforts = await admin.put(
        f"{TASKS}/{task_id}/efforts",
        {"efforts": [{"skillId": qa["id"], "personDays": 1}], "version": 1},
    )
    assert efforts.status_code == 409
    assert efforts.json()["code"] == "FIELD_LOCKED"

    # what the integration does not own stays editable
    described = await admin.put(
        f"{TASKS}/{task_id}", {**base, "description": "Notes", "ownerTeamId": None}
    )
    assert described.status_code == 200
    assert described.json()["description"] == "Notes"

    # an administrator can hand a field back, but cannot lock one
    unlock = await admin.put(
        f"{TASKS}/{task_id}", {**base, "version": 2, "lockedFields": ["status", "efforts"]}
    )
    assert unlock.status_code == 200
    assert unlock.json()["lockedFields"] == ["status", "efforts"]
    assert (
        await admin.put(f"{TASKS}/{task_id}", {**base, "version": 3, "title": "Mine"})
    ).status_code == 200
    lock = await admin.put(
        f"{TASKS}/{task_id}",
        {**base, "version": 4, "title": "Mine", "lockedFields": ["status", "efforts", "title"]},
    )
    assert lock.status_code == 422
    planner = await api.as_role("planner")
    refused = await planner.put(
        f"{TASKS}/{task_id}",
        {**base, "version": 4, "title": "Mine", "lockedFields": []},
    )
    assert refused.status_code == 403, "only integration administrators unlock fields"


async def test_acceptance_8_a_viewer_reads_tasks_but_cannot_change_them(api: Api) -> None:
    admin = await api.admin()
    created = await task(admin)
    viewer = await api.as_role("viewer")
    shown = await viewer.get(f"{TASKS}/{created['id']}")
    assert shown.status_code == 200
    assert shown.json()["writable"] is False
    assert (await viewer.post(TASKS, {"key": "X", "title": "x"})).status_code == 403
    assert (
        await viewer.put(f"{TASKS}/{created['id']}", {"key": "T-1", "title": "x", "version": 1})
    ).status_code == 403
    assert (
        await viewer.delete(f"{TASKS}/{created['id']}", params={"version": 1})
    ).status_code == 403


async def test_a_deleted_task_leaves_an_audit_entry_with_the_client_name(api: Api) -> None:
    admin = await api.admin()
    created = await task(admin, "T-9")
    await admin.delete(f"{TASKS}/{created['id']}", params={"version": 1})
    entries = (await admin.get(AUDIT, params={"entityType": "task"})).json()["items"]
    assert [e["action"] for e in entries] == ["delete", "create"]
    assert entries[0]["clientName"] == "tests"
    assert entries[0]["before"]["key"] == "T-9"


async def test_a_task_without_effort_for_the_skill_cannot_be_assigned(api: Api) -> None:
    admin = await api.admin()
    backend = await skill(admin)
    ada = await member(admin, (await team(admin))["id"], "Ada", (backend["id"],))
    empty = await task(admin, "T-1")
    response = await assign(admin, empty["id"], backend["id"], ada["id"])
    assert response.status_code == 422
    assert response.json()["code"] == "ASSIGNMENT_SKILL_NOT_IN_TASK"
