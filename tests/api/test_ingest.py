"""The machine ingest API: idempotent upsert by source and external id (§7.1)."""

from __future__ import annotations

from typing import Any

from tests.api.support import Api, Caller
from tests.api.world import ABSENCES, MEMBERS, TASKS, audit_count, skill, team

from planner_api.auth.permissions import INGEST_WRITE

INGEST = "/api/v1/ingest"


async def pusher(api: Api) -> Caller:
    """A caller that may do nothing but ingest."""
    return await api.as_role(permissions=[INGEST_WRITE])


def tasks(source: str = "jira-eu", **extra: Any) -> dict[str, Any]:
    return {
        "source": source,
        "tasks": [
            {
                "externalId": "10001",
                "key": "WEB-1",
                "title": "Checkout",
                "url": "https://tracker.test/WEB-1",
                "efforts": [{"skill": "Backend", "personDays": 3}],
            },
            {"externalId": "10002", "key": "WEB-2", "title": "Search", "status": "done"},
        ],
        **extra,
    }


async def stored_tasks(admin: Caller) -> dict[str, dict[str, Any]]:
    found = (await admin.get(TASKS, params={"pageSize": 100})).json()["items"]
    return {t["key"]: t for t in found}


async def test_tasks_are_upserted_by_source_and_external_id(api: Api) -> None:
    admin = await api.admin()
    await skill(admin, "Backend")
    push = await pusher(api)

    first = await push.post(f"{INGEST}/tasks", tasks())
    assert first.status_code == 200, first.text
    assert (first.json()["created"], first.json()["updated"]) == (2, 0)
    again = await push.post(f"{INGEST}/tasks", tasks())
    assert (again.json()["created"], again.json()["updated"], again.json()["skipped"]) == (0, 0, 2)

    body = tasks()
    body["tasks"][0]["title"] = "Checkout v2"
    changed = await push.post(f"{INGEST}/tasks", body)
    assert (changed.json()["created"], changed.json()["updated"]) == (0, 1)

    found = await stored_tasks(admin)
    assert found["WEB-1"]["title"] == "Checkout v2"
    assert found["WEB-1"]["externalSource"] == "jira-eu"
    assert found["WEB-1"]["efforts"][0]["personDays"] == 3
    assert found["WEB-2"]["status"] == "done"

    # the same id from another source is another task
    other = await push.post(f"{INGEST}/tasks", tasks("jira-us"))
    assert other.json()["created"] == 2
    assert len(await stored_tasks(admin)) == 2  # keys are unique per source, listing keeps one

    locked = await admin.put(
        f"{TASKS}/{found['WEB-1']['id']}",
        {"version": found["WEB-1"]["version"], "key": "WEB-1", "title": "Mine", "status": "open"},
    )
    assert locked.status_code == 409 and locked.json()["code"] == "FIELD_LOCKED"


async def test_a_record_that_cannot_be_written_does_not_stop_the_others(api: Api) -> None:
    admin = await api.admin()
    await skill(admin, "Backend")
    push = await pusher(api)
    body = tasks()
    body["tasks"].append(
        {
            "externalId": "10003",
            "key": "WEB-3",
            "title": "Unknown skill",
            "efforts": [{"skill": "Cooking", "personDays": 1}],
        }
    )
    result = (await push.post(f"{INGEST}/tasks", body)).json()
    assert (result["created"], result["errors"]) == (2, 1)
    assert "Cooking" in result["messages"][0]["message"]

    made = (await push.post(f"{INGEST}/tasks", {**body, "createSkills": True})).json()
    assert made["errors"] == 0 and made["created"] == 1
    assert "Cooking" in {s["key"] for s in (await admin.get("/api/v1/skills")).json()["items"]}


async def test_a_dry_run_reports_without_saving_or_auditing(api: Api) -> None:
    admin = await api.admin()
    await skill(admin, "Backend")
    push = await pusher(api)
    before = await audit_count(admin)
    result = await push.post(f"{INGEST}/tasks", tasks(), params={"dryRun": "true"})
    assert result.status_code == 200, result.text
    assert result.json()["dryRun"] is True and result.json()["created"] == 2
    assert await stored_tasks(admin) == {}
    assert await audit_count(admin) == before


async def test_only_those_with_the_ingest_permission_may_push(api: Api) -> None:
    planner = await api.as_role("planner")
    refused = await planner.post(f"{INGEST}/tasks", tasks())
    assert refused.status_code == 403
    assert refused.json()["permission"] == INGEST_WRITE
    viewer = await api.as_role("viewer")
    assert (await viewer.post(f"{INGEST}/members", {"source": "x", "members": []})).status_code in (
        403,
        422,
    )


async def test_the_source_must_not_look_like_the_id_of_an_integration(api: Api) -> None:
    push = await pusher(api)
    reserved = await push.post(f"{INGEST}/tasks", tasks("8c1ec7e4-5e1d-4f6c-9d57-0d5b53b4d0a1"))
    assert reserved.status_code == 422
    assert reserved.json()["errors"][0]["field"] == "source"
    assert (await push.post(f"{INGEST}/tasks", tasks("not a valid name!"))).status_code == 422


async def test_members_are_pushed_with_their_team_and_idempotently(api: Api) -> None:
    admin = await api.admin()
    await team(admin, "Alpha")
    push = await pusher(api)
    body = {
        "source": "hr",
        "defaultTeam": "Alpha",
        "createTeams": True,
        "members": [
            {"externalId": "E1", "displayName": "Ada Lovelace", "email": "ada@example.test"},
            {"externalId": "E2", "displayName": "Bob Builder", "team": "Beta", "active": False},
        ],
    }
    first = (await push.post(f"{INGEST}/members", body)).json()
    assert (first["created"], first["errors"]) == (2, 0)
    second = (await push.post(f"{INGEST}/members", body)).json()
    assert (second["created"], second["updated"], second["skipped"]) == (0, 0, 2)
    found = {m["displayName"]: m for m in (await admin.get(MEMBERS)).json()["items"]}
    assert found["Ada Lovelace"]["teamName"] == "Alpha"
    assert found["Bob Builder"]["teamName"] == "Beta" and found["Bob Builder"]["active"] is False

    nobody = {"source": "hr", "members": [{"externalId": "E9", "displayName": "No Team"}]}
    refused = (await push.post(f"{INGEST}/members", nobody)).json()
    assert refused["created"] == 0 and refused["errors"] == 1


async def test_absences_are_pushed_by_member_and_type_key(api: Api) -> None:
    admin = await api.admin()
    t = await team(admin, "Alpha")
    await admin.create(
        MEMBERS,
        {"teamId": t["id"], "displayName": "Ada", "email": "ada@example.test", "skillIds": []},
    )
    push = await pusher(api)
    body = {
        "source": "hr",
        "absences": [
            {
                "externalId": "V1",
                "person": "ada@example.test",
                "type": "vacation",
                "startDate": "2030-01-08",
                "endDate": "2030-01-09",
            },
            {
                "externalId": "V2",
                "person": "ada@example.test",
                "type": "holiday",
                "startDate": "2030-02-08",
                "endDate": "2030-02-08",
            },
        ],
    }
    result = (await push.post(f"{INGEST}/absences", body)).json()
    assert (result["created"], result["errors"]) == (1, 1)
    assert "holiday" in result["messages"][0]["message"]
    again = (await push.post(f"{INGEST}/absences", body)).json()
    assert (again["created"], again["skipped"]) == (0, 1)
    assert (await admin.get(ABSENCES)).json()["total"] == 1


PLANNING = {
    "dueDate": "2026-12-18",
    "startDate": "2026-12-01",
    "originalEstimateHours": 16.5,
    "storyPoints": 5,
    "priority": "High",
    "assignee": "Alex Example",
    "reporter": "Sam Example",
}


async def test_planning_attributes_are_kept_and_shown_read_only(api: Api) -> None:
    admin = await api.admin()
    await skill(admin, "Backend")
    push = await pusher(api)
    body = tasks()
    body["tasks"][0].update(PLANNING)

    assert (await push.post(f"{INGEST}/tasks", body)).json()["created"] == 2
    found = await stored_tasks(admin)
    for name, value in PLANNING.items():
        assert found["WEB-1"][name] == value, name
    assert found["WEB-2"]["dueDate"] is None and found["WEB-2"]["storyPoints"] is None
    assert found["WEB-1"]["sourceName"] == "jira-eu"

    # the same values again change nothing, not even with the hours compared as decimals
    again = (await push.post(f"{INGEST}/tasks", body)).json()
    assert (again["created"], again["updated"], again["skipped"]) == (0, 0, 2)

    # what is left out stays, an explicit null empties
    body["tasks"][0] = {k: v for k, v in body["tasks"][0].items() if k not in PLANNING}
    body["tasks"][0]["priority"] = None
    changed = (await push.post(f"{INGEST}/tasks", body)).json()
    assert changed["updated"] == 1
    task = (await stored_tasks(admin))["WEB-1"]
    assert task["priority"] is None
    assert task["storyPoints"] == 5 and task["assignee"] == "Alex Example"

    # the attributes are not part of the task the planner edits
    update = await admin.put(
        f"{TASKS}/{task['id']}",
        {"version": task["version"], "key": "WEB-1", "title": "Checkout", "dueDate": "2027-01-01"},
    )
    assert update.status_code == 200, update.text
    assert update.json()["dueDate"] == "2026-12-18"


async def test_changes_of_planning_attributes_are_audited(api: Api) -> None:
    admin = await api.admin()
    await skill(admin, "Backend")
    push = await pusher(api)
    body = tasks()
    await push.post(f"{INGEST}/tasks", body)
    body["tasks"][0]["storyPoints"] = 8
    await push.post(f"{INGEST}/tasks", body)
    entries = (
        await admin.get("/api/v1/audit-log", params={"entityType": "task", "pageSize": 50})
    ).json()["items"]
    changed = [e for e in entries if e["action"] == "update"]
    assert changed and changed[0]["after"]["story_points"] == 8
    assert changed[0]["before"]["story_points"] is None


async def test_bad_planning_attributes_are_refused_by_the_schema(api: Api) -> None:
    push = await pusher(api)
    body = tasks()
    body["tasks"][0]["storyPoints"] = -1
    assert (await push.post(f"{INGEST}/tasks", body)).status_code == 422
    body = tasks()
    body["tasks"][0]["priority"] = "x" * 65
    assert (await push.post(f"{INGEST}/tasks", body)).status_code == 422


async def test_keys_may_contain_an_underscore(api: Api) -> None:
    admin = await api.admin()
    created = await admin.post(TASKS, {"key": "MY_PROJ-1", "title": "Underscore"})
    assert created.status_code == 201, created.text
